"""FastAPI application serving the PowerAtlas UI over two surfaces.

The htmx-driven pages and partials are ordinary request/response routes. The
ACP prototype adds a second surface — the ``/ws/acp`` WebSocket — which is
neither htmx nor request/response, and which ``same_origin_guard`` below
structurally cannot see. Its equivalent protections live in ``_ws_origin_ok``.
"""

import asyncio
import errno
import hashlib
import hmac
import html as html_mod
import ipaddress
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import (load_config, save_config, get_active_launch_profile,
                     LaunchProfile, ensure_remote_secret, load_remote_secret,
                     rotate_remote_secret, validate_remote_bind_address,
                     REMOTE_SECRET_MIN_LEN, REMOTE_SECRET_PATH)
from . import autostart, data, icons, launcher, notifications, presence
from .status_classifier import get_semantic_status, SemanticStatus

# `acp` is throwaway prototype code and is imported under a guard, unlike every
# module above it. Phase 3b adds `win32job`, `win32api`, `win32con` and `psutil`
# to it, and a dependency that is declared in pyproject.toml but absent from the
# running interpreter has already broken this project once. Unguarded at module
# scope, that ImportError takes the entire dashboard down; guarded, it costs the
# /acp page and nothing else. The failure is deliberately loud in both places
# that can see it: an exception traceback in the log at startup, and a banner on
# /acp itself instead of a page that connects to nothing.
try:
    from . import acp
    _ACP_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - prototype degradation path
    acp = None
    _ACP_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
    logging.getLogger("power_atlas.web").exception(
        "ACP prototype failed to import: /acp is disabled, the rest of the UI is "
        "unaffected")

try:
    import mistune
    # escape=True causes mistune to HTML-entity-encode raw HTML tags (e.g. <script> → &lt;script&gt;)
    # rather than passing them through. JS-URL hrefs (javascript:) are sanitized via
    # mistune's HTMLRenderer.safe_url() unconditionally. This makes output safe for | safe filter.
    #
    # `table` is a plugin because pipe tables are GFM and not CommonMark, and
    # mistune ships only the latter by default. Without it a table renders as
    # the literal pipes, collapsed onto one line by `.tail-md`'s
    # `white-space: normal` — and agent transcripts are full of tables.
    # Both sanitizing guarantees above still hold inside cells: `escape=` is
    # consumed by the `HTMLRenderer` this path uses, and the plugin adds one
    # attribute, `style="text-align:…"`, whose value comes from the `:---:`
    # delimiter and not from cell text.
    _md = mistune.create_markdown(escape=True, plugins=["table"])
except Exception:  # noqa: BLE001
    import html as _html
    import logging as _logging
    _logging.getLogger(__name__).warning("mistune not available — falling back to plain-text markdown rendering")
    def _md(text: str) -> str:  # type: ignore[misc]
        """Fallback: escape HTML and wrap in a paragraph."""
        return f"<p>{_html.escape(text)}</p>"

PROVIDER_COLORS = {
    "kiro-cli": "#7138cc",
    "claude-code": "#c2590f",
    "kiro-ide": "#8b5cf6",
}
PROVIDER_DISPLAY_NAMES = {
    "kiro-cli": "kiro-cli",
    "claude-code": "Claude Code",
    "kiro-ide": "Kiro IDE",
}
PROVIDER_BADGES = {
    "kiro-cli": "K",
    "claude-code": "C",
    "kiro-ide": "I",
}
_PROVIDER_BINARY_DISPLAY = {
    "kiro-cli": "kiro-cli chat",
    "claude-code": "claude",
    "kiro-ide": "kiro",
}


def _resolve_launch_cwd(workspace: str, config, provider: str = "") -> str:
    """Resolve working directory for a launch: workspace → per-provider default → global default → ~."""
    if workspace:
        return workspace
    if provider:
        per_provider = config.provider_settings.get(provider, {}).get("default_directory", "")
        if per_provider:
            return per_provider
    if config.default_directory:
        return config.default_directory
    return str(Path.home())


def _resolve_workspace_color(cwd: str, config) -> str:
    """Resolve accent color: workspace explicit > first tag color > empty (use provider gradient)."""
    from .config import get_workspace_settings
    ws = get_workspace_settings(config, cwd)
    if ws["color"]:
        return ws["color"]
    for tag in ws["tags"]:
        tag_color = config.tag_settings.get(tag, {}).get("color", "")
        if tag_color:
            return tag_color
    return ""


def _get_provider_color(provider: str, config) -> str:
    """Return user-configured color for a provider, falling back to PROVIDER_COLORS."""
    user_color = config.provider_settings.get(provider, {}).get("color", "")
    return user_color or PROVIDER_COLORS.get(provider, "#888")


def _enabled(config, prov: str) -> bool:
    """Return whether a provider is enabled in the config."""
    return config.provider_settings.get(prov, {}).get("enabled", True)


def _all_hover_launchers(config) -> list[dict]:
    """Return the ordered list of launcher entries to show in workspace card hover actions.

    Order: installed+enabled providers with show_in_workspace_hover=true (alphabetical),
    then custom launchers with use_selected_workspaces=true AND show_in_workspace_hover=true.
    """
    result: list[dict] = []
    # Built-in providers — installed (on disk) + enabled + show_in_workspace_hover (default true)
    try:
        installed = set(data.available_providers())
    except Exception:
        installed = set()
    for prov in sorted(installed):
        if not _enabled(config, prov):
            continue
        settings = config.provider_settings.get(prov, {})
        if not settings.get("show_in_workspace_hover", True):
            continue
        result.append({
            "id": f"provider--{prov}",
            "name": PROVIDER_DISPLAY_NAMES.get(prov, prov),
            "color": settings.get("color", "") or PROVIDER_COLORS.get(prov, "#888"),
            "is_provider": True,
        })
    # Custom launchers — use_selected_workspaces=true AND show_in_workspace_hover=true
    for launcher in config.custom_launchers:
        if not launcher.get("use_selected_workspaces"):
            continue
        if not launcher.get("show_in_workspace_hover"):
            continue
        result.append({
            "id": launcher["id"],
            "name": launcher.get("name", ""),
            "color": launcher.get("color", ""),
            "is_provider": False,
        })
    return result


def _time_bucket(iso_str: str) -> str:
    """Classify an ISO-8601 timestamp into today/yesterday/this_week/before."""
    if not iso_str:
        return "before"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        d = dt.astimezone().date()  # convert to local date
    except (ValueError, OSError):
        return "before"
    today = date.today()
    if d == today:
        return "today"
    if d == today - timedelta(days=1):
        return "yesterday"
    if d >= today - timedelta(days=today.weekday()):  # Monday of this week
        return "this_week"
    return "before"


# Session statuses that count as "live" (a process is running for them).
_LIVE_STATUSES = ("working", "waiting", "errored")

# Tracks whether the first render has completed (for notification initialization)
_first_render_done = False


# claude-code reports why a session is blocked, which separates "the agent is
# stuck behind an approval you have to grant" from "the agent asked you a
# question". The raw strings come from the provider; the categories are ours.
# An unmapped value is shown verbatim rather than dropped, so a new reason
# degrades to a slightly clumsy tooltip instead of a silent regression.
_WAITING_REASONS = {
    "permission prompt": ("approval", "needs your approval"),
    "sandbox request": ("approval", "needs your approval for a sandbox request"),
    "worker request": ("approval", "needs your approval for a worker request"),
    "input needed": ("question", "asked you a question"),
    "dialog open": ("other", "has a dialog open"),
}


def _waiting_detail(snapshot, session, provider: str, status: str) -> tuple[str, str]:
    """Return (category, human phrase) for a waiting session, else ("", "").

    Only claude-code reports this; kiro-cli's lock file carries no such field.
    """
    if status != "waiting":
        return "", ""
    reason = snapshot.waiting_reason(provider, session.session_id)
    if not reason:
        return "", ""
    known = _WAITING_REASONS.get(reason)
    if known:
        return known
    return "other", reason


def _map_reported_status(reported: str) -> str:
    """Map a provider's self-reported live state onto the semantic vocabulary.

    claude-code writes its live state to ~/.claude/sessions/<pid>.json;
    presence validates that file against the process before exposing it. Its
    four values map as:
      busy    - a turn is running          -> working
      shell   - a shell command is running -> working
      waiting - a dialog needs the human   -> waiting
      idle    - none of the above          -> no verdict
    "idle" is deliberately not mapped to "waiting": it covers finished,
    errored and never-started alike, so only the classifier can say which
    — and it is the sole source of "errored".

    Returns "" when the report carries no usable verdict (absent, "idle", or
    a value this build does not know), meaning "defer to the classifier".
    This is a pure mapping: how a non-empty verdict is weighed against the
    classifier belongs to ``_resolved_session_status``, which the row and the
    card both settle their sessions through.
    """
    if reported in ("busy", "shell"):
        return "working"
    if reported == "waiting":
        return "waiting"
    return ""


# Entrypoints that mean "started by a program, not by a person at a terminal".
# Taken verbatim from Claude Code's own set (2.1.221 carries
# `new Set(["sdk-cli","sdk-ts","sdk-py"])` and filters these out of `/resume`),
# so PowerAtlas and the provider agree on what counts as machine-driven rather
# than inventing a second definition that drifts.
#
# `cli` is the interactive terminal case. Deliberately NOT listed here:
# `mcp`, `local-agent`, `remote*`, `claude-vscode`, `claude-code-github-action`
# and `claude_in_slack` are all real entrypoint values this build knows, and
# each is arguably machine-driven too — but none has been observed on this
# machine, and Claude Code itself does not filter them. Add one when there is a
# sidecar to check it against, not before.
_SDK_ENTRYPOINTS = frozenset({"sdk-cli", "sdk-ts", "sdk-py"})

# Session kinds that are not a person at a prompt. `interactive` and "" are the
# ordinary cases and yield no badge.
_MACHINE_KINDS = frozenset({"bg", "daemon", "daemon-worker"})


def _session_origin(kind: str, entrypoint: str) -> str:
    """A short badge label for a session no human is sitting in front of, or "".

    **Both fields are consulted, and that is the whole point of this function.**
    Measured 2026-08-04 on Claude Code 2.1.221: `kind` is read straight out of
    the `CLAUDE_CODE_SESSION_KIND` environment variable, so it only reads
    non-`interactive` when a caller deliberately sets it. A plain `claude -p` —
    the shape every script, hook and CI job uses — reports `kind: interactive`
    with `entrypoint: sdk-cli`. Keying on `kind` alone, which is what this was
    originally scoped as, would therefore have missed the commonest
    machine-driven session entirely while looking like it worked.

    `kind` wins when both are informative, because it is the more specific
    claim: a `daemon` started through the SDK is better described as a daemon
    than as an SDK run.

    Returns "" for an ordinary interactive session, for kiro-cli (whose lock
    carries neither field), and for any value this build does not recognise —
    the same defer-rather-than-guess contract `_map_reported_status` uses.
    """
    if kind in _MACHINE_KINDS:
        return kind
    if entrypoint in _SDK_ENTRYPOINTS:
        return "sdk"
    return ""


def _row_origin(snapshot, session, provider: str) -> str:
    """``_session_origin`` for one row, read off the snapshot.

    Only ever non-empty for a **live** session: both fields come from the
    per-process sidecar, which `presence` discards once the process is gone. A
    historical row therefore carries no badge, which is the right scope — the
    confusion this addresses is a background session showing the same live dot
    as a real one, and a dead session shows no dot at all.
    """
    sid = getattr(session, "session_id", "")
    if not sid:
        return ""
    return _session_origin(snapshot.session_kind(provider, sid),
                           snapshot.session_entrypoint(provider, sid))


def _session_status(snapshot, session, provider: str,
                    notifications_enabled: bool = False, *,
                    notify: bool = True) -> str:
    """Return semantic status for a session.

    Detection gate: a session is live if either (a) its session_id is on a
    process cmdline (--resume-id), OR (b) a provider process is running in
    the session's cwd AND the session was recently updated (within 5 min).
    Gate (b) uses recency to avoid false-positive dots on old sessions that
    happen to share a workspace with a running process.

    Args:
        notify: When False, skip the notification side-effect entirely.
                Used by the lightweight status-polling endpoint to avoid toasts.
    """
    # 1. Check explicit live (session id on cmdline) — fast path
    is_explicitly_live = snapshot.is_live(provider, session.cwd, session.session_id)

    # 2. Check if a provider process runs in this workspace's cwd
    from .data import _normalize_path
    norm_cwd = _normalize_path(session.cwd)
    has_process = norm_cwd in snapshot.live_cwds({provider})

    # Resolve JSONL path (used for recency gate and fallback classification)
    from .status_classifier import _resolve_jsonl_path
    import os, time as _time
    jsonl_path = _resolve_jsonl_path(session.session_id, provider, session.cwd) if (has_process or is_explicitly_live) else None

    # 3. Recency gate: only classify via cwd if session's JSONL was written recently
    #    (avoids false-positive dots on old sessions sharing the same workspace)
    #    Uses JSONL file mtime (actual agent activity) not metadata updated_at
    #    (which only reflects user interaction timestamps).
    if has_process and not is_explicitly_live:
        if jsonl_path is not None:
            try:
                mtime_age = _time.time() - os.path.getmtime(jsonl_path)
                if mtime_age > 300:  # 5 minutes since last JSONL write
                    has_process = False
            except OSError:
                has_process = False
        else:
            # No JSONL file found — can't classify, treat as closed
            has_process = False

    # 4. Here a non-empty report wins outright, including over a richer-looking
    #    classifier verdict: it is first-hand and current, while the classifier
    #    reads a transcript tail that lags an in-flight turn. See
    #    _map_reported_status for the mapping and for why "idle" yields nothing.
    reported = _map_reported_status(
        snapshot.reported_status(provider, session.session_id))

    if not is_explicitly_live and not has_process:
        status_value = "closed"
    elif reported:
        status_value = reported
    elif (semantic := get_semantic_status(session.session_id, provider, session.cwd)) is not None:
        status_value = semantic.value
    else:
        # Process running but can't classify from JSONL tail.
        # A running process = working. The classifier returns None when the
        # tail is unparseable or mid-write — not evidence of idle state.
        # "Waiting" only comes from positive classifier identification.
        status_value = "working"

    # Notify on transition (skip when caller opts out, e.g. status-poll endpoint)
    if notify:
        notifications.check_and_notify(
            session.session_id, session.title or "untitled",
            status_value, notifications_enabled
        )
    return status_value


# Priority ordering for workspace-level status aggregation
_STATUS_PRIORITY = {"errored": 3, "waiting": 2, "working": 1, "closed": 0}


def _raise_status(best: str, candidate: str) -> str:
    """Return the higher-priority of two statuses; "" candidates are ignored."""
    if not candidate:
        return best
    if _STATUS_PRIORITY.get(candidate, 0) > _STATUS_PRIORITY.get(best, 0):
        return candidate
    return best


def _resolved_session_status(snapshot, provider: str, session_id: str,
                             semantic: SemanticStatus | None) -> str:
    """Settle one live session's status, the way its own row settles it.

    Same precedence as ``_session_status``: a first-hand, current report beats a
    transcript tail that lags an in-flight turn. The one exception is "errored",
    which only the classifier can report at all, so an errored tail is kept even
    against a "busy" — the signal the card exists to surface.

    Callers pass the classifier verdict they have already read, so settling a
    session here costs no extra tail parse.
    """
    if semantic is SemanticStatus.ERRORED:
        return "errored"
    reported = _map_reported_status(snapshot.reported_status(provider, session_id))
    if reported:
        return reported
    if semantic is not None:
        return semantic.value
    # A process is running and nothing could classify it — not evidence of idle.
    return "working"


def _workspace_status(snapshot, cwd: str,
                      providers: set[str] | None) -> str:
    """Aggregate status for a workspace card — highest-priority session status wins.

    Priority: errored > waiting > working > closed (no dot).
    Falls back to classifying the most recently updated session when no
    explicit --resume-id sessions are tracked for this cwd.

    Each session is settled by ``_resolved_session_status`` first and only then
    aggregated. Folding the raw report and the raw classifier straight into the
    aggregate instead let a lagging tail outrank the provider's own "busy", so a
    card read "waiting" above a row the very same signals had already settled as
    "working". The card can still outrank a row, but only on the strength of a
    different session, or of the errored verdict the row honours too.
    """
    from .data import _normalize_path
    if _normalize_path(cwd) not in snapshot.live_cwds(providers):
        return "closed"
    # Check semantic status for recent sessions in this workspace
    best = "working"  # at minimum, a process is running
    found_any = False
    # Try to get semantic classification for sessions in this cwd
    for prov in (providers or {"kiro-cli", "claude-code"}):
        sids = snapshot.live_session_ids_for_cwd(prov, cwd)
        for sid in sids:
            semantic = get_semantic_status(sid, prov, cwd)
            if semantic is not None:
                # Only a classification clears the fallback — a report is not
                # one, and skipping the scan on the strength of one would hide
                # an "errored" session elsewhere in the workspace.
                found_any = True
            best = _raise_status(
                best, _resolved_session_status(snapshot, prov, sid, semantic))
    # Fallback: no explicit session IDs (chat -a without --resume-id).
    # Classify all recently active sessions in this workspace, take highest priority.
    if not found_any:
        from . import data
        from .status_classifier import _resolve_jsonl_path
        import os, time as _time
        for prov in (providers or {"kiro-cli", "claude-code"}):
            sessions = data.get_sessions(cwd=cwd, provider=prov)
            checked = 0
            for recent in sessions:
                if checked >= 10:  # cap: only check the 10 most recent
                    break
                checked += 1
                jsonl_path = _resolve_jsonl_path(recent.session_id, prov, cwd)
                if jsonl_path is None:
                    continue
                try:
                    mtime_age = _time.time() - os.path.getmtime(jsonl_path)
                except OSError:
                    continue
                if mtime_age > 300:  # skip stale sessions
                    continue
                # Narrow but real: presence records a report for any validated
                # sidecar, but a session only reaches sid_to_cwd through its
                # sidecar's own "cwd" field or through the process pass, which
                # maps proc.cwd() when the session id is on argv. A claude-code
                # session with neither is invisible to the loop above and only
                # reachable here — kiro-cli never reports a status at all.
                best = _raise_status(best, _resolved_session_status(
                    snapshot, prov, recent.session_id,
                    get_semantic_status(recent.session_id, prov, cwd)))
    return best


def _status_matches(status_filter: str, status: str) -> bool:
    """True if a computed status passes the requested filter value."""
    if not status_filter or status_filter == "all":
        return True
    if status_filter == "live":
        return status in _LIVE_STATUSES
    return status == status_filter


def _group_workspaces(workspace_data: list[tuple[str, int, str, str]], config) -> list[dict]:
    """Group flat (cwd, count, updated_at, provider) rows into one entry per workspace.

    Returns list of dicts sorted by latest_updated desc:
    {
        "cwd": str,              # original (first-seen) cwd path
        "folder_name": str,      # Path(cwd).name or cwd
        "providers": [{"name": str, "color": str, "count": int, "updated_at": str}],
        "total_count": int,      # sum of all provider counts
        "latest_updated": str,   # max updated_at across providers
    }
    """
    from .data import _normalize_path

    groups: dict[str, dict] = {}  # norm_cwd -> group dict
    original_cwds: dict[str, str] = {}  # norm -> original (first seen)

    for cwd, count, updated_at, prov_name in workspace_data:
        norm = _normalize_path(cwd)
        if norm not in groups:
            groups[norm] = {"providers": [], "total_count": 0, "latest_updated": "", "_seen_providers": set()}
            original_cwds[norm] = cwd
        g = groups[norm]
        if prov_name in g["_seen_providers"]:
            # Duplicate provider for same workspace — merge counts, keep latest updated_at
            for p in g["providers"]:
                if p["name"] == prov_name:
                    p["count"] += count
                    if updated_at and updated_at > p["updated_at"]:
                        p["updated_at"] = updated_at
                    break
        else:
            g["_seen_providers"].add(prov_name)
            g["providers"].append({
                "name": prov_name,
                "display": PROVIDER_DISPLAY_NAMES.get(prov_name, prov_name),
                "color": _get_provider_color(prov_name, config),
                "count": count,
                "updated_at": updated_at,
            })
        g["total_count"] += count
        if updated_at and updated_at > g["latest_updated"]:
            g["latest_updated"] = updated_at

    result = []
    for norm, g in groups.items():
        cwd = original_cwds[norm]
        # Sort providers alphabetically for consistent gradient ordering
        g["providers"].sort(key=lambda p: p["name"])
        result.append({
            "cwd": cwd,
            "folder_name": Path(cwd).name or cwd,
            "providers": g["providers"],
            "total_count": g["total_count"],
            "latest_updated": g["latest_updated"],
        })
    result.sort(key=lambda x: x["latest_updated"], reverse=True)
    return result


_PKG_DIR = Path(__file__).parent
_TEMPLATES_DIR = _PKG_DIR / "templates"
_STATIC_DIR = _PKG_DIR / "static"
log = logging.getLogger("power_atlas.web")


@asynccontextmanager
async def lifespan(app_instance):
    task = asyncio.create_task(_background_refresh())
    # Guarded exactly as the teardown below is. An `acp` import failure is
    # designed to degrade to "/acp disabled" (see the import at the top of this
    # module); an unguarded start here would promote it to "the application
    # will not start".
    # Connect the two modules that deliberately do not know about each other.
    # `acp` states an isolation boundary in its own header and `presence` runs
    # on worker threads that D9 keeps away from loop-owned state, so neither
    # imports the other; this module already imports both, which makes it the
    # only place the wire can be run. Closes D32 — see `presence._acp_live`.
    #
    # Before `start_sweeper`, and published once immediately, so the very first
    # `_scan` after a restart sees an empty live set rather than no answer:
    # every kiro lock on disk at that moment is by definition an orphan of a
    # previous process, and the ones naming a pid we have since reused are the
    # case this exists to reject.
    if acp is not None:
        acp.set_sessions_changed_hook(presence.publish_acp_sessions)
    sweeper = acp.start_sweeper() if acp is not None else None
    try:
        yield
    finally:
        task.cancel()
        if sweeper is not None:
            sweeper.cancel()
        try:
            # One gather for both, with `return_exceptions=True`, and inside
            # this block rather than as two bare awaits. That is what makes the
            # nested teardown below unconditional: `gather` in this mode cannot
            # propagate whatever either task raised on its way out, so there is
            # no exception here that could skip `acp.shutdown()`.
            await asyncio.gather(
                *(t for t in (task, sweeper) if t is not None),
                return_exceptions=True)
        finally:
            # Nested, so that the ACP teardown is not conditional on how the
            # gather above ends — and both tasks are cancelled *and* awaited
            # before it runs, because `acp.shutdown()` is synchronous and a
            # sweeper still parked inside `close_session` when the agent is
            # killed would be a close racing its own teardown.
            #
            # ACP teardown is the *fast* path only. The Windows job object that
            # `acp` assigns the agent to is what actually guarantees no orphans:
            # `--stop`/`--restart` hard-kill this process with
            # `TerminateProcess` and never run `lifespan` at all, and neither
            # does a crash or Task Manager. This makes the tray route prompt;
            # the job makes every route certain. It kills, then waits up to
            # `acp.KILL_WAIT_SECONDS` for the tree to actually go — typically
            # ~0.3-0.5 s, but a worst case of ~5.2 s that can outlast
            # `__main__.py`'s 5 s server-thread join. Benign, and only because
            # the job object then finishes the job when `os._exit(0)` closes
            # its handle; see `acp.KILL_WAIT_SECONDS` for the arithmetic.
            if acp is not None:
                try:
                    acp.shutdown()
                except Exception:
                    log.exception("ACP teardown failed")


async def _background_refresh():
    while True:
        await asyncio.sleep(30)
        try:
            await asyncio.to_thread(data.refresh_stale_entries)
        except Exception:
            log.exception("Background refresh failed")


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# Loopback host names the server is legitimately reached by. Validating the Host
# header against this allowlist blocks DNS rebinding: a rebinding attack arrives
# with the attacker's Host (e.g. evil.com), which would otherwise both make the
# reflected same-origin check below trust the attacker's origin and hand the
# attacker's page readable responses from every unguarded route.
#
# Only genuine loopback names belong here. A single-label name (no dot) is *not*
# safe to allowlist merely because it has no public DNS record: whoever wins
# LLMNR, NBT-NS or mDNS on the local network answers for it, as does anyone
# controlling a DNS search suffix — so it is a rebinding target like any other.
# Tests point their client at a loopback base URL rather than widen this set.
#
# Rebindable, by `set_remote_host` at startup and by nothing else. The two
# obvious ways to teach this set the configured NetBird IP are both traps: a
# per-request `load_config()` puts an uncached whole-file TOML parse on the hot
# path (the stall D15 forbids in `at_capacity`), and an import-time read makes
# every host test in this suite depend on the developer's real config.toml.
# A startup setter mirrors how `acp.apply_config` already injects its tunables.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_ALLOWED_HOSTS = _LOOPBACK_HOSTS

# A Host may carry exactly one suffix, a decimal port. Five digits covers every
# port; the value is never used as a number, only proven to be one.
_PORT_RE = re.compile(r"[0-9]{1,5}")

# The one GET on this app that changes state: rendering it starts the chain
# that spawns the agent. Named once so the route and the middleware guarding it
# cannot drift apart.
_ACP_PATH = "/acp"

# The secret-exchange surface: one path, GET renders the form and POST trades
# the device secret for the cookie. Named once because three things must agree
# about it — the routes, the remote path allowlist, and the cookie exemption.
_REMOTE_AUTH_PATH = "/remote-auth"

# The session browser's listing route. Defined up here, far from its own route,
# for one mechanical reason: `_REMOTE_ALLOWED_PATHS` below now names it, and a
# module-level dict is built at import time, so the name has to exist before
# that literal is evaluated. The route, its page sizes and its rationale stay
# together further down.
_ACP_LISTING_PATH = "/api/acp/sessions"

# The create picker's workspace list, up here for exactly the same mechanical
# reason as its neighbour above — `_REMOTE_ALLOWED_PATHS` names it, and that
# dict is built at import time. Its rationale, and why it is on the remote
# surface while deletion is not, stay with the route further down.
_ACP_WORKSPACES_PATH = "/api/acp/workspaces"


def set_remote_host(address: str) -> None:
    """Teach `_ALLOWED_HOSTS` the one non-loopback address we bind. Startup only.

    Called from `__main__` **after** the remote socket has actually bound, so
    the allowlist never widens for an address nothing is listening on.

    Re-validates rather than trusting the caller. This function is the single
    point where the DNS-rebinding defence can be weakened, and `""` restores
    loopback-only — which is what makes it safe for a test to set and unset.
    """
    global _ALLOWED_HOSTS
    value = (address or "").strip()
    if not value:
        _ALLOWED_HOSTS = _LOOPBACK_HOSTS
        return
    # `port` is irrelevant to the host allowlist, so pass a non-zero placeholder
    # rather than re-deriving it; SC-3b is enforced on the bind path.
    reason = validate_remote_bind_address(value, 1)
    if reason:
        log.error("remote host %r not added to the Host allowlist (%s)",
                  value, reason)
        _ALLOWED_HOSTS = _LOOPBACK_HOSTS
        return
    # Stored unbracketed and lowercase: `_host_allowed` strips brackets and
    # lowercases the name before the membership test, so any other form binds a
    # socket that no Host header can ever match.
    _ALLOWED_HOSTS = _LOOPBACK_HOSTS | {value.lower()}


def _host_allowed(raw_host: str | None) -> bool:
    """Return True when a raw ``Host`` header names a loopback address.

    Takes the header as sent rather than ``request.url.hostname``, because
    Starlette's ``URL`` is not a safe input to an allowlist decision:

    * It **substitutes**. A ``Host`` that fails Starlette's ``_HOST_RE``
      (``^([a-z0-9.-]+|\\[[a-f0-9]*:[a-f0-9.:]+\\])(?::[0-9]+)?$`` — note that
      underscores are absent from that character class) is discarded and the URL
      is rebuilt from ``scope["server"]``. ``Host: a_b.evil.com`` therefore
      reports a hostname of ``127.0.0.1`` and passes the allowlist, handing a
      rebound page every response body this app has — including the ACP token
      and ``custom_launchers``, whose ``env`` holds cleartext credentials.
      Browsers do send hostnames containing underscores, so this is reachable.
    * It **raises**. ``hostname`` runs ``urlsplit``, which throws ``ValueError``
      on an unmatched bracket (``[evil``, ``[::1``), turning a rejection into a
      500 any unauthenticated caller can drive on every route.

    So the parsing is done here, and every failure mode is a rejection: absent,
    empty, userinfo-bearing, bracket-mangled, non-numeric port. Nothing in this
    function can raise, because an unparseable Host must cost the caller a 403
    and not a 500. ``_request_host_allowed`` wraps it for a live request.
    """
    if not raw_host:
        return False
    host = raw_host.strip()
    # Userinfo is the trap ``urlsplit`` walks into: it keeps only what follows
    # the *last* "@", so ``evil.com@127.0.0.1`` reads as loopback. Nothing below
    # strips anything, so the comparison at the end already rejects these — this
    # line is deliberately redundant and no test can kill it. It stays because
    # that redundancy is load-bearing against a future edit: it is the only part
    # of this function that does not assume ``_ALLOWED_HOSTS`` is compared by
    # exact equality, and it fails closed if that ever stops being true.
    if any(ch in host for ch in "@/\\?#"):
        return False
    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return False
        name, remainder = host[1:end], host[end + 1:]
    else:
        name, colon, port = host.partition(":")
        remainder = colon + port
    # ``remainder`` is empty or ":<port>". Anything else — a second colon, a
    # hostname smuggled into the port (``127.0.0.1:4915.evil.com``), bytes
    # trailing the closing bracket (``[::1]extra``) — is malformed, and a
    # malformed Host is rejected rather than trimmed down to something valid.
    if remainder and not (
            remainder.startswith(":") and _PORT_RE.fullmatch(remainder[1:])):
        return False
    return name.lower() in _ALLOWED_HOSTS


def _request_host_allowed(request: Request) -> bool:
    """``_host_allowed`` for a live request: exactly one Host header, or refuse.

    ``getlist`` rather than ``get`` because both of the counts it rules out are
    real. **Zero**: HTTP/1.0 permits omitting Host, and with none sent there is
    nothing left for Starlette's URL but the ``scope["server"]`` fallback, so
    ``url.hostname`` answered ``127.0.0.1`` by construction — an absent Host was
    a loopback Host. **Two or more**: a request-smuggling shape and never a
    browser, since which copy is authoritative differs between hops, and
    ``get`` would silently answer with the first.
    """
    hosts = request.headers.getlist("host")
    if len(hosts) != 1:
        return False
    return _host_allowed(hosts[0])


def _origin_or_referer_ok(request: Request, *, allow_missing: bool) -> bool:
    """Whether a request's declared origin is this app's own.

    ``allow_missing`` is the only difference between the two callers. A POST
    with neither header is refused: every POST here comes from the dashboard's
    own script, which always sends one. A navigation with neither is the
    address bar or a bookmark, which is how ``/acp`` is legitimately opened
    from cold.
    """
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    if origin == "null":
        return False
    if not origin and not referer:
        return allow_missing
    expected_origin = f"{request.url.scheme}://{request.url.netloc}"
    if origin:
        return origin == expected_origin
    parsed = urlparse(referer)
    return f"{parsed.scheme}://{parsed.netloc}" == expected_origin


def _acp_navigation_ok(request: Request) -> bool:
    """Whether a ``GET /acp`` may proceed. Modelled on what the real flows send.

    Copying the POST rule verbatim would break the page. The flows are:

    * the dashboard's row action (``location.href = '/acp?sid=…'``) — a
      same-origin top-level navigation, which sends **no** ``Origin`` at all
      (browsers only attach it to navigations that are not GET/HEAD) and a
      same-origin ``Referer``;
    * a bookmark or a typed address — **neither** header;
    * the page's own ``fetch`` of itself in ``diagnoseRejectedHandshake`` —
      same-origin ``Referer``, no ``Origin``.

    So "missing Origin" and even "missing both" have to pass, and that is what
    ``Sec-Fetch-Site`` is consulted for. It is set by the browser and cannot be
    influenced by page content — unlike ``Referer``, which an attacker page
    strips with one ``Referrer-Policy`` — and it separates the two cases the
    other headers cannot: ``none`` is a user-initiated load (bookmark, address
    bar), while a cross-site navigation says ``cross-site`` however the
    referrer was suppressed. Requests without it (a non-browser client) fall
    back to the Origin/Referer rule, which is all this route had before.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        return False
    return _origin_or_referer_ok(request, allow_missing=True)


@app.middleware("http")
async def same_origin_guard(request: Request, call_next):
    """Reject non-loopback Hosts on every request, CSRF-suspect POSTs, and
    cross-origin navigations to ``/acp``.

    The three halves have different scopes on purpose. The Host allowlist is a
    DNS-rebinding defense and applies to *all* methods: a rebound page is
    same-origin with whatever it fetches, so an unguarded GET hands it the
    response body — workspace paths, session titles, settings. The
    Origin/Referer checks are CSRF defense and stay POST-only, because browsers
    omit Origin on ordinary navigations and every other GET here only reads.

    ``GET /acp`` is the exception, and the reason the POST-only scope could no
    longer be justified as "a GET here is never state-changing": rendering that
    page seeds a socket that sends ``subscribe``, is answered
    ``unknown_session``, and sends ``load`` — which reaches ``ensure_started``
    and spawns ``kiro-cli acp -a``. A cross-origin top-level navigation was
    therefore enough to start a trust-all-tools agent with no user gesture.
    """
    # `_ALLOWED_HOSTS` is loopback-only by default and gains **at most one**
    # further name — the configured remote bind address, taught to it by
    # `set_remote_host` at startup after that socket actually bound. So a Host
    # outside the set still cannot arise legitimately, but the reason is no
    # longer "nothing on the network can reach this app": with the remote bind
    # enabled, every peer on the NetBird account can, and this check is what
    # keeps a rebound page from being same-origin with the responses.
    if not _request_host_allowed(request):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    if request.method == "POST":
        if not _origin_or_referer_ok(request, allow_missing=False):
            return JSONResponse({"error": "Forbidden"}, status_code=403)
    elif request.url.path == _ACP_PATH and not _acp_navigation_ok(request):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    return await call_next(request)


# --- Remote access: the whole authorization boundary ---------------------
#
# D3 designed two independent layers — a NetBird access-control policy plus
# this device secret. Phase 0 measured that the policy layer does not exist:
# all 17 peers on the account sit in this host's network map, so reachability
# is not authorization and the cookie below is the ONLY control. D33 records
# the user's decision to ship on that basis, with the consequence stated: what
# sits behind this code is `kiro-cli acp -a`, i.e. arbitrary command execution
# as the user. Every check here is load-bearing on its own.

# Loaded once at startup by `set_remote_secret`, from a file, never from
# `config.toml` (D8). Empty means "no usable secret", which is the state a
# missing, unreadable, empty or truncated file collapses to — and with it
# empty, `_cookie_ok` returns False for every cookie ever presented.
_REMOTE_SECRET = ""

_DEVICE_COOKIE_NAME = "pa_device"

# Bounded charset and length. This value is client-supplied, is echoed into a
# WARNING line, and is concatenated into a cookie: a ";" or "," is a cookie
# attribute injection, a CR-LF is a header injection, and a newline is log
# injection. Excluding "." also makes the three-field cookie unambiguous to
# split.
_DEVICE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

# `str.isdigit()` is True for non-ASCII decimal digits and `int()` accepts
# them, so the timestamp field is matched against ASCII digits explicitly —
# the same trap `_PORT_RE` above exists for.
_ISSUED_AT_RE = re.compile(r"[0-9]{1,12}")

# 90 days. `issued_at` is what gives the cookie an expiry with **no server-side
# store**, which is D24's whole premise: a stored token dies with the process
# and the phone would re-enter the secret after every restart. Without the
# timestamp, "long-lived" means eternal, because D24 also gives up per-device
# revocation.
REMOTE_COOKIE_MAX_AGE_SECONDS = 90 * 24 * 3600

# A cookie stamped in the future is a clock disagreement, not a forgery — but
# an unbounded future stamp is an unbounded lifetime, so it is bounded too.
_COOKIE_FUTURE_SKEW_SECONDS = 300


def set_remote_secret(secret: str) -> None:
    """Load the device secret. Startup only, mirroring `set_remote_host`.

    Called from `__main__` only after the remote socket has actually bound, so
    an instance with no remote listener also has no secret in memory. Passing
    `""` restores the fail-closed state, which is what lets a test set and
    unset it without leaving the process authenticating.
    """
    global _REMOTE_SECRET
    value = (secret or "").strip()
    if value and len(value) < REMOTE_SECRET_MIN_LEN:
        log.error("remote secret is shorter than %d characters; refusing every "
                  "remote request", REMOTE_SECRET_MIN_LEN)
        value = ""
    _REMOTE_SECRET = value


def _device_cookie_sig(secret: str, device_id: str, issued_at: str) -> str:
    """HMAC-SHA256 over `(device_id, issued_at)`, keyed by the file secret."""
    return hmac.new(secret.encode("utf-8"),
                    f"{device_id}.{issued_at}".encode("utf-8"),
                    hashlib.sha256).hexdigest()


def make_device_cookie(device_id: str, issued_at: int | None = None) -> str:
    """Mint a cookie value, or `""` when there is no usable secret."""
    if not _REMOTE_SECRET:
        return ""
    if not _DEVICE_ID_RE.fullmatch(device_id):
        return ""
    stamp = str(int(time.time()) if issued_at is None else issued_at)
    return f"{device_id}.{stamp}.{_device_cookie_sig(_REMOTE_SECRET, device_id, stamp)}"


def _scope_cookie(scope, name: str) -> str:
    """Read one cookie out of a raw ASGI scope without raising.

    Hand-parsed rather than routed through `http.cookies`, which is lenient in
    ways an authorization decision must not inherit and which this guard cannot
    afford to have raise: it runs on the `websocket` scope too, where an
    exception is not a 500 but a broken handshake on the guarded path.
    """
    for key, value in scope.get("headers") or ():
        if key.lower() != b"cookie":
            continue
        try:
            raw = value.decode("latin-1")
        except Exception:  # pragma: no cover - bytes always decode as latin-1
            continue
        for part in raw.split(";"):
            candidate, sep, val = part.partition("=")
            if sep and candidate.strip() == name:
                return val.strip()
    return ""


def _cookie_ok(scope) -> bool:
    """Whether a scope carries a valid, unexpired device cookie.

    Fails closed on every path: no secret, no cookie, malformed cookie, a
    device id outside the bounded charset, a non-ASCII-digit timestamp, an age
    past the ceiling, a signature that does not verify. Nothing here raises,
    because a raise on this path is a 500 an unauthenticated peer can drive.
    """
    secret = _REMOTE_SECRET
    if not secret or len(secret) < REMOTE_SECRET_MIN_LEN:
        return False
    raw = _scope_cookie(scope, _DEVICE_COOKIE_NAME)
    # 64 (id) + 12 (stamp) + 64 (hex digest) + 2 separators = 142.
    if not raw or len(raw) > 160:
        return False
    device_id, sep_a, rest = raw.partition(".")
    issued_at, sep_b, sig = rest.partition(".")
    if not sep_a or not sep_b or not sig:
        return False
    if not _DEVICE_ID_RE.fullmatch(device_id):
        return False
    if not _ISSUED_AT_RE.fullmatch(issued_at):
        return False
    now = int(time.time())
    issued = int(issued_at)
    if issued > now + _COOKIE_FUTURE_SKEW_SECONDS:
        return False
    if now - issued > REMOTE_COOKIE_MAX_AGE_SECONDS:
        return False
    # UTF-8 bytes, not str: `compare_digest` raises `TypeError` for a `str`
    # holding non-ASCII, and a cookie is entirely attacker-chosen — the same
    # lesson `_acp_token_ok` already encodes.
    return secrets.compare_digest(
        sig.encode("utf-8", "replace"),
        _device_cookie_sig(secret, device_id, issued_at).encode("utf-8"))


def _is_remote_peer(peer: str | None) -> bool:
    """Is the transport-level peer address non-loopback?

    Defined once rather than left to a call site, because the whole model
    collapses to whichever predicate someone writes. `peer != bind_address` and
    `peer in an allowlist` are both wrong; only "is it loopback?" is right, and
    unparseable or absent means remote.

    The input is `scope["client"]`, set by the transport, **never the `Host`
    header** (D26): `Host` is attacker-controlled, so a NetBird peer sending
    `Host: 127.0.0.1:4915` would otherwise read as local and skip both the path
    allowlist and the cookie.
    """
    if not peer:
        return True
    try:
        return not ipaddress.ip_address(peer).is_loopback
    except ValueError:
        return True


# Default-deny (D6). A denylist over ~40 routes leaks by default on the next
# route added; this makes every new route loopback-only until someone puts it
# here deliberately. `_ACP_LISTING_PATH` (Phase 4) was held out of this map
# until the route existed and had a consumer, so that registering a path could
# not make it remotely reachable before anything read it; Phase 5b is that
# integration step, because the session rail is the whole reason a phone loads
# `/acp` and a rail that 403s from the phone leaves the page unusable there.
# Read-only, no `env`, no launcher data and no action affordances (D18), so what
# it widens is a listing of workspace paths and session titles — weighed and
# accepted in the route's own docstring, which notes `title` may carry prompt
# text. Still behind the device cookie: the allowlist and the cookie are two
# conditions, not alternatives.
# Scope-typed, not merely path-keyed. `/ws/acp` is the only websocket entry;
# everything else is HTTP. A path-only allowlist admitted `ws://<ip>/static/x`
# on the cookie alone, and `StaticFiles.__call__` opens with
# `assert scope["type"] == "http"` — so the guard's own mount entry produced an
# unhandled `AssertionError` on a websocket scope. Post-authentication, so it
# was noise rather than a boundary failure, but the docstring below names
# `/static` + websocket as the reason this function exists at all.
#
# The `/static` mount is an entry here rather than a literal inside the matcher
# below, and that placement is load-bearing rather than tidy: it makes this map
# the *whole* statement of what a remote peer may reach, with nothing left in
# the matcher able to admit a path on its own. An empty map is therefore an
# empty surface — the single property the runtime stop switch below is built on.
_REMOTE_STATIC_MOUNT = "/static"

_REMOTE_ALLOWED_PATHS: dict[str, str] = {
    _ACP_PATH: "http",
    "/ws/acp": "websocket",
    _REMOTE_AUTH_PATH: "http",
    _ACP_LISTING_PATH: "http",
    # The create picker's workspace list. Here because creating a session is
    # already a remote capability — `session/new` rides the allowlisted
    # `/ws/acp` — so a picker that could not list workspaces from a phone would
    # remove something that works today. It carries workspace paths and session
    # *counts* and no session content, i.e. a strict subset of what
    # `_ACP_LISTING_PATH` above already discloses. Deletion is deliberately not
    # here; see `_ACP_DELETE_PATH`.
    _ACP_WORKSPACES_PATH: "http",
    _REMOTE_STATIC_MOUNT: "http",
}

# --- The runtime stop switch ---------------------------------------------
#
# "Refuse every remote request now", chosen deliberately over closing the
# socket: the port stays bound until this process restarts, and what stops is
# that anything arriving on it is refused.
#
# Written as a *surface*, not as a flag, because the failure direction is the
# whole design. `if _remote_stopped: refuse` reads correctly and fails the
# wrong way: that flag becomes the only thing standing between a remote peer
# and the app, so an unset, inverted, shadowed or half-applied flag leaves
# remote access live while the user believes it is off — fail-open, silently.
#
# Here there is no second condition to get wrong. Stopping installs the empty
# map into the variable `_remote_path_allowed` already reads, and that lookup
# is default-deny (D6). Every way this can break — `_remote_surface` never
# assigned the live map, cleared, rebound to `None` by a bad edit, set to
# something that is not a mapping — answers "no path is allowed", which is the
# guard's existing refusal. A bug disables remote access; it cannot disable the
# guard.
#
# It is also the only state there is: `remote_stopped()` is *derived* from this
# same variable rather than tracked beside it, so the panel cannot report
# "stopped" while the guard is admitting traffic.
#
# Never persisted. `remote_bind_address` in `config.toml` is what a restart
# reads and this switch deliberately does not touch it: a kill switch that
# rewrites the configuration is one the user has to undo twice, and the user
# asked for a runtime switch, not a configuration change.
_REMOTE_SURFACE_STOPPED: dict[str, str] = {}

_remote_surface: dict[str, str] = _REMOTE_ALLOWED_PATHS


def _live_remote_surface() -> dict[str, str]:
    """The surface in force, or the empty one when it is not a mapping.

    The `isinstance` is not defensive noise. This variable is the entirety of
    the kill switch, and the one outcome that must be impossible is a corrupt
    value raising out of `_remote_path_allowed` — which runs on the `websocket`
    scope too, where an exception is a broken handshake rather than a 500, and
    would have to be caught somewhere that could as easily let the request pass.
    """
    surface = _remote_surface
    return surface if isinstance(surface, dict) else _REMOTE_SURFACE_STOPPED


def set_remote_stopped(stopped: object) -> None:
    """Stop or resume the remote surface, in this process only.

    Resuming takes an exact `False` and nothing else. Every other value —
    `None`, `0`, `""`, `"false"`, a field the request body never carried —
    stops, because the caller is an HTTP route and an argument it could not
    make sense of must not be the one that re-opens the boundary.
    """
    global _remote_surface
    _remote_surface = (_REMOTE_ALLOWED_PATHS if stopped is False
                       else _REMOTE_SURFACE_STOPPED)


def remote_stopped() -> bool:
    """Whether the surface in force admits nothing at all. Derived, not stored.

    Reading the same variable the guard reads is what keeps the reported state
    and the enforced state from drifting apart.
    """
    return not _live_remote_surface()


# Path-only, so the exchange route itself must reject methods other than
# GET/POST — which FastAPI does by registering only those two.
_COOKIE_EXEMPT = frozenset({_REMOTE_AUTH_PATH})

_FORBIDDEN_BODY = b'{"error":"Forbidden"}'


def _remote_path_allowed(path: str, scope_type: str) -> bool:
    """Exact match for the fixed paths; prefix match only for the mount.

    Reads the surface *in force* rather than `_REMOTE_ALLOWED_PATHS` directly,
    which is how the runtime stop switch above works: stopping swaps in the
    empty map and this answers False for every path, so the guard emits the
    refusal it already emits, on every remote scope type, with no branch added
    to it.

    `startswith("/static")` alone would also admit `/staticfoo`, so the mount
    is matched as the directory it is — and its verdict is read out of the same
    map, so emptying the map empties it too. That is why the mount is an entry
    rather than a literal here.

    The scope type is part of the key, not an afterthought: an entry admits the
    protocol it was written for and no other. `/ws/acp` is websocket-only;
    `/acp`, `/remote-auth` and the `/static` mount are http-only. Without this,
    a websocket upgrade to `/static/anything` passed the guard on the cookie
    alone and reached `StaticFiles.__call__`, whose first statement asserts an
    http scope.
    """
    surface = _live_remote_surface()
    allowed = surface.get(path)
    if allowed is None and path.startswith(_REMOTE_STATIC_MOUNT + "/"):
        allowed = surface.get(_REMOTE_STATIC_MOUNT)
    return allowed is not None and scope_type == allowed


async def _refuse(scope, send) -> None:
    """Scope-typed refusal.

    Emitting `http.response.start` into a `websocket` scope is an ASGI protocol
    violation and surfaces as a uvicorn exception rather than a refusal — on
    the very path this guard exists to protect. uvicorn turns a pre-accept
    close into an HTTP 403 handshake rejection and discards the code, so 1008
    records the intent rather than what a client observes.
    """
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    await send({"type": "http.response.start", "status": 403, "headers": [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(_FORBIDDEN_BODY)).encode("ascii")),
    ]})
    await send({"type": "http.response.body", "body": _FORBIDDEN_BODY})


def _remote_navigation_ok(scope) -> bool:
    """`_acp_navigation_ok`'s `Sec-Fetch-Site` rule, for remote GETs only.

    Cookies are host-scoped and **port-agnostic**, so another service listening
    on any port of the NetBird address is "same-site" as far as
    `SameSite=Strict` is concerned. This rule closes the browser half of that.

    It constrains browsers only: the rule falls back to
    `_origin_or_referer_ok(allow_missing=True)` when the header is absent, and
    a non-browser client simply omits it. **The cookie, not this rule, is the
    control against a non-browser remote client.**
    """
    try:
        return _acp_navigation_ok(Request(scope))
    except Exception:  # pragma: no cover - a malformed scope must not 500
        return False


class RemoteAccessGuard:
    """The one construct that sees every ASGI scope type (D7, revised).

    `BaseHTTPMiddleware.__call__` returns early on a non-`http` scope, so
    `same_origin_guard` — and its `_ALLOWED_HOSTS` rebinding defence — never
    sees an upgrade. Two enforcement points were the other candidate and were
    also wrong: `/static` is a `Mount` whose `matches` admits websocket scopes,
    so `ws://<ip>/static/x` reaches `StaticFiles` having passed neither
    `same_origin_guard` nor `ws_acp`'s own checks.

    Registered **after** `same_origin_guard`, which makes it OUTERMOST:
    `add_middleware` inserts at index 0 and the stack is built over
    `reversed(middleware)`, so the last registered wraps the rest. A deny
    survives either order; the refusal body and whether an inner guard's
    logging fires do not.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            if _is_remote_peer((scope.get("client") or (None,))[0]):
                path = scope.get("path") or ""
                if not _remote_path_allowed(path, scope["type"]):
                    await _refuse(scope, send)
                    return
                if path not in _COOKIE_EXEMPT and not _cookie_ok(scope):
                    await _refuse(scope, send)
                    return
                # `http`-scope GETs only. The `/ws/acp` upgrade is a GET at the
                # HTTP layer but a `websocket` ASGI scope, and browsers do not
                # attach `Sec-Fetch-Site` to a WebSocket handshake — the literal
                # reading would break the phone client outright.
                if (scope["type"] == "http" and scope.get("method") == "GET"
                        and not _remote_navigation_ok(scope)):
                    await _refuse(scope, send)
                    return
        await self.app(scope, receive, send)


# Registered here, after `same_origin_guard`'s definition, so that this guard
# ends up outermost. Moving this line above that decorator silently inverts the
# order.
app.add_middleware(RemoteAccessGuard)


# Per-process, never persisted, regenerated every launch. The origin check
# below stops a web page; it does nothing against a local non-browser process,
# which can send any header it likes — and the ACP agent runs with
# trust-all-tools, so that gap is arbitrary command execution.
#
# Residual risk, stated rather than implied: this token is delivered inside a
# page served over unauthenticated HTTP, so any local process that can fetch
# GET /acp can read it. That raises the bar from "connect blindly" to "scrape
# one page first"; it is not a boundary. Closing it properly means
# authenticating the page route too, which this prototype does not do.
_ACP_TOKEN = secrets.token_urlsafe(32)


def _acp_token_ok(supplied: str) -> bool:
    """Constant-time token comparison that cannot fault on hostile input.

    ``secrets.compare_digest`` raises ``TypeError`` for a ``str`` holding
    non-ASCII, and query params arrive URL-decoded — so ``?t=%C3%A9`` would turn
    a 403 on the authentication path into a 500 that any unauthenticated caller
    can drive. Comparing UTF-8 bytes keeps the comparison constant-time and
    fails closed for every wrong token instead.
    """
    return secrets.compare_digest(
        supplied.encode("utf-8", "replace"), _ACP_TOKEN.encode("utf-8")
    )


def _ws_origin_ok(ws: WebSocket) -> bool:
    """Mandatory first line of *every* WebSocket route in this module.

    Middleware cannot do this: ``BaseHTTPMiddleware.__call__`` returns early on
    ``scope["type"] != "http"``, so ``same_origin_guard`` above — including its
    ``_ALLOWED_HOSTS`` DNS-rebinding defense — never sees an upgrade request.
    A new ``@app.websocket`` route that omits this call ships unprotected.

    Both halves are derived from the raw ``Host`` header, through the same
    ``_host_allowed`` parser the HTTP path uses, and **nothing here reads**
    ``ws.url``. Deriving them from the URL instead was argued safe on the
    grounds that the two halves then agree with each other; they do not, and
    which of them is wrong depends on the Starlette in front of it:

    * ``Host: evil.com@127.0.0.1:4915`` with a matching ``Origin`` **passes**
      on starlette 0.37.2, which has no ``_HOST_RE``: the raw header goes
      straight into the URL, ``hostname`` keeps only what follows the last
      ``@`` and reads ``127.0.0.1``, while ``netloc`` keeps the userinfo and so
      reproduces the attacker's Origin exactly. ``_host_allowed`` rejects any
      ``@`` outright, which is why that trap is its own first check.
    * ``Host: [::1`` **raises** ``ValueError`` out of ``urlsplit`` on 0.37.2,
      turning a rejection into a traceback on the handshake path.

    Starlette 1.3.1 rejects both through ``_HOST_RE`` and substitutes
    ``scope["server"]``, which is why neither is observable from the test
    interpreter. Reading the header is what makes the verdict the same on both.

    ``getlist`` for the same reason ``_request_host_allowed`` uses it: zero
    Host headers left the URL nothing but the ``scope["server"]`` fallback, so
    an absent Host was a loopback Host, and two or more is a smuggling shape
    where which copy is authoritative differs between hops.
    """
    hosts = ws.headers.getlist("host")
    if len(hosts) != 1 or not _host_allowed(hosts[0]):
        return False
    # Safe to put back into a URL: ``_host_allowed`` has established that this
    # is a loopback name, optionally bracketed, with at most a numeric port and
    # none of ``@/\?#``. Compared case-insensitively because the allowlist is,
    # and a browser's ``Host`` and ``Origin`` are the same string from the same
    # address bar.
    scheme = "https" if ws.scope.get("scheme") == "wss" else "http"
    expected = f"{scheme}://{hosts[0].strip()}"
    return ws.headers.get("origin", "").lower() == expected.lower()


def _launchers_without_env(launchers) -> list[dict]:
    """Launcher entries with ``env`` removed, for anything that leaves this process.

    A custom launcher's ``env`` is the one field in the config that routinely
    holds production credentials — the live file carries `AUTH_TOKEN_PRODUCTION`
    and `AUTH_TOKEN_STAGING` — and it used to travel on **three** paths that
    nothing authenticates: `GET /api/launchers`, the `custom_launchers` key of
    `GET /api/settings`, and the `|tojson` bootstrap that puts the whole list in
    the page source of `/`. None of the three needs it: the tile partial never
    renders `env`, and the only consumer is the edit modal, which now asks for
    one launcher's env explicitly (`/api/launcher/env`).

    **What this is and is not.** It removes the credentials from payloads that
    are fetched routinely, cached by the browser, visible in `view-source:`, and
    readable by any local process issuing a single GET with a loopback `Host`.
    It does **not** authenticate anything — `same_origin_guard` never did, and a
    local process that deliberately forges an `Origin` header can still reach
    the POST below. The exposure goes from ambient to deliberate, which is worth
    having and is not the same as fixed. The durable answer remains
    reference-by-name indirection into the OS credential store, recorded as
    shape (a) on `plans/ROADMAP.md`.

    Shallow copies, and only the one key dropped: these dicts are the live
    config objects, so mutating them here would strip `env` from the process's
    own state and the next `save_config` would write the credentials out of the
    file entirely.
    """
    out = []
    for entry in launchers or ():
        if isinstance(entry, dict):
            out.append({k: v for k, v in entry.items() if k != "env"})
        else:
            out.append(entry)
    return out


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    config = load_config()
    profile = get_active_launch_profile(config)
    return templates.TemplateResponse(request, "index.html", {
        "port": config.port,
        "active_launch_profile": profile,
        "launch_profiles": [asdict(p) for p in config.launch_profiles],
        "autostart": autostart.is_enabled(),
        # Stripped: this one lands in the page source via `|tojson`.
        "launchers": _launchers_without_env(config.custom_launchers),
        "peek_hotkey": config.peek_hotkey,
        "default_directory": config.default_directory,
        "provider_settings": config.provider_settings,
        "autostart_label": "Start at login" if sys.platform != "win32" else "Start with Windows",
    })


def _acp_csp(nonce: str, host: str) -> str:
    """The policy served with ``/acp``, and only with ``/acp``.

    Nonce-based or not worth shipping. ``acp.html`` carries its own inline
    ``<script>``, so ``script-src 'self'`` would blank the page; ``'unsafe-inline'``
    would admit both an injected ``<script>`` and an ``<img onerror=…>``, which
    is the exact vector this exists to stop. A value the page cannot be made to
    guess is the only form that blocks injected markup while the page's own
    script runs.

    It is not applied globally. ``index.html`` holds substantial inline script
    and ``static/htmx.min.js`` binds at ``DOMContentLoaded``; a policy there
    would risk the dashboard for no gain, and the dashboard does not render
    agent-authored text.

    ``connect-src`` names the WebSocket origins rather than leaning on
    ``'self'``: whether ``'self'`` covers a ``ws://`` upgrade from an ``http:``
    page is a CSP3 clarification rather than something every engine has always
    done, and a ``connect-src`` that blocks ``/ws/acp`` takes the whole feature
    down while every server-side test still passes.

    ``host`` is the Host header ``_host_allowed`` accepted, which bounds the
    **name** to the allowlist and nothing else: ``_PORT_RE`` proves the suffix
    is one to five decimal digits, not that it is a port in range, so
    ``127.0.0.1:99999`` reaches ``connect-src`` intact. That is a policy naming
    an origin nothing can connect to — the page's own socket still matches the
    real origin the browser loaded it from — so it costs a broken page for a
    caller who chose to break it, and grants nothing.

    ``img-src`` is the one directive that had to be widened, and only to
    ``blob:``. Without it ``default-src 'self'`` governs images, and ``'self'``
    does not cover a ``blob:`` URL — so the thumbnails of the images a user has
    staged for the next prompt would silently not render.

    ``blob:`` rather than ``data:`` deliberately. A ``blob:`` URL is revocable,
    which is what lets the page hand back the several hundred KB behind each
    thumbnail the moment the turn it belongs to starts; a ``data:`` URI is a
    string living in a DOM attribute for as long as the node does. It is also
    the narrower grant: ``blob:`` names something this page minted, while
    ``data:`` admits any bytes anything can spell.

    What this does **not** re-admit is the case the page refuses on purpose. An
    image in agent-authored markdown is dropped before it becomes an element at
    all — ``MD_DROPPED.image`` in ``acp.html`` — because a remote URL in an
    ``<img>`` is a request this page would be making on the agent's say-so.
    That refusal is upstream of the policy and unaffected by it, so the two
    layers stay independent: the CSP admits local bytes the user pasted, and
    the renderer still refuses remote ones the agent named.
    """
    return "; ".join((
        "default-src 'self'",
        f"script-src 'nonce-{nonce}'",
        "img-src 'self' blob:",
        f"connect-src 'self' ws://{host} wss://{host}",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
    ))


@app.get(_ACP_PATH, response_class=HTMLResponse)
async def acp_page(request: Request, sid: str = ""):
    """The ACP prototype page. ``sid`` names the session to re-subscribe to.

    This page is the ACP token's only delivery vehicle, so it repeats the
    ``_ALLOWED_HOSTS`` check that ``same_origin_guard`` now runs for every
    method. The duplication is deliberate: the middleware was POST-only until
    recently, and narrowing it again would silently make this route hand the
    token to whatever Host a rebinding attack chooses. It calls the same
    ``_request_host_allowed`` helper, so the rule has one home and the two
    copies cannot drift into disagreeing about what a loopback Host is.
    """
    if not _request_host_allowed(request):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    # Fresh per response, so a nonce read out of one page's markup is already
    # spent by the time it could be replayed into another.
    nonce = secrets.token_urlsafe(16)
    response = templates.TemplateResponse(request, "acp.html", {
        "acp_token": _ACP_TOKEN,
        "sid": sid,
        "csp_nonce": nonce,
        # Whether the dashboard is reachable *for this viewer*. `/` is not on
        # `_REMOTE_ALLOWED_PATHS` and never will be (SC-4), so the topbar's
        # "back to PowerAtlas" link is a guaranteed 403 from a phone — a
        # control that exists only to fail. Rendered as a link for a loopback
        # viewer and as plain text for a remote one.
        #
        # From `scope["client"]` and never the `Host` header (D26): a remote
        # peer can send `Host: 127.0.0.1:4915` and would otherwise be handed a
        # link it cannot follow. Nothing here is a security decision — the
        # guard already refused or admitted this request — so a wrong reading
        # costs a link, not a boundary.
        "local": not _is_remote_peer((request.scope.get("client") or (None,))[0]),
        # Non-empty when the guarded import above failed. The page renders the
        # reason and does not open a socket, rather than retrying against a
        # route that cannot answer.
        "acp_error": _ACP_IMPORT_ERROR,
    })
    # `_request_host_allowed` has already established that there is exactly one
    # Host header and that it parses to a loopback name with a numeric port, so
    # nothing hostile survives into the header value.
    response.headers["Content-Security-Policy"] = _acp_csp(
        nonce, request.headers["host"].strip())
    # This page is the ACP token's only delivery vehicle, so the response body
    # is a live credential. The token rotates per launch and the page survives a
    # stale one, so a retained copy is not a live hole — but nothing should be
    # holding one on disk or in an intermediary either way. Scoped to this
    # route: no other response carries a secret, and `StaticFiles` deliberately
    # sets no caching headers at all.
    response.headers["Cache-Control"] = "no-store"
    return response


@app.websocket("/ws/acp")
async def ws_acp(ws: WebSocket) -> None:
    """Transport for the ACP page. Token, then origin, then hand off.

    Both checks run before ``accept()``. uvicorn converts a pre-accept close
    into an HTTP 403 handshake rejection and discards the code, so 1008 is the
    intent recorded here rather than what a client observes.

    Past the handoff this route is an opaque router: ``acp`` owns the frames
    and this function never inspects a ``type``, which is what lets later
    phases add message types without touching ``web.py``.

    **From a remote peer the only controls on this upgrade are the device
    cookie and ``_ACP_TOKEN``.** An earlier note claimed ``/ws/acp`` was
    incidentally browser-only from remote, on the grounds that ``_ws_origin_ok``
    demands an ``Origin`` that non-browser clients do not send. That is false
    and was disproved by execution, not by reading: ``_ws_origin_ok`` requires
    only a *self-consistent* ``Host``/``Origin`` pair, which any scripted
    client sets in one line, and ``_host_allowed`` admits loopback names
    without reference to the peer's actual address — so a remote client may
    simply claim ``Host: 127.0.0.1:4915``. A non-browser client on a remote
    address, presenting a valid cookie and token, reaches ``accept()`` and
    ``acp.serve_socket``.

    Recorded here because a phantom control is worse than a missing one: a
    later cleanup of ``_ws_origin_ok`` would otherwise be priced as removing a
    real defence against non-browser clients when it removes nothing of the
    sort. ``_ws_origin_ok`` is browser-CSRF hygiene — it stops a *web page* on
    another origin from opening this socket — and nothing more.
    """
    if not _acp_token_ok(ws.query_params.get("t", "")):
        await ws.close(code=1008)
        return
    if not _ws_origin_ok(ws):
        await ws.close(code=1008)
        return
    if acp is None:
        # The guarded import failed. Close after accept so the reason survives:
        # a pre-accept close becomes a bare 403 handshake rejection.
        await ws.accept()
        await ws.close(code=1011, reason="ACP prototype unavailable")
        return
    await ws.accept()
    await acp.serve_socket(ws)


# --- The session browser's data source -----------------------------------
#
# A purpose-built read-only listing (D18) rather than a reuse of
# `/partials/all-sessions`: that partial renders `partials/session_row.html`,
# which is hover-driven and carries the launch-action cluster — dashboard
# markup that is useless on a phone and undesirable on a surface intended to
# leave loopback. A narrow route is also auditable against the remote
# allowlist, which a partial that renders whatever the template grows is not.
#
# **This path is on `_REMOTE_ALLOWED_PATHS` as of Phase 5b**, and was held off
# it until then: registering a path before the route existed would have made it
# remotely reachable the moment it was written, inverting the default-deny the
# allowlist exists to provide. It is registered now because the rail is what a
# phone opens `/acp` for, and it stays behind the device cookie either way —
# the allowlist and the cookie are two conditions, not two options.
#
# `_ACP_LISTING_PATH` itself is defined near `_REMOTE_AUTH_PATH` at the top of
# this module, because the allowlist dict is built at import time and needs the
# name before this point in the file is reached.

# D16's defaults — 10 groups, 3 sessions each. The product of the two is what
# bounds the per-row lock check to ~30 rather than the store's 1,207.
_ACP_GROUPS_PER_PAGE = 10
_ACP_SESSIONS_PER_GROUP = 3
# A caller-supplied page size is an amplification lever, so both axes are
# clamped — but they are not equally expensive. A row costs one `.lock` read
# plus one `psutil` query; a *group* costs that for its rows **plus a full
# session load**, because the group's `total` needs its whole list. The group
# axis is therefore the amplification axis: measured against the real store,
# the previous 50-group ceiling answered a single 50x50 request with 472 rows
# and 975 of the store's 1,210 sessions loaded.
#
# 20 is twice what the product asks for — the rail shows 10 groups with a
# show-more — which leaves headroom for a client wanting a larger first page
# while halving the worst-case group fan-out. The session axis stays at 50: an
# extra row there costs one slice of an already-loaded list plus one lock read,
# and paging a 208-session workspace is a real use. This route becomes remotely
# reachable in Phase 5, so both numbers are bounds, not preferences.
_ACP_MAX_GROUPS_PER_PAGE = 20
_ACP_MAX_SESSIONS_PER_GROUP = 50

# The flat axis, used when the rail groups by day instead of by workspace. It
# has one page size because it has one axis: no group carries a `total`, so
# none of the group-axis amplification above applies and a row costs exactly a
# row.
#
# 30 matches the ~30 rows the grouped default puts on screen (10 x 3), which is
# what bounds the per-row lock check. It is also what makes the day grouping
# useful rather than degenerate: measured against this store, 30 rows reach
# back to 2026-07-19, so a first page is roughly two weeks of day groups rather
# than one enormous "Today".
#
# The ceiling is where the cost stops being free rather than where it starts to
# hurt. `_acp_availability` has no wall-clock budget of its own and is strictly
# O(rows) — measured at 0.12-0.21 ms a row, so 100 rows is 17.9 ms — while the
# collect-and-sort behind it dominates at 140-340 ms warm. The bound exists
# because this route is remotely reachable and a caller-supplied page size is
# an amplification lever, not because 100 rows is expensive.
_ACP_FLAT_PAGE_SIZE = 30
_ACP_MAX_FLAT_PAGE_SIZE = 100

# Bounds the payload, not the store: a kiro-cli title is free text and a first
# prompt can be thousands of characters, and neither belongs in a rail row.
_ACP_TITLE_MAX_CHARS = 120

# kiro-cli only, and not by omission: ACP is v2-only (see the plan's scope
# boundaries), so a row this endpoint served for another provider would be a
# session the browser cannot resume.
_ACP_LISTING_PROVIDER = "kiro-cli"


def _acp_row_title(session) -> str:
    """The rail's label for a session.

    `data_kiro` stamps `"<untitled>"` when the store carries no title, and the
    `session-tab-title` steering rework that would populate it is out of scope
    for this plan — so the honest fallback is the raw first prompt, which is
    what the user actually typed and what they will recognise.
    """
    title = (session.title or "").strip()
    if not title or title == "<untitled>":
        title = (session.first_prompt or "").strip()
    return title[:_ACP_TITLE_MAX_CHARS]


def _acp_availability(session_ids, held) -> dict[str, str]:
    """D17's three states for **these** ids and no others.

    Blocking — one bounded file read plus a `psutil` query per id — so this runs
    under `asyncio.to_thread`, never on the loop.

    `held` is a snapshot taken on the loop and passed in; this function must not
    reach for `_supervisor.sessions` itself. `_supervisor` state is loop-owned
    and unlocked by design, and D9 forbids reading it from a worker thread:
    iterating it here while the loop mutates gives a torn read or an outright
    `RuntimeError: dictionary changed size during iteration`.

    **Fails open to `available`.** A wrongly-greyed session is unreachable from
    the UI with no way for the user to find out why; a wrongly-available one
    costs one click and gets the agent's own typed in-use refusal at load. The
    hint may only add a refusal, never grant one — the same rule
    `acp._lock_holder` states for itself.
    """
    out: dict[str, str] = {}
    for sid in session_ids:
        if sid in held:
            out[sid] = "held"
            continue
        state = "available"
        try:
            if acp is not None and acp._lock_holder(sid) is not None:
                state = "locked"
        except Exception:
            state = "available"
        out[sid] = state
    return out


def _acp_status_for_held(sessions) -> dict[str, str]:
    """The dashboard's verdict for the sessions this PowerAtlas is driving.

    Blocking — a transcript-tail classify per session — so this runs inside
    `_acp_listing`'s thread hop, beside `_acp_availability`.

    **Held sessions and no others, which is also what bounds the cost.** The
    rail draws a dot only where this ACP holds the session, so a row nothing
    here holds needs no verdict: a `locked` one is live in a foreign process
    this cannot ask, and an `available` one has no live process at all. That
    caps the work at `MAX_SESSIONS` (8) however many rows the page shows.

    `_resolved_session_status`, and deliberately not `_session_status`. The
    latter opens with a liveness gate that asks `presence` whether a process is
    running — which for a held session is answered first-hand, because we *are*
    that process — and it can answer `closed`, which for a held session is not a
    state but a lag in a 3 s-cached process scan. `_resolved_session_status`
    takes liveness as given and settles among errored/waiting/working, the same
    precedence `session_row.html` settles its own dot through, so the two
    surfaces cannot disagree about a session both of them are showing.
    """
    if not sessions:
        return {}
    # One scan for the whole response. It contributes nothing for kiro-cli
    # today — only claude-code writes a self-reported status, and
    # `presence._sidecar_records` pushes "" for every kiro record — but passing
    # it is what keeps this on the shared code path instead of a local
    # re-implementation that would drift the first time that stops being true.
    snapshot = presence.get_snapshot()
    out: dict[str, str] = {}
    for session in sessions:
        try:
            semantic = get_semantic_status(
                session.session_id, _ACP_LISTING_PROVIDER, session.cwd)
            out[session.session_id] = _resolved_session_status(
                snapshot, _ACP_LISTING_PROVIDER, session.session_id, semantic)
        except Exception:
            log.exception("ACP listing: could not settle status for %s",
                          session.session_id)
            # The direction `_resolved_session_status` itself fails in when
            # nothing classifies. A session this process holds is running, and
            # a quieter-looking verdict would be a wrong one on a live row.
            out[session.session_id] = "working"
    return out


# Error codes that mean **"there is nothing at that path"**. Everything not
# named here — including everything Windows and POSIX have to say about a host
# that did not answer — reads as *unknown*, and unknown fails open to present.
#
# Deliberately an allowlist of absence rather than a denylist of failure: a code
# nobody anticipated is far likelier to be a new way of not reaching a share
# than a new way of a directory being gone, and the cheap error is the one that
# leaves a live workspace unbadged.
_ACP_ABSENT_WINERRORS = frozenset({
    2,    # ERROR_FILE_NOT_FOUND
    3,    # ERROR_PATH_NOT_FOUND — an unmapped drive letter lands here
    123,  # ERROR_INVALID_NAME — a name Windows will never resolve
    267,  # ERROR_DIRECTORY
})
_ACP_ABSENT_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR, errno.ENAMETOOLONG})


def _acp_stat_says_absent(exc: OSError) -> bool:
    """Does this failed `stat` mean *gone*, or merely *unanswered*?

    **`winerror` is read before `errno`, and that ordering is the whole point.**
    Measured on this machine 2026-08-01, Python 3.13.13: `os.stat` on an
    unreachable UNC path raises `ERROR_BAD_NETPATH` — `winerror=53` — carrying
    `errno=2`, i.e. `ENOENT`. Reading `errno` first therefore calls a host that
    did not answer "absent", which is exactly the misreading this function
    exists to stop. On POSIX `winerror` is absent and `errno` decides alone.
    """
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        return winerror in _ACP_ABSENT_WINERRORS
    return exc.errno in _ACP_ABSENT_ERRNOS


def _acp_cwd_exists(cwd: str) -> bool:
    """Does the workspace directory still exist on disk?

    A **separate question from D17's availability**, which measures lock
    liveness and nothing else. Measured on the real store 2026-08-01: 14 of 65
    workspaces name a directory that is gone, including the 208-session
    `nrf_tool` worktree that is D19's own showcase — and every one of their
    sessions reports `available`, correctly, because no process holds a lock on
    a session in a deleted tree. The rail would otherwise offer 208 sessions
    that fail the moment one is tapped.

    The field has to come from here because **a browser cannot stat a
    filesystem**; there is no client-side answer to substitute. Cost is one
    `stat` per returned group — ~10-20 a page, not per row — bounded in total by
    `_acp_exists_flags`, and this function body runs inside `asyncio.to_thread`
    with the rest of `_acp_listing`.

    **Fails to `True` on any failure that is not positively an absence.** A
    permission error, an unmounted network drive or a host that did not answer
    must not be reported as a vanished workspace: a false "gone" badge tells the
    user to stop trusting rows that are fine, which is the more expensive error
    of the two.

    **`os.stat`, not `Path.exists()`, and the difference is the contract.**
    `Path.exists()` swallows every `OSError` whose code is on pathlib's own
    ignore list (`_IGNORED_ERRNOS` = ENOENT/ENOTDIR/EBADF/ELOOP,
    `_IGNORED_WINERRORS` = NOT_READY/INVALID_NAME/CANT_RESOLVE_FILENAME) and
    returns `False` without raising — so an `except OSError` wrapped around it
    never sees them and the fail-open above was half a promise. Measured
    2026-08-01: `os.stat(r"\\\\<unreachable-host>\\share\\proj")` raises
    `winerror=53` with `errno=2`, and `Path(...).exists()` answers `False` for
    the same path without raising at all. The unreachable network workspace the
    paragraph above cites was therefore badged "folder missing" — the expensive
    error — for exactly the reason the paragraph said it must not be.

    **What stays indistinguishable.** An unmapped drive letter (`Q:\\proj`)
    raises `ERROR_PATH_NOT_FOUND`, the same code a deleted local directory
    raises, and is reported gone. Windows offers nothing to separate the two and
    this function does not guess.
    """
    try:
        os.stat(cwd)
    except OSError as exc:
        return not _acp_stat_says_absent(exc)
    except ValueError:
        # A path the OS cannot be asked about at all — an embedded NUL. Nothing
        # transient about it and no later `stat` will succeed, so it is absent
        # rather than unknown.
        return False
    return True


# Wall-clock ceiling on the `stat` phase of one listing response. Measured
# 2026-08-01 on this machine: `os.stat` on a routable-but-dead UNC host
# (`\\10.255.255.1\share\x`) took **42.2 s** to return `ERROR_BAD_NETPATH`, and
# one on an unresolvable host took 2.6 s. The event loop is not blocked —
# `_acp_listing` runs under `asyncio.to_thread` — but the *response* is, and at
# `_ACP_MAX_GROUPS_PER_PAGE` = 20 the unbounded serial version could hold a
# single request for over ten minutes.
_ACP_EXISTS_BUDGET_SECONDS = 2.0


def _acp_exists_flags(cwds: list[str]) -> list[bool]:
    """`_acp_cwd_exists` for each cwd, under one budget for the whole request.

    The deadline is checked **between** calls, never inside one: `os.stat` takes
    no timeout and there is no interrupting it, so the achievable bound is *one*
    stalled `stat` per request rather than up to twenty. That is the guarantee
    made here and it is worth stating plainly — a caller reading "capped at 2 s"
    would be reading a promise this cannot keep.

    Every cwd past the deadline reports `True`, the same fail-open reading
    `_acp_cwd_exists` gives an unanswered `stat`, and for the same reason: not
    having looked is not evidence of absence.
    """
    out: list[bool] = []
    deadline = time.monotonic() + _ACP_EXISTS_BUDGET_SECONDS
    for index, cwd in enumerate(cwds):
        # The first entry is unconditional, and the exemption is explicit rather
        # than inferred from the clock. Reading `time.monotonic() >= deadline`
        # for index 0 as well looks equivalent and is not: at a budget of zero
        # it skips the only group on a single-workspace page and answers `True`
        # without having looked, which is a fabricated reading rather than a
        # deferred one. The first test written against this function caught
        # exactly that.
        if index and time.monotonic() >= deadline:
            out.append(True)
            continue
        out.append(_acp_cwd_exists(cwd))
    return out


def _acp_listing(cwd: str, group_page: int, group_size: int,
                 session_page: int, session_size: int, held,
                 capacity: dict) -> dict:
    """Build the listing payload. Blocking; runs off the loop.

    Paginated **independently at both levels** (D19). The existing listing
    filters all set `has_more = False` (`partials_all_sessions`), i.e. they
    filter the loaded page and then declare there is nothing after it —
    inheriting that here would silently truncate this store's 208-session
    workspace at whatever the first page happened to hold. So each group
    carries its own `total`/`has_more` computed from its own session list, and
    the group axis carries its own, and moving one does not move the other.

    A `cwd` selects a single workspace and bypasses the group axis entirely:
    that is the shape the rail's per-group "show more" needs, and it is what
    makes paging a 208-session workspace cost one workspace's sessions rather
    than the whole page's.

    **Honours the `hidden` workspace tag and the provider's enabled flag**, the
    same two config-driven exclusions `/partials/all-sessions` applies — a
    workspace the user hid from the dashboard has not asked to be visible from
    a phone, and a disabled provider is not a listing this route may serve. The
    config read costs an uncached TOML parse, which is exactly why D15 forbids
    it in `at_capacity()`; D15's ban is **on the event loop**, and this function
    body runs entirely inside `asyncio.to_thread`. `load_config` is guarded by a
    `threading.Lock` (`config.py:_lock`) and returns a fresh `Config` per call,
    so it is safe to call from a worker thread. Unlike the dashboard routes this
    one takes no `tag` parameter: there is no "show hidden" view to reveal them,
    so `hidden` here means hidden.
    """
    from .config import get_workspace_settings
    from .data import _normalize_path

    config = load_config()
    if _enabled(config, _ACP_LISTING_PROVIDER):
        workspaces = [
            w for w in data.discover_workspaces_with_counts(_ACP_LISTING_PROVIDER)
            if "hidden" not in get_workspace_settings(config, w[0])["tags"]
        ]
    else:
        workspaces = []

    if cwd:
        target = _normalize_path(cwd)
        matched = [w for w in workspaces if _normalize_path(w[0]) == target]
        page_groups = matched[:1]
        group_total = len(matched)
        group_page = 1
        groups_has_more = False
    else:
        group_total = len(workspaces)
        start = (group_page - 1) * group_size
        page_groups = workspaces[start:start + group_size]
        groups_has_more = start + group_size < group_total

    rows: list[tuple[dict, list]] = []
    sids: list[str] = []
    # Hoisted out of the row loop so one budget covers the whole page rather
    # than each group getting its own — see `_acp_exists_flags`.
    exists_flags = _acp_exists_flags([w[0] for w in page_groups])
    for index, (ws_cwd, _count, _updated, _prov) in enumerate(page_groups):
        try:
            sessions = data.get_sessions(ws_cwd, _ACP_LISTING_PROVIDER)
        except Exception:
            log.exception("ACP listing: could not read sessions for %s", ws_cwd)
            sessions = []
        total = len(sessions)
        s_start = (session_page - 1) * session_size
        page_sessions = sessions[s_start:s_start + session_size]
        sids.extend(s.session_id for s in page_sessions)
        rows.append(({
            "cwd": ws_cwd,
            "name": Path(ws_cwd).name or ws_cwd,
            "total": total,
            "session_page": session_page,
            "has_more": s_start + session_size < total,
            "exists": exists_flags[index],
        }, page_sessions))

    # One call for the whole response, over exactly the ids the response
    # contains — ~30 by default, not the store's 1,207.
    availability = _acp_availability(sids, held)
    # Bounded by the session cap rather than by the page: only a held row
    # carries a dot, so only a held row needs a verdict behind it.
    statuses = _acp_status_for_held([
        s for _meta, page_sessions in rows for s in page_sessions
        if availability.get(s.session_id) == "held"])

    groups = []
    for meta, page_sessions in rows:
        meta["sessions"] = [{
            "id": s.session_id,
            "title": _acp_row_title(s),
            "updated_at": s.updated_at,
            "availability": availability.get(s.session_id, "available"),
            # "" for every row this ACP does not hold. The rail draws no dot
            # there, so there is no verdict to carry and none is invented.
            "status": statuses.get(s.session_id, ""),
        } for s in page_sessions]
        groups.append(meta)

    return {
        "groups": groups,
        "group_page": group_page,
        "group_total": group_total,
        "has_more": groups_has_more,
        # How full the session cap is. The rail can reach `MAX_SESSIONS` in
        # eight taps and had no way to say so: the ninth was refused by the
        # server *after* `selectSession` had already cleared the transcript and
        # repointed `?sid=`, so the cost of finding out was losing what you were
        # reading. Carried on the listing rather than on a websocket frame
        # because the rail already re-fetches this every 60 s, so the number
        # converges without a new protocol surface.
        "capacity": capacity,
    }


def _acp_flat_listing(page: int, size: int, held, capacity: dict) -> dict:
    """Build the recency-ordered listing payload. Blocking; runs off the loop.

    The listing's second shape: every session this ACP can resume, newest
    first, across all workspaces instead of grouped inside one. It exists so
    the rail can group rows by day, which no amount of the grouped payload can
    be rearranged into — the ten most recently *touched workspaces* are not the
    thirty most recently touched sessions, so a client re-bucketing what the
    grouped route returns would file a session from an eleventh workspace
    nowhere at all.

    **Cutting the days is deliberately the client's job.** "Today" is a
    question about the *reader's* clock, and this route answers a phone on the
    NetBird interface that may be several timezones from this host.
    `_time_bucket` (:181) answers it with `dt.astimezone()`, i.e. in the host's
    zone — correct for the dashboard, which only ever renders locally, and
    wrong here. So this returns instants and the browser decides which day
    each one falls in.

    Honours the same two exclusions the grouped listing does, for the same
    reasons: a workspace tagged `hidden` has not asked to be visible from a
    phone, and a disabled provider is not a listing this route may serve. Both
    are pushed *into* `get_all_sessions_paginated` rather than applied to what
    it returns — see that function's `exclude_cwds` documentation for why the
    placement decides whether `page_size` and `has_more` mean anything.

    Pinned to `_ACP_LISTING_PROVIDER`. `get_all_sessions_paginated` spans every
    registered provider by default, and a row served here for another one would
    be a session the browser cannot resume — the same constraint that makes the
    grouped listing single-provider, arriving from the opposite direction.
    """
    from .config import get_workspace_settings

    config = load_config()
    if not _enabled(config, _ACP_LISTING_PROVIDER):
        return {"sessions": [], "page": page, "has_more": False,
                "capacity": capacity}

    hidden = {
        w[0] for w in data.discover_workspaces_with_counts(_ACP_LISTING_PROVIDER)
        if "hidden" in get_workspace_settings(config, w[0])["tags"]
    }
    try:
        rows, has_more = data.get_all_sessions_paginated(
            page=page, page_size=size,
            provider=_ACP_LISTING_PROVIDER,
            enabled_providers={_ACP_LISTING_PROVIDER},
            exclude_cwds=hidden)
    except Exception:
        log.exception("ACP flat listing: could not collect sessions")
        rows, has_more = [], False

    sessions = [s for s, _prov in rows]
    # Same one-call-per-response discipline as the grouped path, over exactly
    # the ids being returned.
    availability = _acp_availability([s.session_id for s in sessions], held)
    statuses = _acp_status_for_held(
        [s for s in sessions if availability.get(s.session_id) == "held"])

    # One stat per distinct workspace rather than one per row, which is a much
    # bigger saving here than the grouped path ever needed: measured against
    # this store, the 100 most recent sessions live in 5 workspaces. Ordered
    # dedupe rather than a set, so the budget inside `_acp_exists_flags` is
    # spent newest-workspace-first if it runs out.
    order = list(dict.fromkeys(s.cwd for s in sessions))
    flags = dict(zip(order, _acp_exists_flags(order)))

    return {
        "sessions": [{
            "id": s.session_id,
            "title": _acp_row_title(s),
            "updated_at": s.updated_at,
            "availability": availability.get(s.session_id, "available"),
            "status": statuses.get(s.session_id, ""),
            # The workspace behind the row. Grouped by day the rail draws no
            # workspace header, so this is the only thing that can say which
            # project a session belongs to and the only thing a missing-folder
            # warning has left to hang off.
            "cwd": s.cwd,
            "name": Path(s.cwd).name or s.cwd,
            "exists": flags.get(s.cwd, True),
        } for s in sessions],
        "page": page,
        "has_more": has_more,
        "capacity": capacity,
    }


@app.get(_ACP_LISTING_PATH)
async def api_acp_sessions(response: Response, cwd: str = "", group_page: int = 1,
                           group_size: int = _ACP_GROUPS_PER_PAGE,
                           session_page: int = 1,
                           session_size: int = _ACP_SESSIONS_PER_GROUP,
                           mode: str = "", page: int = 1,
                           size: int = _ACP_FLAT_PAGE_SIZE):
    """Sessions for the session browser, grouped or flat. Read-only.

    Returns **only** the workspace path, display name and whether that
    directory still exists, and per session the id, title, updated timestamp,
    availability state and — for a held session alone — the semantic status the
    dashboard would show for it. No `env`, no launcher data, no action
    affordances — the payload is the whole audit surface, so what is not here
    cannot leak from here.

    `status` is `""` for every session this ACP does not hold, and that is a
    narrowing rather than an omission: it is a reading of a transcript's last
    lines, and the sessions worth spending that on are the ones the rail draws
    a dot for. See `_acp_status_for_held`.

    **The whole store is reachable through this route, not a sample of it.**
    Paging is the entire access-control story here: `group_page` walks the
    workspace axis and `session_page` walks the sessions inside each one, and
    neither has a ceiling other than the data running out. Measured over the
    real remote surface, `group_total` is 61 with `has_more: true` — so an
    authorized peer that keeps asking enumerates **every workspace path and
    every session title on this machine**. The rail's default 10 workspaces by
    3 sessions is a page size, not a bound, and reading the numbers below as a
    bounded sample is the mistake this paragraph exists to prevent.

    `mode=recent` does not widen that exposure — same route, same store, same
    two exclusions — but it does make collecting it cheaper, and that is worth
    stating rather than leaving to be discovered. It answers one flat
    recency-ordered walk with `page`/`has_more`, so a peer enumerating the
    store follows a single cursor to the end instead of crossing two nested
    axes and reconciling them. Nothing becomes reachable that was not, and
    reaching all of it takes less work.

    **`title` may be raw user prompt text.** `_acp_row_title` falls back to the
    first 120 characters of the session's first prompt whenever the store holds
    no title or the literal `"<untitled>"` — 267 of the real store's 1,210
    sessions, 22.1%. That proportion describes how often the fallback fires; it
    does **not** bound the exposure, because the peer can page to all 1,210.
    The fallback is deliberate (the `session-tab-title` rework that would
    populate the field is out of this plan's scope, and the first prompt is
    what the user will recognise), but it means one field of this payload
    carries free-form text the user typed.

    What that is in practice, from page one of the real listing: an employer
    name, four client and project names, the directory layout of the whole
    machine, a colleague's first name and the subject line of an email. Anyone
    deciding whether to enable remote access — on a work laptop especially —
    should read this route as publishing the *names of everything you have
    worked on*, to every peer holding a valid device cookie, and decide on that
    basis rather than on the 22.1%.

    Workspaces tagged `hidden` and a disabled `kiro-cli` provider are excluded;
    see `_acp_listing`.

    Sub-agent sessions are absent because `data_kiro.load_sessions` skips any
    record carrying `parent_session_id`; that filter removes 4,734 of the
    store's 5,941 files and this route inherits it rather than re-deriving it.

    `no-store`: availability is a liveness reading with a lifetime of seconds,
    and a phone rendering a cached `available` for a session another process
    took in the meantime is exactly the wrong failure to cache.
    """
    response.headers["Cache-Control"] = "no-store"
    # **On the loop, synchronously, before the thread hop** (D9). `sessions` is
    # loop-owned and unlocked; a worker thread iterating it races every mutation
    # the loop makes. `frozenset` also makes the snapshot un-mutable by anything
    # downstream, so the thread cannot write back into loop-owned state either.
    held = frozenset(acp._supervisor.sessions) if acp is not None else frozenset()
    # Same loop-side snapshot rule as `held`, and the same reason. `_reserved`
    # counts creations in flight, and `at_capacity()` is
    # `len(sessions) + _reserved >= MAX_SESSIONS` — so counting `held` alone
    # would report a free slot during the ~0.5-1.1 s a `session/new` is
    # resolving, which is exactly when a second tap arrives.
    capacity = {
        "held": (len(held) + acp._supervisor._reserved) if acp is not None else 0,
        "max": acp.MAX_SESSIONS if acp is not None else 0,
    }
    # An exact match, not a truthiness test. `mode` is caller-supplied and the
    # only value that means anything is this one; anything else — a typo, an
    # older client, a probe — falls through to the grouped shape, which is the
    # response every existing caller already expects.
    if mode == "recent":
        return await asyncio.to_thread(
            _acp_flat_listing, max(1, page),
            max(1, min(size, _ACP_MAX_FLAT_PAGE_SIZE)), held, capacity)
    return await asyncio.to_thread(
        _acp_listing, cwd,
        max(1, group_page), max(1, min(group_size, _ACP_MAX_GROUPS_PER_PAGE)),
        max(1, session_page), max(1, min(session_size, _ACP_MAX_SESSIONS_PER_GROUP)),
        held, capacity)


# --- The create flow's workspace list ------------------------------------
#
# **On the remote surface, unlike its sibling below**, and the asymmetry is not
# an oversight. Creating a session already works from a phone — `session/new`
# rides `/ws/acp`, which is allowlisted — so a picker that could not list
# workspaces remotely would break a capability that exists today. Deletion is
# the opposite: it does not exist yet, so keeping it local costs nothing.
#
# It discloses strictly less than `_ACP_LISTING_PATH` already does. That route's
# own docstring records that an authorized peer which keeps paging reaches every
# workspace path *and every session title* on the machine; this one carries the
# paths and counts with no session content at all.
#
# Cheap on purpose. `/api/acp/sessions` costs a full `get_sessions` per group
# because each group's `total` needs the whole list — measured at 975 of 1,210
# sessions loaded for a single 50x50 request — so building a 65-workspace picker
# out of four pages of it would be the most expensive request the app makes.
# `discover_workspaces_with_counts` already carries the count, is cached for 30 s
# and is the same call the dashboard makes, so this is a filter over a warm list.
#
# `_ACP_WORKSPACES_PATH` itself is defined next to `_REMOTE_AUTH_PATH` at the top
# of this file, because the allowlist literal is evaluated before this point.


def _acp_workspaces(capacity: dict) -> dict:
    """Workspaces a session can be created in. Blocking; runs off the loop.

    Excludes the same two sets `_acp_listing` excludes — `hidden`-tagged
    workspaces and a disabled provider — plus a third this route needs and that
    one does not: **workspaces whose directory is gone**. 14 of the real store's
    65 are in that state, and `_resolve_session_cwd` refuses every one of them
    with `BadCwd`, so offering them as create targets would be offering 14
    guaranteed failures. The listing route keeps them because reading an old
    conversation from a deleted tree is perfectly reasonable; creating a new one
    there is not.

    The count of what was dropped is reported rather than swallowed: a picker
    that silently shows 51 of 65 workspaces reads as a broken list.
    """
    from .config import get_workspace_settings

    config = load_config()
    if not _enabled(config, _ACP_LISTING_PROVIDER):
        return {"workspaces": [], "missing": 0, "capacity": capacity}
    found = [
        w for w in data.discover_workspaces_with_counts(_ACP_LISTING_PROVIDER)
        if "hidden" not in get_workspace_settings(config, w[0])["tags"]
    ]
    # One budget for the whole sweep, the same helper and the same reason the
    # listing route uses it: `os.stat` on a routable-but-dead UNC host measured
    # 42.2 s, and this list is longer than one listing page.
    flags = _acp_exists_flags([w[0] for w in found])
    live = [w for w, ok in zip(found, flags) if ok]
    return {
        "workspaces": [{
            "cwd": cwd,
            "name": Path(cwd).name or cwd,
            "sessions": count,
        } for cwd, count, _updated, _prov in live],
        "missing": len(found) - len(live),
        "capacity": capacity,
    }


@app.get(_ACP_WORKSPACES_PATH)
async def api_acp_workspaces(response: Response):
    """Workspace paths, names and session counts, for the create picker.

    No session content of any kind — no ids, no titles, no timestamps. That is
    the whole difference from `_ACP_LISTING_PATH`, and it is why this one can be
    on the remote surface with a smaller disclosure than the route already
    there.

    `capacity` rides along so the picker can refuse at the cap before spending
    anything, the way the rail's rows already do — one request rather than two,
    and the same pair the listing route reports.
    """
    response.headers["Cache-Control"] = "no-store"
    # Loop-side snapshot before the thread hop (D9), exactly as the listing
    # route takes it, and `_reserved` counted for the same reason: a creation
    # in flight already holds the slot this number is asked about.
    held = frozenset(acp._supervisor.sessions) if acp is not None else frozenset()
    capacity = {
        "held": (len(held) + acp._supervisor._reserved) if acp is not None else 0,
        "max": acp.MAX_SESSIONS if acp is not None else 0,
    }
    return await asyncio.to_thread(_acp_workspaces, capacity)


# --- The session browser's delete action ---------------------------------
#
# **The first thing in PowerAtlas that writes to kiro-cli's store.** Everything
# else that touches `~/.kiro/sessions/cli` reads it: `data_kiro` parses and
# caches, `acp._lock_holder` reads a bounded `.lock` prefix, and the listing
# route above says "Read-only" in its first line. That is worth stating once,
# here, because it is the property a reviewer would otherwise assume still held.
#
# **Loopback-only, and the enforcement is the absence below.** `_ACP_DELETE_PATH`
# is deliberately NOT in `_REMOTE_ALLOWED_PATHS`, and that map is default-deny
# (D6), so a remote peer is refused by the guard that already exists rather than
# by a check written here that could be forgotten or inverted. Adding the path to
# that map is the only edit that would make irreversible deletion reachable from
# a phone — one line, in one place, which is where a decision like that belongs.
# The page cooperates rather than relying on the 403: `/acp` renders the menu
# only when `local` is true, so a remote viewer is never offered a control that
# would fail. Being a POST, it also inherits `same_origin_guard`'s Origin/Referer
# check (`:809`) for free.
_ACP_DELETE_PATH = "/api/acp/sessions/delete"

# Everything one session owns in the store — measured against the live store on
# 2026-08-03, not inferred from the loader: 13,993 entries made of 5,958 `.json`,
# 5,958 `.jsonl`, 708 `.history`, 861 `.lock` and ~500 `<id>/` directories (each
# holding a `tasks/` subtree). `data_kiro.load_sessions` opens only the first
# three, so a delete written from the loader's point of view would leave the
# `.lock` and the directory behind: invisible to PowerAtlas, still on disk, and
# — for the `.lock` — still able to make a *reused* id read as `locked`.
_ACP_SESSION_SUFFIXES = (".json", ".jsonl", ".history", ".lock")

# What a path is renamed to before anything is unlinked. Chosen so the staged
# name cannot be picked up as a session again: `data_kiro._iter_meta_files`
# selects on `entry.name.endswith(".json")`, which `<id>.json.pa-deleting-ab12`
# does not satisfy.
_ACP_DELETE_STAGING = ".pa-deleting"

# ERROR_SHARING_VIOLATION. **Measured, not assumed** (Windows 11, Python 3.13,
# 2026-08-03): with a second handle open on a file, `os.unlink` AND `os.replace`
# both raise `winerror=32` / `errno=13`. The second half is what this whole
# design rests on — see `_acp_delete_session`.
_ACP_SHARING_VIOLATION = 32

# A caller-supplied list is a loop bound, so it is capped. The UI sends exactly
# one; the list form exists so that adding bulk deletion later is a change to
# the page rather than to the protocol.
_ACP_MAX_DELETE_IDS = 200


def _acp_session_paths(session_id: str) -> list[Path]:
    """Every path in the store belonging to one session, existing ones only.

    `Path.exists()` rather than the `os.stat` dance `_acp_cwd_exists` argues
    for, and the difference is the input: that function is handed a workspace
    path the agent wrote, which can be an unreachable UNC host. This one only
    ever joins a `_valid_session_id` onto `KIRO_SESSION_DIR`, which is under
    `Path.home()` — local, always answerable, and nothing here is a remote mount.
    """
    base = acp.KIRO_SESSION_DIR
    found = [base / f"{session_id}{suffix}" for suffix in _ACP_SESSION_SUFFIXES]
    out = [path for path in found if path.exists()]
    directory = base / session_id
    if directory.is_dir():
        out.append(directory)
    return out


def _acp_delete_session(session_id: str) -> tuple[str, str]:
    """Remove one session from the store. Blocking; runs off the loop.

    Returns ``("", "")`` on success, or ``(code, message)``.

    **Rename first, unlink second, and that ordering is the correctness
    argument rather than a style.** A session is up to five separate paths, and
    the naive loop — unlink each in turn — has no way to fail cleanly: measured
    above, Windows refuses to unlink a file another process holds open, so a
    delete racing a live holder removes the `.json`, then trips on the `.jsonl`,
    and leaves a store entry that nothing in this repo can parse. The rail would
    show a session whose transcript is gone; `data_kiro.load_sessions` would
    skip it silently; the bytes would stay forever.

    `os.replace` is refused by the *same* sharing violation, and — this is the
    part that makes it useful — a refused rename changes nothing at all. So
    every path is staged to a name first, and only once all of them have moved
    is anything destroyed. A holder anywhere in the set aborts the whole
    operation, and the renames already made are put back.

    **This is a second line of defence, not the first.** The route refuses a
    session that is `held` or `locked` before reaching here. What this catches
    is the gap those checks cannot close: `acp._lock_holder` is explicitly "a
    hint and never the gate" (`acp.py:1175`) and the store carries 861 `.lock`
    files whose pids died long ago, so a live holder with an unreadable lock
    passes the hint. The rename is what stops that becoming a corrupt entry.

    **The one degradation it accepts.** If a rename succeeds and the unlink that
    follows fails, the session is gone as far as every reader is concerned — the
    staged names match no loader pattern — but the bytes remain. That is logged
    loudly rather than reported to the user, because from the caller's point of
    view the delete did happen, and an error naming a file they cannot act on
    would be worse than a log line an operator can grep for.
    """
    paths = _acp_session_paths(session_id)
    if not paths:
        return ("not_found",
                "Nothing left to delete — the store has no files for this "
                "session.")

    # Distinguishes one delete's staging names from another's, so two deletions
    # racing on ids that share a prefix cannot collide on a staged name.
    token = secrets.token_hex(4)
    staged: list[tuple[Path, Path]] = []
    try:
        for path in paths:
            target = path.with_name(f"{path.name}{_ACP_DELETE_STAGING}-{token}")
            os.replace(path, target)
            staged.append((target, path))
    except OSError as exc:
        for target, original in reversed(staged):
            try:
                os.replace(target, original)
            except OSError:
                # The rollback itself failed, which leaves this session
                # half-staged: readers skip it, and no later call will find it
                # under its own name. Nothing here can fix that, so it is
                # recorded with the exact paths an operator would need.
                log.exception("ACP delete: could not restore %s to %s",
                              target, original)
        if getattr(exc, "winerror", None) == _ACP_SHARING_VIOLATION:
            return ("in_use",
                    "A process still has this session's files open. Close it "
                    "there, then try again.")
        log.warning("ACP delete: staging failed for session=%s: %s",
                    session_id, exc)
        return ("failed", f"Could not delete this session: {exc}")

    leftover: list[str] = []
    for target, _original in staged:
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as exc:
            leftover.append(f"{target} ({exc})")
    if leftover:
        log.warning(
            "ACP delete: session=%s is gone from the store's readable names, "
            "but %d staged path(s) could not be removed and are still on disk: "
            "%s", session_id, len(leftover), "; ".join(leftover))
    return ("", "")


def _acp_delete_many(session_ids: list[str], held: frozenset) -> dict:
    """Delete each id, refusing the ones that are not safe. Blocking.

    `held` is a snapshot taken on the loop and passed in, for the reason
    `_acp_availability` gives at length: `_supervisor.sessions` is loop-owned
    and unlocked, and iterating it from a worker thread is a torn read (D9).
    """
    deleted: list[str] = []
    failed: list[dict] = []
    touched: set[str] = set()

    for session_id in session_ids:
        if not acp._valid_session_id(session_id):
            # Before anything joins it to a path. The same guard the `load`
            # path applies, and the reason it exists: this string becomes a
            # filename in a directory whose neighbours are 5,958 other
            # conversations.
            failed.append({"id": session_id, "code": "bad_id",
                           "message": "Not a usable session id."})
            continue
        if session_id in held:
            failed.append({
                "id": session_id, "code": "held",
                "message": "PowerAtlas has this session open. Open it in the "
                           "conversation pane and press Close first."})
            continue
        try:
            holder = acp._lock_holder(session_id)
        except Exception:
            # Same fail-open reading `_acp_availability` takes: the hint may
            # add a refusal, never grant one — so a hint that could not be read
            # does not refuse. The rename staging below is what covers the case
            # where it was wrong.
            holder = None
        if holder is not None:
            failed.append({
                "id": session_id, "code": "locked",
                "message": f"Another process (pid {holder}) is using this "
                           "session. Close it there first."})
            continue

        # Read *before* the delete: it is the session's own metadata file that
        # says which workspace it belongs to, and after the delete there is
        # nothing left to ask.
        cwd = acp._stored_session_cwd(session_id)
        code, message = _acp_delete_session(session_id)
        if code:
            failed.append({"id": session_id, "code": code, "message": message})
            continue
        deleted.append(session_id)
        if cwd:
            touched.add(cwd)

    if deleted:
        # The store has changed under caches that key on it. `data_kiro`'s own
        # index keys on the directory's mtime and self-invalidates, but
        # `session_cache` holds the parsed list per workspace and
        # `discover_workspaces_with_counts` holds the counts for 30 s — so
        # without these two the deleted row comes back on the next Refresh and
        # the workspace header keeps counting it.
        for cwd in touched:
            data.session_cache.forget(cwd, _ACP_LISTING_PROVIDER)
        data.invalidate_workspace_counts()
        log.info("ACP delete: removed %d session(s) across %d workspace(s)",
                 len(deleted), len(touched))

    return {"deleted": deleted, "failed": failed}


@app.post(_ACP_DELETE_PATH)
async def api_acp_delete_sessions(request: Request):
    """Delete sessions from kiro-cli's store. Irreversible. Loopback-only.

    Loopback-only by omission from `_REMOTE_ALLOWED_PATHS` — see the block
    comment above this route, which is where that decision is recorded.

    Answers `{"deleted": [...], "failed": [{"id", "code", "message"}]}` with a
    200 whenever the request itself was well-formed, including when every id in
    it was refused. Per-id outcomes rather than a status code because the list
    form admits partial success, and a 4xx over a mixed result would leave the
    caller unable to tell which half happened.
    """
    if acp is None:
        return JSONResponse(
            {"error": "The ACP module is not loaded, so its store is not "
                      "reachable from here."}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Expected a JSON body."}, status_code=400)
    raw = body.get("session_ids") if isinstance(body, dict) else None
    if not isinstance(raw, list) or not raw:
        return JSONResponse(
            {"error": "'session_ids' must be a non-empty list."},
            status_code=400)
    if len(raw) > _ACP_MAX_DELETE_IDS:
        return JSONResponse(
            {"error": f"At most {_ACP_MAX_DELETE_IDS} sessions per request."},
            status_code=400)
    session_ids = [s for s in raw if isinstance(s, str)]
    # **On the loop, before the thread hop** (D9), exactly as the listing route
    # snapshots it. `_reserved` is deliberately not counted here: it bounds
    # *creation*, and a session still being created holds no store files a
    # delete could reach.
    held = frozenset(acp._supervisor.sessions)
    return await asyncio.to_thread(_acp_delete_many, session_ids, held)


# --- The secret exchange -------------------------------------------------
#
# The only two cookie-exempt remote paths. Without them no remote device could
# ever authenticate, because the page that trades the secret for the cookie
# would itself be refused for having no cookie.

_EXCHANGE_BASE_BACKOFF_SECONDS = 2.0
_EXCHANGE_MAX_BACKOFF_SECONDS = 300.0
# Bounded so an attacker cycling source addresses cannot grow this dict without
# limit. Evicting the oldest entry costs a forgiving attacker nothing they did
# not already have (they can always come from a fresh address), and costs a
# single-address attacker nothing at all.
_EXCHANGE_MAX_TRACKED_PEERS = 512
# `(failure count, monotonic time of the last failure, already warned?)`.
#
# The third field is a log-amplification bound, not throttle state. `/remote-auth`
# is reachable by an unauthenticated remote peer by construction, and the refusal
# below is a WARNING — so without it, a peer that is already locked out writes one
# line to `orchestrator.log` per request, at whatever rate it can issue them,
# forever. The refusal itself costs the peer nothing to retry, which is what makes
# the *logging* the amplified resource rather than the authentication.
#
# Scoped to the lockout window rather than suppressed outright: the first refusal
# in each window is still recorded, so a real attack is still visible in the log —
# once per window, per peer, which is the rate at which it carries new information.
_exchange_failures: dict[str, tuple[int, float, bool]] = {}

# `/remote-auth` is the ONE path an unauthenticated remote peer can reach, so
# it is the one path where an unbounded `await request.body()` is a remote
# resource-exhaustion primitive rather than a local footgun. Measured on the
# unbounded version: a 64 MiB body drove 268.7 MB of peak RSS, and a body of
# 1,000,000 fields cost 1.03 s of **synchronous** CPU inside `parse_qsl` —
# which is time the event loop is not serving any websocket or the dashboard.
#
# The per-peer backoff does not bound either one. It is consulted at request
# entry, so 20 concurrent 8 MiB POSTs from an already-throttled peer are all
# buffered (160 MiB) before a single one records a failure.
#
# 4096 bytes: the real form posts `device_id` (<=64) + `secret` (43 chars of
# `token_urlsafe(32)`) + field names and percent-encoding — under 200 bytes.
# 4 KiB is a >20x margin for a browser that adds hidden fields or a UTF-8
# device name that expands under percent-encoding, while being far too small
# to be worth sending as an attack.
_REMOTE_AUTH_MAX_BODY = 4096
# `parse_qsl` builds a list of every separator-delimited pair before anything
# looks at it, so field *count* is a cost axis of its own. Under a 4 KiB
# ceiling a body of bare `&`s tops out around 4096 fields anyway; 64 is a
# generous ceiling for a two-field form and makes the bound explicit rather
# than incidental to the byte cap.
_REMOTE_AUTH_MAX_FIELDS = 64

_EXCHANGE_FORM = """<!doctype html>
<title>PowerAtlas remote access</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<h1>PowerAtlas remote access</h1>
<p>{message}</p>
<form method="post" action="{action}">
<p><label>Device name<br><input name="device_id" maxlength="64"
   pattern="[A-Za-z0-9_-]+" value="{device_id}" required></label></p>
<p><label>Device secret<br><input name="secret" type="password"
   autocomplete="off" required></label></p>
<p><button type="submit">Authorize this device</button></p>
</form>
"""


def _exchange_backoff_remaining(peer: str) -> float:
    """Seconds left on this peer's lockout, or 0.0.

    Exponential from the first failure, capped. Checked **before** the secret
    is compared, so it throttles guessing rather than merely recording it.
    """
    count, last, _ = _exchange_failures.get(peer, (0, 0.0, False))
    if count <= 0:
        return 0.0
    delay = min(_EXCHANGE_BASE_BACKOFF_SECONDS * (2 ** min(count - 1, 8)),
                _EXCHANGE_MAX_BACKOFF_SECONDS)
    remaining = delay - (time.monotonic() - last)
    return remaining if remaining > 0 else 0.0


def _record_exchange_failure(peer: str) -> None:
    count = _exchange_failures.get(peer, (0, 0.0, False))[0]
    if peer not in _exchange_failures and len(_exchange_failures) >= _EXCHANGE_MAX_TRACKED_PEERS:
        _exchange_failures.pop(next(iter(_exchange_failures)), None)
    # `False`: a new failure opens a new lockout window, and the first refusal
    # inside it is worth one line.
    _exchange_failures[peer] = (count + 1, time.monotonic(), False)


def _claim_throttle_warning(peer: str) -> bool:
    """True once per lockout window, then False until a new failure opens one.

    The caller has already established that this peer is throttled, so the entry
    exists; the `.get` default only guards a concurrent `pop` from the success
    path.
    """
    count, last, warned = _exchange_failures.get(peer, (0, 0.0, False))
    if count <= 0 or warned:
        return False
    _exchange_failures[peer] = (count, last, True)
    return True


def _peer_of(request: Request) -> str:
    client = request.scope.get("client")
    return (client[0] if client else "") or "unknown"


@app.get(_REMOTE_AUTH_PATH, response_class=HTMLResponse)
async def remote_auth_page(request: Request):
    """The form that trades the device secret for the cookie.

    Deliberately script-free and self-contained: it is served to an
    unauthenticated peer, so it must not be a delivery vehicle for anything.
    """
    if not _REMOTE_SECRET:
        return HTMLResponse(
            "<!doctype html><title>PowerAtlas remote access</title>"
            "<h1>Remote access is not configured</h1>"
            "<p>No usable device secret exists on the server.</p>",
            status_code=503)
    return HTMLResponse(_EXCHANGE_FORM.format(
        message="Enter the device secret shown in PowerAtlas settings.",
        action=_REMOTE_AUTH_PATH, device_id=""))


@app.post(_REMOTE_AUTH_PATH, response_class=HTMLResponse)
async def remote_auth_exchange(request: Request):
    """Verify the secret, then set the long-lived device cookie.

    The comparison is constant-time over UTF-8 bytes for the reason
    `_acp_token_ok` documents: `compare_digest` raises `TypeError` on a `str`
    holding non-ASCII, and this field is entirely attacker-chosen, so a `str`
    comparison turns a 403 on the authentication path into a 500 that any
    unauthenticated caller can drive.
    """
    peer = _peer_of(request)
    remaining = _exchange_backoff_remaining(peer)
    if remaining > 0:
        # Once per lockout window. Every subsequent request inside the same
        # window is refused just as hard but writes nothing: see
        # `_exchange_failures` for why the log line, not the check, is the
        # amplified resource here.
        if _claim_throttle_warning(peer):
            log.warning("remote auth throttled for peer %s (%.0fs remaining); "
                        "further attempts in this window are refused silently",
                        peer, remaining)
        return HTMLResponse(_EXCHANGE_FORM.format(
            message=f"Too many attempts. Try again in {int(remaining) + 1}s.",
            action=_REMOTE_AUTH_PATH, device_id=""), status_code=429)
    if not _REMOTE_SECRET:
        log.error("remote auth attempted from %s with no usable device secret",
                  peer)
        return HTMLResponse("Remote access is not configured", status_code=503)
    # Refuse on the declared length **before** awaiting a byte, so an oversized
    # body is never buffered. `Content-Length` is attacker-controlled, hence the
    # streaming ceiling below rather than trust in this check alone.
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > _REMOTE_AUTH_MAX_BODY:
        _record_exchange_failure(peer)
        log.warning("remote auth from %s rejected: body declares %s bytes",
                    peer, declared)
        return HTMLResponse("Request body too large", status_code=413)
    # Stream with a running ceiling for the cases the header does not cover: a
    # chunked request carries no `Content-Length` at all, and a stated one may
    # simply be a lie. Bail on the first chunk that crosses the cap rather than
    # reading to the end to find out how big it was.
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > _REMOTE_AUTH_MAX_BODY:
            _record_exchange_failure(peer)
            log.warning("remote auth from %s rejected: body exceeded %d bytes",
                        peer, _REMOTE_AUTH_MAX_BODY)
            return HTMLResponse("Request body too large", status_code=413)
    try:
        pairs = parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True,
                          max_num_fields=_REMOTE_AUTH_MAX_FIELDS)
    except ValueError:
        # `max_num_fields` reports the overflow by raising. This is a refusal,
        # not a 500: an unauthenticated peer must not be able to drive a
        # traceback out of the authentication path.
        _record_exchange_failure(peer)
        log.warning("remote auth from %s rejected: too many fields", peer)
        return HTMLResponse("Request body too large", status_code=413)
    fields = dict(pairs)
    supplied = fields.get("secret", "")
    device_id = fields.get("device_id", "")
    # Validate the identifier before it reaches a cookie or a log line: a ";"
    # or "," is cookie-attribute injection, a CR-LF is header injection, and a
    # newline in the WARNING below is log injection.
    if not _DEVICE_ID_RE.fullmatch(device_id):
        _record_exchange_failure(peer)
        log.warning("remote auth from %s rejected: invalid device id", peer)
        return HTMLResponse(_EXCHANGE_FORM.format(
            message="Device name must be 1-64 characters of A-Z a-z 0-9 _ -",
            action=_REMOTE_AUTH_PATH, device_id=""), status_code=400)
    if not secrets.compare_digest(supplied.encode("utf-8", "replace"),
                                  _REMOTE_SECRET.encode("utf-8")):
        _record_exchange_failure(peer)
        # D3 makes the cookie "the layer that survives policy drift"; without
        # this line the drift is never observable.
        log.warning("remote auth from %s rejected for device %r: bad secret",
                    peer, device_id)
        return HTMLResponse(_EXCHANGE_FORM.format(
            message="That secret was not accepted.",
            action=_REMOTE_AUTH_PATH, device_id=device_id), status_code=403)
    _exchange_failures.pop(peer, None)
    value = make_device_cookie(device_id)
    response = HTMLResponse(
        "<!doctype html><title>PowerAtlas remote access</title>"
        "<h1>Device authorized</h1>"
        f'<p><a href="{_ACP_PATH}">Open PowerAtlas</a></p>')
    # `set_cookie` rather than a hand-assembled header: it raises `CookieError`
    # on an illegal character, and nothing in `src/` sets a cookie today, so
    # there is no in-house pattern to inherit. No `Secure`: there is no TLS by
    # design (D5) — WireGuard carries the transport.
    response.set_cookie(
        _DEVICE_COOKIE_NAME, value,
        max_age=REMOTE_COOKIE_MAX_AGE_SECONDS,
        httponly=True, samesite="strict", path="/")
    log.info("remote device %r authorized from %s", device_id, peer)
    return response


@app.post("/api/autostart")
async def toggle_autostart():
    if autostart.is_enabled():
        autostart.disable()
    else:
        autostart.enable()
    return {"enabled": autostart.is_enabled()}


@app.post("/api/open-folder", response_class=HTMLResponse)
async def api_open_folder(request: Request):
    body = await request.json()
    folder = body.get("folder", "")
    try:
        is_dir = bool(folder) and Path(folder).is_dir()
    except (OSError, ValueError):
        is_dir = False
    if not is_dir:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": f"Folder not found: {Path(folder).name if folder else '(empty)'}", "level": "error",
        })
    try:
        if sys.platform == "win32":
            os.startfile(folder)
        else:
            subprocess.Popen(
                ["xdg-open", folder],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except OSError as e:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": f"Could not open folder: {e}", "level": "error",
        })
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": f"Opened: {Path(folder).name}", "level": "success",
    })


@app.post("/api/launch-terminal", response_class=HTMLResponse)
async def api_launch_terminal(request: Request):
    body = await request.json()
    config = load_config()
    cwd = _resolve_launch_cwd(body.get("workspace", ""), config)
    profile = get_active_launch_profile(config)
    result = launcher.launch_terminal(cwd, launch_profile=profile)
    if not result.success:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": result.error, "level": "error",
        })
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": f"Terminal opened: {Path(cwd).name}", "level": "success",
    })


@app.post("/api/pin-session")
async def pin_session(request: Request):
    body = await request.json()
    session_id = body["session_id"]
    config = load_config()
    if session_id not in config.pinned_sessions:
        config.pinned_sessions.append(session_id)
        save_config(config)
    return {"ok": True}


@app.post("/api/pin-folder")
async def pin_folder(request: Request):
    body = await request.json()
    folder = body["folder"]
    config = load_config()
    if folder not in config.pinned_folders:
        config.pinned_folders.append(folder)
        save_config(config)
    return {"ok": True}


@app.post("/api/unpin-folder")
async def unpin_folder(request: Request):
    body = await request.json()
    folder = body["folder"]
    config = load_config()
    if folder in config.pinned_folders:
        config.pinned_folders.remove(folder)
        save_config(config)
    return {"ok": True}


@app.post("/api/unpin-session")
async def unpin_session(request: Request):
    body = await request.json()
    session_id = body["session_id"]
    config = load_config()
    if session_id in config.pinned_sessions:
        config.pinned_sessions.remove(session_id)
        save_config(config)
    return {"ok": True}


def _poll_statuses(snapshot, cwds: list[str], poll_providers: set[str],
                   active_norm_cwds: set[str]) -> tuple[dict, dict, list]:
    """Compute the per-session and per-workspace status maps for one poll tick.

    Runs whole in a single worker thread. Every branch below can reach the
    filesystem — ``_session_status`` stats a JSONL file and ``_workspace_status``
    falls back to loading session files on a cache miss — so a per-cwd hop would
    cost one serialized thread round-trip per visible workspace on a 5s timer.
    Iterating in ``cwds`` order also keeps the response identical to the
    caller's request order.
    """
    from .data import _normalize_path

    sessions_map: dict[str, str] = {}
    workspaces_map: dict[str, str] = {}
    active_cwds: list[str] = []

    for cwd in cwds:
        norm = _normalize_path(cwd)
        # Short-circuit: if no live process in this cwd, everything is closed
        if norm not in active_norm_cwds:
            workspaces_map[cwd] = "closed"
            continue

        try:
            active_cwds.append(cwd)
            # Compute per-session status (no notifications)
            for provider in poll_providers:
                cached_sessions = data.session_cache.get(cwd, provider)
                if cached_sessions is None:
                    continue
                for session in cached_sessions:
                    status = _session_status(
                        snapshot, session, provider, notify=False
                    )
                    if status != "closed":
                        sessions_map[session.session_id] = status

            # Card-level dot comes from the same aggregator the server renders
            # with, so a poll cannot contradict the last full render.
            workspaces_map[cwd] = _workspace_status(snapshot, cwd, poll_providers)
        except Exception:
            log.debug("Status poll failed for cwd %s", cwd)
            workspaces_map[cwd] = "closed"

    return sessions_map, workspaces_map, active_cwds


@app.post("/api/session-status")
async def api_session_status(request: Request):
    """Lightweight status endpoint for background polling.

    Accepts {"cwds": [...], "provider": "..."} and returns per-session and
    per-workspace status without triggering notification side-effects.
    Short-circuits cwds that have no live process.

    ``provider`` is the filter the page currently has applied; it must reach
    the status computation, or a poll answers about providers the render
    deliberately excluded. Absent or "all" means every CLI provider, which
    also keeps older clients working.
    """
    body = await request.json()
    cwds = body.get("cwds", [])
    if not cwds:
        return JSONResponse({"sessions": {}, "workspaces": {}, "active_cwds": []})

    provider_filter = body.get("provider")
    # A JSON value of any other shape would land in a set below and raise
    # (an array is unhashable), so anything that is not a usable name is
    # treated as absent — the same answer an older client without the field
    # gets. "all" narrows to the CLI providers, which is exactly what the
    # render path's ``None`` resolves to. A "kiro-ide" filter is passed
    # through and reports every cwd closed, because presence tracks no
    # kiro-ide process — again the dot the render draws.
    if not isinstance(provider_filter, str) or not provider_filter:
        provider_filter = "all"
    poll_providers = ({"kiro-cli", "claude-code"} if provider_filter == "all"
                      else {provider_filter})

    # Get cached presence snapshot (3s TTL — very fast)
    snapshot = await asyncio.to_thread(presence.get_snapshot)
    active_norm_cwds = snapshot.live_cwds(poll_providers)

    sessions_map, workspaces_map, active_cwds = await asyncio.to_thread(
        _poll_statuses, snapshot, cwds, poll_providers, active_norm_cwds)

    return JSONResponse({
        "sessions": sessions_map,
        "workspaces": workspaces_map,
        "active_cwds": active_cwds,
    })


@app.get("/partials/workspaces", response_class=HTMLResponse)
async def partials_workspaces(
    request: Request,
    provider: str = "all",
    tag: str = "",
    time_filter: str = "",
    status: str = "",
    fresh: int = 0,
):
    """Render all workspaces: pinned at top (alphabetical), non-pinned below (by recency)."""
    import asyncio
    import time
    t0 = time.perf_counter()
    if fresh:
        data._cache.pop("workspaces_with_counts:all", None)
    try:
        workspace_data = await asyncio.to_thread(
            data.discover_workspaces_with_counts,
            provider=None,
        )
        log.info("Discovered %d workspaces in %.2fs", len(workspace_data), time.perf_counter() - t0)
    except Exception:
        log.exception("Failed to discover workspaces")
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Error: could not load session data",
            "level": "error",
        })

    config = load_config()

    from .data import _normalize_path
    from .config import get_workspace_settings
    workspace_data = list(workspace_data)

    cards_html = ""
    # Build pinned set by normalized path only (workspace-level)
    pinned_norm_paths: set[str] = set()
    for folder in config.pinned_folders:
        pinned_norm_paths.add(_normalize_path(folder))

    # --- Pinned workspaces (at top, sorted alphabetically) ---
    pinned_data = [(c, n, u, p) for c, n, u, p in workspace_data if _normalize_path(c) in pinned_norm_paths]
    # Add pinned folders not found in discovery results (so they show even with 0 sessions)
    all_existing_norms = {_normalize_path(c) for c, _, _, _ in workspace_data}
    for folder in config.pinned_folders:
        if _normalize_path(folder) not in all_existing_norms:
            pinned_data.append((folder, 0, "", ""))
    # Filter out disabled providers (keep entries with empty provider for zero-session pinned folders)
    pinned_data = [(c, n, u, p) for c, n, u, p in pinned_data if not p or _enabled(config, p)]
    pinned_grouped = _group_workspaces(pinned_data, config)
    if provider != "all":
        pinned_grouped = [g for g in pinned_grouped if any(prov["name"] == provider for prov in g["providers"])]
    pinned_grouped.sort(key=lambda x: x["folder_name"].lower())

    # --- Non-pinned workspaces (by recency) ---
    other_data = [(c, n, u, p) for c, n, u, p in workspace_data if _normalize_path(c) not in pinned_norm_paths]
    other_data = [(c, n, u, p) for c, n, u, p in other_data if _enabled(config, p)]
    other_grouped = _group_workspaces(other_data, config)
    if provider != "all":
        other_grouped = [g for g in other_grouped if any(prov["name"] == provider for prov in g["providers"])]

    # --- Tag filtering (applies uniformly to both pinned and other) ---
    if not tag:
        # Default: exclude workspaces tagged "hidden"
        pinned_grouped = [g for g in pinned_grouped if "hidden" not in get_workspace_settings(config, g["cwd"])["tags"]]
        other_grouped = [g for g in other_grouped if "hidden" not in get_workspace_settings(config, g["cwd"])["tags"]]
    elif tag == "hidden":
        pinned_grouped = [g for g in pinned_grouped if "hidden" in get_workspace_settings(config, g["cwd"])["tags"]]
        other_grouped = [g for g in other_grouped if "hidden" in get_workspace_settings(config, g["cwd"])["tags"]]
    else:
        pinned_grouped = [g for g in pinned_grouped if tag in get_workspace_settings(config, g["cwd"])["tags"]]
        other_grouped = [g for g in other_grouped if tag in get_workspace_settings(config, g["cwd"])["tags"]]

    # --- Time filter ---
    if time_filter:
        pinned_grouped = [g for g in pinned_grouped if _time_bucket(g["latest_updated"]) == time_filter]
        other_grouped = [g for g in other_grouped if _time_bucket(g["latest_updated"]) == time_filter]

    # --- Live-status filter (cwd-level; shows cards containing matching activity) ---
    snap = await asyncio.to_thread(presence.get_snapshot)
    prov_names = None if provider == "all" else {provider}

    if status and status != "all":
        def _ws_status_keep(g):
            return _status_matches(status, _workspace_status(
                snap, g["cwd"], prov_names))

        pinned_grouped = [g for g in pinned_grouped if _ws_status_keep(g)]
        other_grouped = [g for g in other_grouped if _ws_status_keep(g)]

    # --- Render: pinned first, then time-grouped non-pinned ---
    hover_launchers = _all_hover_launchers(config)
    for group in pinned_grouped:
        cwd = group["cwd"]
        stale = not Path(cwd).exists()
        if provider != "all":
            session_count = sum(p["count"] for p in group["providers"] if p["name"] == provider)
        else:
            session_count = group["total_count"]
        workspace_color = _resolve_workspace_color(cwd, config)
        ws_status = _workspace_status(snap, cwd, prov_names)
        cards_html += templates.get_template("partials/workspace_card.html").render(
            request=request, cwd=cwd, sessions=[], stale=stale,
            pinned_sessions=config.pinned_sessions, folder_name=group["folder_name"],
            session_count=session_count, is_pinned=True,
            last_updated=group["latest_updated"],
            workspace_color=workspace_color,
            providers=group["providers"],
            workspace_status=ws_status,
            time_group="pinned",
            hover_launchers=hover_launchers,
        )

    if pinned_grouped and other_grouped:
        cards_html += '<div class="pinned-separator" aria-hidden="true"></div>'

    # Time-group non-pinned workspaces
    time_groups: dict[str, list[dict]] = {"today": [], "yesterday": [], "this_week": [], "before": []}
    for ws in other_grouped:
        bucket = _time_bucket(ws["latest_updated"])
        time_groups[bucket].append(ws)
    time_labels = {"today": "Today", "yesterday": "Yesterday", "this_week": "This week", "before": "Older"}
    for key in ["today", "yesterday", "this_week", "before"]:
        if time_groups[key]:
            cards_html += f'<div class="group-heading">{time_labels[key]}</div>'
            for group in time_groups[key]:
                cwd = group["cwd"]
                stale = not Path(cwd).exists()
                if provider != "all":
                    session_count = sum(p["count"] for p in group["providers"] if p["name"] == provider)
                else:
                    session_count = group["total_count"]
                workspace_color = _resolve_workspace_color(cwd, config)
                ws_status = _workspace_status(snap, cwd, prov_names)
                cards_html += templates.get_template("partials/workspace_card.html").render(
                    request=request, cwd=cwd, sessions=[], stale=stale,
                    pinned_sessions=config.pinned_sessions, folder_name=group["folder_name"],
                    session_count=session_count, is_pinned=False,
                    last_updated=group["latest_updated"],
                    workspace_color=workspace_color,
                    providers=group["providers"],
                    workspace_status=ws_status,
                    time_group=key,
                    hover_launchers=hover_launchers,
                )

    if not cards_html:
        if status and status != "all":
            cards_html += f'<div class="empty-state">No {html_mod.escape(status)} workspaces right now.</div>'
        elif tag:
            cards_html += f'<div class="empty-state">No workspaces with tag &quot;{html_mod.escape(tag)}&quot;</div>'
        elif time_filter:
            cards_html += f'<div class="empty-state">No workspaces active {html_mod.escape(time_filter.replace("_", " "))}</div>'
        elif provider != "all" and provider:
            empty_msgs = {
                "claude-code": "No Claude Code sessions found \u2014 start one with <code>claude</code> to see it here.",
                "kiro-cli": "No Kiro CLI sessions found \u2014 start one with <code>kiro-cli</code> to see it here.",
                "kiro-ide": "No Kiro IDE sessions found \u2014 open a folder in Kiro IDE and start a conversation to see it here.",
            }
            msg = empty_msgs.get(provider, f"No {provider} sessions found.")
            cards_html += f'<div class="empty-state">{msg}</div>'
        else:
            cards_html += '<div class="empty-state">No workspaces found yet.</div>'

    log.info("Rendered workspace cards in %.2fs total", time.perf_counter() - t0)
    return HTMLResponse(cards_html)


@app.get("/partials/all-sessions", response_class=HTMLResponse)
async def partials_all_sessions(request: Request, page: int = 1, provider: str = "all",
                                q: str = "", tag: str = "", time_filter: str = "", status: str = ""):
    """Render paginated all-sessions panel. Pinned at top, then time-grouped."""
    from .config import get_workspace_settings

    config = load_config()

    enabled = {p for p in data.PROVIDERS if _enabled(config, p)}
    prov_filter = None if provider == "all" else provider

    sessions_with_prov, has_more = await asyncio.to_thread(
        data.get_all_sessions_paginated,
        page=page,
        page_size=20,
        provider=prov_filter,
        pinned_sessions=config.pinned_sessions,
        enabled_providers=enabled,
    )

    # Apply search filter if q provided
    if q:
        query = q.strip().lower()
        sessions_with_prov = [
            (s, p) for s, p in sessions_with_prov
            if query in (s.title or "").lower()
            or query in (s.first_prompt or "").lower()
            or query in (s.cwd or "").lower()
        ]
        has_more = False  # Search disables pagination

    # Tag filtering — build workspace-tags lookup
    cwd_tags_cache: dict[str, list[str]] = {}

    def _get_ws_tags(cwd: str) -> list[str]:
        if not cwd:
            return []
        if cwd not in cwd_tags_cache:
            cwd_tags_cache[cwd] = get_workspace_settings(config, cwd)["tags"]
        return cwd_tags_cache[cwd]

    if tag:
        if tag == "hidden":
            sessions_with_prov = [(s, p) for s, p in sessions_with_prov if "hidden" in _get_ws_tags(s.cwd)]
        else:
            sessions_with_prov = [(s, p) for s, p in sessions_with_prov if tag in _get_ws_tags(s.cwd)]
        has_more = False
    else:
        # Default: exclude sessions from hidden workspaces
        sessions_with_prov = [(s, p) for s, p in sessions_with_prov if "hidden" not in _get_ws_tags(s.cwd)]

    # Time filter
    if time_filter:
        sessions_with_prov = [
            (s, p) for s, p in sessions_with_prov
            if _time_bucket(s.updated_at) == time_filter
        ]
        has_more = False

    # Live-status: annotate every row (for dots) and optionally filter.
    snap = await asyncio.to_thread(presence.get_snapshot)
    _notif_enabled = config.notifications.get("enabled", False)
    row_status = {(s.session_id, p): _session_status(snap, s, p, _notif_enabled) for s, p in sessions_with_prov}
    if status and status != "all":
        sessions_with_prov = [
            (s, p) for s, p in sessions_with_prov
            if _status_matches(status, row_status[(s.session_id, p)])
        ]
        has_more = False  # status filter operates on the loaded page only

    # Split pinned from non-pinned
    pinned_set = set(config.pinned_sessions)
    pinned_items = [(s, p) for s, p in sessions_with_prov if s.session_id in pinned_set]
    non_pinned = [(s, p) for s, p in sessions_with_prov if s.session_id not in pinned_set]

    # Exclude pinned from page > 1 (already shown on page 1)
    if page > 1:
        pinned_items = []

    html = ""

    # Render pinned section
    for session, prov_name in pinned_items:
        html += templates.get_template("partials/session_row.html").render(
            request=request, session=session, cwd=session.cwd,
            stale=not Path(session.cwd).exists(),
            pinned_sessions=config.pinned_sessions,
            provider_name=prov_name,
            provider_color=_get_provider_color(prov_name, config),
            show_workspace=True,
            workspace_name=Path(session.cwd).name if session.cwd else "",
            status=(_rs := row_status.get((session.session_id, prov_name), "closed")),
            waiting_detail=_waiting_detail(snap, session, prov_name, _rs),
            origin=_row_origin(snap, session, prov_name),
        )
    if pinned_items and non_pinned:
        html += '<div class="pinned-separator" aria-hidden="true"></div>'

    # Render time-grouped non-pinned
    time_groups: dict[str, list] = {"today": [], "yesterday": [], "this_week": [], "before": []}
    for s, p in non_pinned:
        bucket = _time_bucket(s.updated_at)
        time_groups[bucket].append((s, p))
    time_labels = {"today": "Today", "yesterday": "Yesterday", "this_week": "This week", "before": "Older"}
    for key in ["today", "yesterday", "this_week", "before"]:
        if time_groups[key]:
            html += f'<div class="group-heading">{time_labels[key]}</div>'
            for session, prov_name in time_groups[key]:
                html += templates.get_template("partials/session_row.html").render(
                    request=request, session=session, cwd=session.cwd,
                    stale=not Path(session.cwd).exists(),
                    pinned_sessions=config.pinned_sessions,
                    provider_name=prov_name,
                    provider_color=_get_provider_color(prov_name, config),
                    show_workspace=True,
                    workspace_name=Path(session.cwd).name if session.cwd else "",
                    status=(_rs := row_status.get((session.session_id, prov_name), "closed")),
                    waiting_detail=_waiting_detail(snap, session, prov_name, _rs),
            origin=_row_origin(snap, session, prov_name),
                )

    if not html:
        if status and status != "all":
            html = f'<div class="empty-state">No {html_mod.escape(status)} sessions right now.</div>'
        elif tag:
            html = f'<div class="empty-state">No sessions in workspaces tagged &quot;{html_mod.escape(tag)}&quot;</div>'
        elif time_filter:
            html = f'<div class="empty-state">No sessions active {html_mod.escape(time_filter.replace("_", " "))}</div>'
        else:
            html = '<div class="empty-state">No sessions found.</div>'

    if has_more:
        next_page = page + 1
        html += f'<button class="load-more-btn" onclick="loadMoreSessions({next_page})">Load more</button>'

    # Mark initialization after first render (prevents startup notification burst)
    global _first_render_done
    if not _first_render_done:
        _first_render_done = True
        notifications.mark_initialized()

    return HTMLResponse(html)


@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = "", provider: str = "all",
                 tag: str = "", time_filter: str = "", status: str = ""):
    query = q.strip().lower()
    if not query:
        return await partials_workspaces(request, provider=provider, tag=tag,
                                         time_filter=time_filter, status=status)

    import asyncio
    try:
        workspace_data = await asyncio.to_thread(data.discover_workspaces_with_counts)
    except Exception:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Error: could not load session data",
            "level": "error",
        })

    config = load_config()
    matched = [(c, n, u, p) for c, n, u, p in workspace_data if query in c.lower()]
    # Filter out disabled providers
    matched = [(c, n, u, p) for c, n, u, p in matched if _enabled(config, p)]
    query_matched = bool(matched)

    from .data import _normalize_path
    from .config import get_workspace_settings
    pinned_norm_paths: set[str] = set()
    for folder in config.pinned_folders:
        pinned_norm_paths.add(_normalize_path(folder))

    # Filter by provider if specified
    if provider != "all":
        matched = [(c, n, u, p) for c, n, u, p in matched if p == provider]

    # Group matched workspaces
    grouped = _group_workspaces(matched, config)
    if provider != "all":
        grouped = [g for g in grouped if any(prov["name"] == provider for prov in g["providers"])]

    # Apply tag filtering
    if not tag:
        grouped = [g for g in grouped if "hidden" not in get_workspace_settings(config, g["cwd"])["tags"]]
    elif tag == "hidden":
        grouped = [g for g in grouped if "hidden" in get_workspace_settings(config, g["cwd"])["tags"]]
    else:
        grouped = [g for g in grouped if tag in get_workspace_settings(config, g["cwd"])["tags"]]

    # Apply time filter
    if time_filter:
        grouped = [g for g in grouped if _time_bucket(g["latest_updated"]) == time_filter]

    snap = await asyncio.to_thread(presence.get_snapshot)
    prov_names = None if provider == "all" else {provider}

    # Apply live-status filter (skipped when nothing survived the earlier
    # ones — the presence scan is the expensive step and cannot change an
    # already-empty result).
    if grouped and status and status != "all":
        grouped = [g for g in grouped if _status_matches(
            status, _workspace_status(snap, g["cwd"], prov_names))]

    cards_html = ""

    hover_launchers = _all_hover_launchers(config)

    # Separate pinned from non-pinned results
    pinned_results = [g for g in grouped if _normalize_path(g["cwd"]) in pinned_norm_paths]
    other_results = [g for g in grouped if _normalize_path(g["cwd"]) not in pinned_norm_paths]

    # Render pinned results at top
    for group in pinned_results:
        cwd = group["cwd"]
        stale = not Path(cwd).exists()
        workspace_color = _resolve_workspace_color(cwd, config)
        ws_status = _workspace_status(snap, cwd, prov_names)
        cards_html += templates.get_template("partials/workspace_card.html").render(
            request=request, cwd=cwd, sessions=[], stale=stale,
            pinned_sessions=config.pinned_sessions, folder_name=group["folder_name"],
            session_count=group["total_count"], last_updated=group["latest_updated"],
            is_pinned=True,
            workspace_color=workspace_color,
            providers=group["providers"],
            workspace_status=ws_status,
            time_group="pinned",
            hover_launchers=hover_launchers,
        )

    if pinned_results and other_results:
        cards_html += '<div class="pinned-separator" aria-hidden="true"></div>'

    # Time-group non-pinned results
    time_groups: dict[str, list[dict]] = {"today": [], "yesterday": [], "this_week": [], "before": []}
    for ws in other_results:
        bucket = _time_bucket(ws["latest_updated"])
        time_groups[bucket].append(ws)
    time_labels = {"today": "Today", "yesterday": "Yesterday", "this_week": "This week", "before": "Older"}
    for key in ["today", "yesterday", "this_week", "before"]:
        if time_groups[key]:
            cards_html += f'<div class="group-heading">{time_labels[key]}</div>'
            for group in time_groups[key]:
                cwd = group["cwd"]
                stale = not Path(cwd).exists()
                workspace_color = _resolve_workspace_color(cwd, config)
                ws_status = _workspace_status(snap, cwd, prov_names)
                cards_html += templates.get_template("partials/workspace_card.html").render(
                    request=request, cwd=cwd, sessions=[], stale=stale,
                    pinned_sessions=config.pinned_sessions, folder_name=group["folder_name"],
                    session_count=group["total_count"], last_updated=group["latest_updated"],
                    is_pinned=False,
                    workspace_color=workspace_color,
                    providers=group["providers"],
                    workspace_status=ws_status,
                    time_group=key,
                    hover_launchers=hover_launchers,
                )

    if not cards_html:
        # One cascade decides the wording for every empty outcome, so two
        # routes to an empty page cannot word themselves differently.
        # The query branch comes first and the distinction it draws is
        # deliberate: when the query matched nothing, no filter is at fault,
        # and naming one would send the user to clear a filter that changes
        # nothing. Past that, a filter removed every match — name the filter
        # rather than the query. Branch order mirrors partials_workspaces; the
        # provider wording does not, because here the truth is "the query
        # matched nothing for that provider", not "you have no sessions for
        # that provider at all".
        if not query_matched:
            message = f'No results for "{q}"'
        elif status and status != "all":
            message = f"No {status} workspaces right now."
        elif tag:
            message = f'No workspaces with tag "{tag}"'
        elif time_filter:
            message = f'No workspaces active {time_filter.replace("_", " ")}'
        elif provider != "all" and provider:
            display = PROVIDER_DISPLAY_NAMES.get(provider, provider)
            message = f'No {display} results for "{q}"'
        else:
            message = f'No results for "{q}"'
        return templates.TemplateResponse(request, "partials/empty_state.html", {
            "message": message,
        })

    return HTMLResponse(cards_html)


@app.post("/api/refresh")
async def api_refresh():
    import asyncio
    data.session_cache.clear()
    data._cache.clear()
    config = load_config()
    pinned_paths = list(config.pinned_folders)
    await asyncio.to_thread(data.warmup_all, pinned_paths, config.pinned_sessions)
    return {"last_refresh": data.session_cache.last_refresh}


@app.get("/api/last-refresh")
async def api_last_refresh():
    return {"last_refresh": data.session_cache.last_refresh}


@app.get("/api/warmup-status")
async def api_warmup_status():
    return {"ready": data.warmup_done.is_set()}


@app.get("/api/settings")
async def api_settings():
    config = load_config()
    try:
        autostart_enabled = autostart.is_enabled()
    except Exception:
        autostart_enabled = False
    return {
        "active_launch_profile": config.active_launch_profile,
        "launch_profiles": [asdict(p) for p in config.launch_profiles],
        "peek_hotkey": config.peek_hotkey,
        "port": config.port,
        "default_directory": config.default_directory,
        "provider_settings": config.provider_settings,
        "custom_launchers": _launchers_without_env(config.custom_launchers),
        "autostart": autostart_enabled,
        "acp_max_sessions": config.acp_max_sessions,
        "acp_idle_ttl_seconds": config.acp_idle_ttl_seconds,
        "acp_prompt_silence_seconds": config.acp_prompt_silence_seconds,
        # The address only; the secret is served by `/api/remote-access` alone.
        "remote_bind_address": config.remote_bind_address,
        # Which keys are restart-only. A property of the setting, unchanging.
        "restart_to_apply": sorted(_RESTART_TO_APPLY),
        # What this process is actually running, for those same keys, and which
        # of them the stored config no longer agrees with. The two are separate
        # because the panel needs both: the value in force to display, and the
        # disagreement to badge. `in_force` is `{}` when no snapshot was taken,
        # in which case `restart_pending` lists everything — see
        # `_STARTUP_VALUES` for why that direction is the safe one.
        "in_force": dict(_STARTUP_VALUES or {}),
        "restart_pending": _restart_pending(config),
    }


@app.get("/api/available-providers")
async def api_available_providers():
    """Return list of available (enabled) providers with display names and colors."""
    providers = sorted(data.available_providers())
    config = load_config()
    providers = [p for p in providers if _enabled(config, p)]
    return [{"name": p, "display": PROVIDER_DISPLAY_NAMES.get(p, p), "color": _get_provider_color(p, config)} for p in providers]


@app.get("/api/tags")
async def api_tags():
    """Return all known tags with colors and workspace counts."""
    config = load_config()
    tag_counts: dict[str, int] = {}
    for ws in config.workspace_settings.values():
        for t in ws.get("tags", []):
            tag_counts[t] = tag_counts.get(t, 0) + 1
    for t in config.tag_settings:
        tag_counts.setdefault(t, 0)
    return [
        {"name": t, "color": config.tag_settings.get(t, {}).get("color", ""), "count": c}
        for t, c in sorted(tag_counts.items())
    ]


@app.post("/api/tag/save", response_class=HTMLResponse)
async def save_tag_settings(request: Request):
    """Save tag color settings."""
    body = await request.json()
    tag_name = body.get("tag", "")
    color = body.get("color", "")
    # Validation: tag_name max 64 chars, no control chars
    if not tag_name or len(tag_name) > 64 or any(ord(ch) < 0x20 for ch in tag_name):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Invalid tag name", "level": "error"})
    if color and (len(color) > 20 or any(ord(ch) < 0x20 for ch in color)):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Invalid color value", "level": "error"})
    config = load_config()
    config.tag_settings[tag_name] = {"color": color}
    save_config(config)
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": "Tag color saved", "level": "success",
    })


@app.post("/api/tag/delete", response_class=HTMLResponse)
async def delete_tag(request: Request):
    """Globally delete a tag from tag_settings and all workspace assignments."""
    body = await request.json()
    tag_name = body.get("tag", "")
    if not isinstance(tag_name, str) or not tag_name or len(tag_name) > 64 or any(ord(ch) < 0x20 for ch in tag_name):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Invalid tag name", "level": "error"})
    if tag_name == "hidden":
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Cannot delete the 'hidden' tag", "level": "error"})
    config = load_config()
    removed_from_settings = config.tag_settings.pop(tag_name, None) is not None
    affected = 0
    for ws in config.workspace_settings.values():
        tags = ws.get("tags", [])
        if tag_name in tags:
            ws["tags"] = [t for t in tags if t != tag_name]
            affected += 1
    if not removed_from_settings and affected == 0:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": f"Tag '{tag_name}' not found", "level": "success"})
    save_config(config)
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": f"Tag '{tag_name}' deleted from {affected} workspace(s)",
        "level": "success",
    })


@app.get("/api/provider/{key}")
async def get_provider_settings(key: str):
    if key not in data.PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    config = load_config()
    settings = config.provider_settings.get(key, {"default_args": "", "color": "", "enabled": True, "default_directory": ""})
    settings.setdefault("default_directory", "")
    settings.setdefault("show_in_workspace_hover", True)
    return {"provider": key, **settings}


@app.post("/api/provider/save", response_class=HTMLResponse)
async def save_provider_settings(request: Request):
    body = await request.json()
    provider = body.get("provider", "")
    if not provider:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Missing provider key", "level": "error",
        })
    # Validate default_args: max 256 chars, no control characters
    default_args = body.get("default_args", "")
    if len(default_args) > 256:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Default args too long (max 256 chars)", "level": "error",
        })
    if any(ord(ch) < 0x20 for ch in default_args):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Default args contains invalid control characters", "level": "error",
        })
    # Validate default_directory: max 512 chars, no control characters
    default_directory = body.get("default_directory", "")
    if len(default_directory) > 512:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Working directory too long (max 512 chars)", "level": "error",
        })
    if any(ord(ch) < 0x20 for ch in default_directory):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Working directory contains invalid control characters", "level": "error",
        })
    config = load_config()
    config.provider_settings[provider] = {
        "default_args": default_args,
        "color": body.get("color", ""),
        "enabled": body.get("enabled", True),
        "default_directory": default_directory,
        "show_in_workspace_hover": body.get("show_in_workspace_hover", True),
    }
    save_config(config)
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": f"Provider settings saved", "level": "success",
    })


# --- Launch profile validation constants ---
_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _has_control_chars(s: str) -> bool:
    """Return True if string contains characters < 0x20."""
    return any(ord(ch) < 0x20 for ch in s)


@app.post("/api/launch-profile/activate")
async def activate_launch_profile(request: Request):
    body = await request.json()
    profile_id = body.get("id", "")
    config = load_config()
    if not any(p.id == profile_id for p in config.launch_profiles):
        return JSONResponse({"ok": False, "error": "Profile not found"}, status_code=404)
    config.active_launch_profile = profile_id
    save_config(config)
    return {"ok": True}


@app.post("/api/launch-profile/save", response_class=HTMLResponse)
async def save_launch_profile(request: Request):
    body = await request.json()
    config = load_config()

    # Validate profile ID
    profile_id = body.get("id", "")
    is_new = profile_id == "__new__" or not profile_id
    if is_new:
        profile_id = str(uuid.uuid4()).replace("-", "")[:16]
    elif not _PROFILE_ID_RE.match(profile_id):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Invalid profile ID format", "level": "error",
        })

    # Validate name
    name = str(body.get("name", "")).strip()
    if not name or len(name) > 80:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Name must be 1-80 characters", "level": "error",
        })
    if _has_control_chars(name):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Name contains invalid control characters", "level": "error",
        })

    # Validate terminal_command
    terminal_command = str(body.get("terminal_command", ""))
    if len(terminal_command) > 512:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Terminal command too long (max 512 chars)", "level": "error",
        })
    if _has_control_chars(terminal_command):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Terminal command contains invalid control characters", "level": "error",
        })

    # Validate wt_profile
    wt_profile = str(body.get("wt_profile", "PowerShell")).strip()
    if len(wt_profile) > 128:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "WT Profile too long (max 128 chars)", "level": "error",
        })
    if _has_control_chars(wt_profile):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "WT Profile contains invalid control characters", "level": "error",
        })

    # Build the validated profile
    new_profile = LaunchProfile(
        id=profile_id,
        name=name,
        terminal_command=terminal_command,
        wt_profile=wt_profile or "PowerShell",
    )

    # Check if updating existing or creating new
    existing_idx = next((i for i, p in enumerate(config.launch_profiles) if p.id == profile_id), None)
    if existing_idx is not None:
        config.launch_profiles[existing_idx] = new_profile
    elif is_new:
        # Check for duplicate ID (shouldn't happen with generated IDs but be safe)
        if any(p.id == profile_id for p in config.launch_profiles):
            return templates.TemplateResponse(request, "partials/toast.html", {
                "message": "Duplicate profile ID", "level": "error",
            })
        config.launch_profiles.append(new_profile)
    else:
        # New ID that doesn't exist yet — create
        config.launch_profiles.append(new_profile)

    save_config(config)
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": "Profile saved", "level": "success",
    })


@app.post("/api/launch-profile/delete", response_class=HTMLResponse)
async def delete_launch_profile(request: Request):
    body = await request.json()
    profile_id = body.get("id", "")
    config = load_config()

    # Reject deleting the last profile
    if len(config.launch_profiles) <= 1:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Cannot delete the last profile", "level": "error",
        })

    # Check profile exists
    if not any(p.id == profile_id for p in config.launch_profiles):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Profile not found", "level": "error",
        })

    # Remove the profile
    config.launch_profiles = [p for p in config.launch_profiles if p.id != profile_id]

    # Reassign active if deleted was active
    if config.active_launch_profile == profile_id:
        config.active_launch_profile = config.launch_profiles[0].id

    save_config(config)
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": "Profile deleted", "level": "success",
    })


_SETTING_TYPES: dict[str, type] = {
    "port": int,
    "peek_hotkey": str,
    "default_directory": str,
    "pinned_folders": list,
    "pinned_sessions": list,
    # Read once at startup by design (`acp.apply_config`), so the settings UI
    # must say **restart to apply** — see `_RESTART_TO_APPLY` below.
    "acp_max_sessions": int,
    "acp_idle_ttl_seconds": int,
    "acp_prompt_silence_seconds": int,
    "remote_bind_address": str,
}

# Inclusive integer bounds, enforced on the write path only. `load_config` is
# documented as never raising and runs on the event loop, so the bounds cannot
# live there; `acp.apply_config` separately logs-and-ignores an out-of-range
# hand-edit rather than clamping it. Adding a key to `_SETTING_TYPES` without a
# bound here is what would turn Phase 2's fail-closed `Unknown setting` refusal
# into an unbounded write.
_SETTING_BOUNDS: dict[str, tuple[int, int]] = {
    "acp_max_sessions": (1, 16),
    "acp_idle_ttl_seconds": (300, 86400),
    "acp_prompt_silence_seconds": (60, 7200),
}

# Keys whose value is read exactly once at startup. Returned to the settings UI
# so it can say so, rather than appearing to take effect and silently doing
# nothing until the next launch.
_RESTART_TO_APPLY = frozenset({
    "port", "acp_max_sessions", "acp_idle_ttl_seconds",
    "acp_prompt_silence_seconds", "remote_bind_address",
    # `peek_hotkey` is consumed once, at startup, by
    # `create_peek(server_url, config.peek_hotkey)`; `PeekWindow.__init__`
    # parses it into `self._trigger_keys` and nothing re-reads or re-registers
    # it afterwards. `index.html` offers a live input for it, so omitting it
    # here made the endpoint answer `restart_required: False` for a key that
    # genuinely needs one — a field that is positively wrong is worse than no
    # field, because the user acts on it.
    "peek_hotkey",
})

# The restart-only values as this process actually read them, captured once
# before the app serves anything. Everything else the settings endpoint returns
# is `config.X` — the value **on disk** — and for a restart-only key those two
# are the same only until someone edits one. Without this snapshot the panel
# that exists to say what is in force was rendering the stored value instead,
# so changing `acp_max_sessions` from 8 to 12 made it report 12 while the
# running app was still capping at 8.
#
# `None` means never captured, which is not the same as "nothing pending" and
# must not be reported as such: a test importing the app, or an entry point
# that does not call the setter, would otherwise have the page conclude every
# value is live. That case falls back to the old unconditional behaviour —
# every restart-only key reads as pending — because over-warning is the safe
# direction here and under-warning is the bug this whole block is about.
_STARTUP_VALUES: dict | None = None


def set_startup_config(config) -> None:
    """Record the restart-only values this process started with.

    A startup setter mirroring `set_remote_host` and `acp.apply_config`, called
    from `__main__` beside the latter. Snapshots by value rather than holding
    the config object, which `save_config` rewrites in place on any settings
    change — a held reference would track the edits and report every value as
    in force, which is precisely the failure it exists to prevent.
    """
    global _STARTUP_VALUES
    _STARTUP_VALUES = {key: getattr(config, key, None)
                       for key in _RESTART_TO_APPLY}
    log.info("startup snapshot of restart-only settings: %s", _STARTUP_VALUES)


def _restart_pending(config) -> list:
    """Which restart-only keys hold a stored value the process is not running.

    Falls back to *every* restart-only key when no snapshot was taken — see
    `_STARTUP_VALUES`. Comparison is by equality on the loaded value, so a
    config rewritten with an identical value does not read as pending.
    """
    if _STARTUP_VALUES is None:
        return sorted(_RESTART_TO_APPLY)
    return sorted(key for key in _RESTART_TO_APPLY
                  if getattr(config, key, None) != _STARTUP_VALUES.get(key))


@app.post("/api/save-setting")
async def save_setting(request: Request):
    body = await request.json()
    key = body.get("key")
    value = body.get("value")
    if key is None or value is None:
        return {"ok": False, "error": "Missing required field"}
    expected_type = _SETTING_TYPES.get(key)
    if expected_type is None:
        return {"ok": False, "error": f"Unknown setting: {key}"}
    if isinstance(value, bool):
        # Python: isinstance(True, int) is True; reject booleans before the int check
        return {"ok": False, "error": f"Invalid type for {key}"}
    if not isinstance(value, expected_type):
        return {"ok": False, "error": f"Invalid type for {key}"}
    if expected_type is list and not all(isinstance(x, str) for x in value):
        return {"ok": False, "error": f"All elements of {key} must be strings"}
    # Port-specific range validation
    if key == "port":
        if value != 0 and not (1024 <= value <= 65535):
            return {"ok": False, "error": "Port must be 0 (random) or 1024\u201365535"}
    bounds = _SETTING_BOUNDS.get(key)
    if bounds is not None and not (bounds[0] <= value <= bounds[1]):
        return {"ok": False,
                "error": f"{key} must be between {bounds[0]} and {bounds[1]}"}
    # String-specific validation (applies to peek_hotkey, default_directory)
    if expected_type is str:
        if len(value) > 512:
            return {"ok": False, "error": f"{key} too long (max 512 chars)"}
        if any(ord(ch) < 0x20 for ch in value):
            return {"ok": False, "error": f"{key} contains invalid control characters"}
    config = load_config()
    if key == "remote_bind_address":
        # The named error SC-3b asks for, on the write path. `load_config`
        # sanitises the same value to "" and logs, because it may not raise;
        # here the user is told why, before the value is ever persisted.
        reason = validate_remote_bind_address(value, config.port)
        if reason:
            return {"ok": False, "error": reason}
        # Strip **before** the branch, not inside it. Nested under
        # `if value.strip():` the assignment never ran for a whitespace-only
        # value, so `"   "` was persisted verbatim into config.toml.
        # `load_config` strips it again on read, so the effect was cosmetic —
        # but the stored value disagreed with the effective one, which is the
        # kind of gap a later reader resolves in the wrong direction.
        value = value.strip()
        if value:
            # First enable: create the device secret in the same step that
            # turns the surface on, so "reachable" and "authenticable" cannot
            # come apart. An existing secret is returned untouched \u2014 issuing a
            # new one here would revoke every device that already holds a
            # cookie, on a route the user thinks only sets an address.
            if not ensure_remote_secret():
                return {"ok": False,
                        "error": "Could not create the device secret; "
                                 "remote access not enabled"}
            value = value.strip()
    setattr(config, key, value)
    save_config(config)
    return {"ok": True, "restart_required": key in _RESTART_TO_APPLY}


@app.get("/api/remote-access")
async def api_remote_access(response: Response):
    """The remote URL and device secret, as copyable text (D22).

    Its own route rather than a field on `/api/settings`: that payload is
    fetched broadly by the dashboard and a credential does not belong in it.
    Both routes are loopback-only \u2014 they are absent from
    `_REMOTE_ALLOWED_PATHS`, and the allowlist is default-deny \u2014 so the secret
    is never served over the remote surface it authenticates.

    `no-store` for the same reason `/acp` sets it, only more so: this body
    carries the **permanent** device secret, where `/acp` carries the strictly
    weaker per-launch rotating `_ACP_TOKEN`. Nothing fetches this route yet,
    which is exactly why the header goes on now — before a consumer exists to
    start caching it.
    """
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    config = load_config()
    secret = load_remote_secret()
    address = config.remote_bind_address
    return {
        "enabled": bool(address),
        "remote_bind_address": address,
        "url": f"http://{address}:{config.port}{_REMOTE_AUTH_PATH}" if address and config.port else "",
        "secret": secret,
        "secret_present": bool(secret),
        "secret_path": str(REMOTE_SECRET_PATH),
        # Read once at startup: the bind happens before the app exists.
        "restart_required": True,
        # The runtime stop switch, read out of the surface the guard itself
        # consults rather than out of a second variable tracking it — so this
        # field cannot report "running" while every remote request is being
        # refused, or the reverse.
        "stopped": remote_stopped(),
    }


@app.post("/api/remote-access/rotate")
async def api_remote_access_rotate(response: Response):
    """Issue a new device secret, revoking **every** authorized device (D24).

    D24 knowingly gives up *per-device* revocation and names secret rotation as
    the remedy — but until now no route, flag or command performed one, so the
    real answer for a lost phone was "delete `remote-secret` by hand and
    restart", which nobody finds under pressure.

    Loopback-only by the same mechanism as `/api/remote-access`, not a second
    one: neither path appears in `_REMOTE_ALLOWED_PATHS`, and that allowlist is
    default-deny (D6), so `RemoteAccessGuard` refuses both from any non-loopback
    peer before routing. A remote peer must not be able to lock the owner out of
    their own devices, and one holding a stolen cookie must not be able to
    re-key the surface around it.

    POST, so `same_origin_guard`'s Origin/Referer check applies — the same CSRF
    protection every other mutating route here gets, and it matters more than
    usual: a GET would be reachable by any cross-origin `<img src>`, and this
    action is irreversible.

    **Ordering.** The file is written first, the in-process secret second, and
    that order is chosen for how the partial failures read:

    * file written, in-process update not reached — the durable state is the new
      secret, the running process still honours the old one. The lost device
      keeps working until the next restart, which then completes the rotation.
      Stale-but-converging.
    * in-process updated, file write failed — every device is revoked *now*, but
      the process reloads the OLD secret from disk at startup and every revoked
      cookie comes back to life. Revocation that silently undoes itself on
      restart is the worse failure, and it is the one this ordering excludes.

    When the write fails outright, nothing changes at all and the caller is told.
    """
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    secret = rotate_remote_secret()
    if not secret:
        return {"ok": False,
                "error": f"Could not write {REMOTE_SECRET_PATH}; "
                         "the previous secret is still in effect"}
    # Only when this process actually serves the remote surface. `_REMOTE_SECRET`
    # is empty on an instance that never bound a remote listener, and
    # `set_remote_secret` documents that as deliberate — loading one here would
    # give a loopback-only process a live authentication path for a surface it
    # is not serving. `applied` reports which of the two happened rather than
    # letting the caller assume.
    applied = bool(_REMOTE_SECRET)
    if applied:
        set_remote_secret(secret)
    log.warning("device secret rotated; every authorized device must "
                "re-authenticate (applied in-process: %s)", applied)
    return {
        "ok": True,
        "secret": secret,
        "secret_path": str(REMOTE_SECRET_PATH),
        # The destructive consequence, in the payload rather than only in a doc:
        # a caller that renders `ok` and nothing else still cannot claim it was
        # not told.
        "devices_revoked": True,
        "applied": applied,
        "restart_required": not applied,
        "message": ("Every authorized device has been signed out and must "
                    "re-enter the new secret."
                    if applied else
                    "The new secret is saved but takes effect on restart; "
                    "remote access is not running in this process."),
    }


_STOP_MESSAGE = (
    "Remote access is stopped. Every request arriving from a remote address is "
    "refused from now on. The port stays bound until PowerAtlas restarts — the "
    "socket was not closed — and config.toml was not changed, so a restart "
    "comes back up according to the bind address."
)

_RESUME_MESSAGE = (
    "Remote access is running again. Requests from remote addresses are served "
    "exactly as before, subject to the same path allowlist and device cookie."
)


@app.post("/api/remote-access/stop")
async def api_remote_access_stop(request: Request, response: Response):
    """Stop or resume the remote surface at runtime, without a restart.

    The user asked to be able to disable remote control of this machine
    *immediately*, and chose "refuse every remote request" over "close the
    socket" knowing the consequence: the listener stays bound until the process
    restarts, so a device gets a refusal rather than a connection error. That is
    the honest description and it is what the panel says.

    Loopback-only by the same mechanism as `/api/remote-access` and
    `/api/remote-access/rotate`, not a second one: this path is absent from
    `_REMOTE_ALLOWED_PATHS`, and that allowlist is default-deny (D6), so
    `RemoteAccessGuard` refuses it from any non-loopback peer before routing.
    Both directions need that. A remote peer must not be able to resume a
    surface its owner stopped — and, less obviously, must not be able to *stop*
    it either, which would be a denial of service against the owner's own phone
    driven from a peer that never authenticated.

    POST, so `same_origin_guard`'s Origin/Referer check applies: the same CSRF
    protection every other mutating route here gets.

    **Only an exact `{"stopped": false}` resumes.** A body that is not JSON, is
    not an object, omits the field, or sends something other than a boolean,
    stops — the ambiguous direction here is the one that refuses remote
    requests. `set_remote_stopped` enforces that, in one place, rather than each
    caller deciding; the reply then reports the state actually in force rather
    than the state that was asked for.

    Nothing is written to `config.toml`. This is process state by design: a
    restart reads `remote_bind_address` and comes back up according to it.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        body = await request.json()
    except Exception:
        # An unparseable body is exactly the case that must not resume, so it
        # is not an error path — it falls into the stopping direction below.
        body = None
    set_remote_stopped(body.get("stopped") if isinstance(body, dict) else None)
    stopped = remote_stopped()
    log.warning("remote access %s at runtime; the listening socket is "
                "unchanged and config.toml is unchanged",
                "STOPPED" if stopped else "resumed")
    return {
        "ok": True,
        # Read back out of the guard's own surface, not echoed from the
        # request: a caller is told what is in force, not what it asked for.
        "stopped": stopped,
        "persisted": False,
        "socket_closed": False,
        "message": _STOP_MESSAGE if stopped else _RESUME_MESSAGE,
    }


@app.get("/partials/session-tail", response_class=HTMLResponse)
async def partials_session_tail(request: Request, sid: str = "", provider: str = "kiro-cli", cwd: str = ""):
    # Validate sid to a UUID-like pattern before passing to the data layer.
    # Accept bare UUID (v2: aabbccdd-...) or sess_<uuid> (v3 prefix) formats.
    if not re.fullmatch(r'(?:sess_)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', sid):
        return HTMLResponse('<div class="tail-empty">Invalid session id</div>', status_code=400)
    messages = await asyncio.to_thread(data.get_session_tail, sid, provider, cwd)
    first_prompt = await asyncio.to_thread(data.get_first_prompt, sid, provider, cwd)
    # Look up session title and last_prompt from cache
    session_title = ""
    last_prompt = ""
    cached_sessions = data.session_cache.get(cwd, provider)
    if cached_sessions:
        for s in cached_sessions:
            if s.session_id == sid:
                session_title = s.title
                last_prompt = (s.last_prompt or "").strip()
                break
    # Derive workspace name from cwd
    workspace_name = Path(cwd).name if cwd else ""
    # Guard: show empty-state if ALL content fields are absent
    if not messages and not first_prompt and not last_prompt:
        return HTMLResponse('<div class="tail-empty">No recent output</div>')
    # Suppress last_prompt when it duplicates the beginning of first_prompt (dedup for single-exchange sessions).
    # first_prompt from get_first_prompt() may be longer than last_prompt from session cache (200-char cap).
    if last_prompt and first_prompt.startswith(last_prompt):
        last_prompt = ""
    # Render all text sections through mistune (escape=True entity-encodes raw HTML — safe for | safe filter)
    first_prompt_html = _md(first_prompt) if first_prompt else ""
    last_prompt_html = _md(last_prompt) if last_prompt else ""
    messages_html = [_md(m) for m in messages]
    return templates.TemplateResponse(request, "partials/session_tail.html", {
        "first_prompt": first_prompt_html,
        "last_prompt": last_prompt_html,
        "messages": messages_html,
        "session_title": session_title,
        "workspace_name": workspace_name,
        "session_id": sid,
    })


@app.get("/partials/sessions", response_class=HTMLResponse)
async def partials_sessions(request: Request, cwd: str = "", provider: str = "all",
                            status: str = "", fresh: int = 0):
    """Lazy-load sessions for a workspace card. provider=all merges all providers."""
    import asyncio
    import time
    t0 = time.perf_counter()
    log.info("Loading sessions for %s", cwd[-40:])
    config = load_config()

    if provider == "all":
        # Merge sessions from all providers, sorted by updated_at desc
        all_sessions = []
        for prov_name, mod in data.PROVIDERS.items():
            if not mod.is_available():
                continue
            if not _enabled(config, prov_name):
                continue
            if fresh:
                try:
                    sessions, file_stats = await asyncio.to_thread(mod.load_sessions, cwd)
                    data.session_cache.put(cwd, sessions, file_stats, prov_name)
                except Exception:
                    sessions = []
            else:
                try:
                    sessions = await asyncio.to_thread(data.get_sessions, cwd, prov_name)
                except Exception:
                    sessions = []
            for s in sessions:
                all_sessions.append((s, prov_name))
        # Sort interleaved by updated_at descending
        # Normalize Z→+00:00 for consistent lexicographic sort across providers
        all_sessions.sort(key=lambda x: (x[0].updated_at or "").replace("Z", "+00:00"), reverse=True)
        sessions_with_provider = all_sessions
    else:
        # Single provider (existing behavior)
        if fresh:
            mod = data.PROVIDERS.get(provider)
            if mod and mod.is_available():
                try:
                    sessions, file_stats = await asyncio.to_thread(mod.load_sessions, cwd)
                    data.session_cache.put(cwd, sessions, file_stats, provider)
                except Exception:
                    sessions = []
            else:
                sessions = []
        else:
            try:
                sessions = await asyncio.to_thread(data.get_sessions, cwd, provider)
            except Exception:
                sessions = []
        sessions_with_provider = [(s, provider) for s in sessions]

    log.info("Got %d sessions for %s in %.2fs", len(sessions_with_provider), Path(cwd).name, time.perf_counter() - t0)
    # Flatten for pinned sort, then re-pair
    flat_sessions = [s for s, _ in sessions_with_provider]
    flat_sessions = _sort_pinned_first(flat_sessions, config.pinned_sessions)
    # Rebuild provider mapping after sort
    prov_map = {id(s): p for s, p in sessions_with_provider}

    if not flat_sessions:
        return HTMLResponse('<div class="new-session-inline">+ New session</div>')
    stale = not Path(cwd).exists()
    snap = await asyncio.to_thread(presence.get_snapshot)
    _notif_enabled = config.notifications.get("enabled", False)
    html = ""
    for session in flat_sessions:
        prov_name = prov_map.get(id(session), provider if provider != "all" else "kiro-cli")
        sess_status = _session_status(snap, session, prov_name, _notif_enabled)
        if status and status != "all" and not _status_matches(status, sess_status):
            continue
        html += templates.get_template("partials/session_row.html").render(
            request=request, session=session, cwd=cwd, stale=stale,
            pinned_sessions=config.pinned_sessions,
            provider_name=prov_name,
            provider_color=_get_provider_color(prov_name, config),
            status=sess_status,
            waiting_detail=_waiting_detail(snap, session, prov_name, sess_status),
            origin=_row_origin(snap, session, prov_name),
        )
    if not html:
        return HTMLResponse('<div class="new-session-inline">No matching sessions</div>')
    return HTMLResponse(html)


@app.post("/api/launch", response_class=HTMLResponse)
async def api_launch(request: Request):
    body = await request.json()
    config = load_config()
    provider = body.get("provider") or "kiro-cli"
    cwd = _resolve_launch_cwd(body.get("workspace", ""), config, provider)
    default_args = config.provider_settings.get(provider, {}).get("default_args", "")
    result = launcher.launch_session(
        cwd=cwd,
        session_id=body.get("session_id"),
        provider=provider,
        default_args=default_args,
        launch_profile=get_active_launch_profile(config),
    )
    if not result.success:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": result.error, "level": "error",
        })
    if result.warning:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": result.warning, "level": "warning", "persistent": True,
        })
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": "Session launched", "level": "success",
    })


@app.post("/api/launch-batch", response_class=HTMLResponse)
async def api_launch_batch(request: Request):
    body = await request.json()
    config = load_config()
    # Resolve empty workspaces through the fallback chain before passing to launcher
    sessions = body["sessions"]
    for s in sessions:
        if not s.get("workspace"):
            s["workspace"] = _resolve_launch_cwd("", config, s.get("provider", "kiro-cli"))
    results = launcher.launch_batch(
        sessions=sessions,
        launch_profile=get_active_launch_profile(config),
        provider_settings=config.provider_settings,
    )
    ok = sum(1 for r in results if r.success)
    failed = len(results) - ok
    warnings = [r.warning for r in results if r.success and r.warning]
    msg = f"Launched {ok} session{'s' if ok != 1 else ''}"
    if failed and warnings:
        msg = f"Launched {ok} ({len(warnings)} via fallback), {failed} failed"
    elif failed:
        msg += f", {failed} failed"
    elif warnings:
        msg = f"{len(warnings)} launch{'es' if len(warnings) != 1 else ''} used fallback: {warnings[0]}"
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": msg, "level": "warning", "persistent": True,
        })
    level = "success" if not failed else ("warning" if ok else "error")
    return templates.TemplateResponse(request, "partials/toast.html", {"message": msg, "level": level})


@app.post("/api/new-session", response_class=HTMLResponse)
async def api_new_session(request: Request):
    body = await request.json()
    config = load_config()
    provider = body.get("provider") or "kiro-cli"
    cwd = _resolve_launch_cwd(body.get("workspace", ""), config, provider)
    default_args = config.provider_settings.get(provider, {}).get("default_args", "")
    result = launcher.launch_session(
        cwd=cwd,
        session_id=None,
        provider=provider,
        default_args=default_args,
        launch_profile=get_active_launch_profile(config),
    )
    if not result.success:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": result.error, "level": "error",
        })
    if result.warning:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": result.warning, "level": "warning", "persistent": True,
        })
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": "New session launched", "level": "success",
    })


def _sort_pinned_first(sessions: list[data.Session], pinned: list[str]) -> list[data.Session]:
    """Sort pinned sessions to top while preserving relative order."""
    pinned_set = set(pinned)
    top = [s for s in sessions if s.session_id in pinned_set]
    rest = [s for s in sessions if s.session_id not in pinned_set]
    return top + rest


def _session_matches(session: data.Session, query: str) -> bool:
    return (
        query in (session.title or "").lower()
        or query in (session.first_prompt or "").lower()
        or query in (session.last_prompt or "").lower()
        or query in (session.last_reply_tail or "").lower()
    )



@app.get("/partials/launchers", response_class=HTMLResponse)
async def partials_launchers(request: Request):
    config = load_config()
    html = ""
    # Provider-launcher tiles first
    try:
        providers = sorted(data.available_providers())
    except Exception:
        providers = []
    for p in providers:
        settings = config.provider_settings.get(p, {})
        if not _enabled(config, p):
            continue
        provider_launcher = {
            "id": f"provider--{p}",
            "name": PROVIDER_DISPLAY_NAMES.get(p, p),
            "command": _PROVIDER_BINARY_DISPLAY.get(p, p),
            "custom_args": settings.get("default_args", ""),
            "color": settings.get("color", "") or PROVIDER_COLORS.get(p, ""),
            "terminal": True,
            "use_selected_workspaces": True,
            "is_provider": True,
        }
        html += templates.get_template("partials/launcher_tile.html").render(request=request, launcher=provider_launcher)
    # Built-in terminal tile
    terminal_tile = {
        "id": "builtin--terminal",
        "name": "Terminal",
        "command": "terminal",
        "custom_args": "",
        "color": "#6b7280",
        "terminal": True,
        "use_selected_workspaces": True,
        "is_provider": True,  # shows lock icon + prevents editing
    }
    html += templates.get_template("partials/launcher_tile.html").render(request=request, launcher=terminal_tile)
    # Custom launchers after
    for l in config.custom_launchers:
        html += templates.get_template("partials/launcher_tile.html").render(request=request, launcher=l)
    return HTMLResponse(html)


@app.get("/api/launchers")
async def api_launchers():
    config = load_config()
    return _launchers_without_env(config.custom_launchers)


@app.post("/api/launcher/env")
async def api_launcher_env(request: Request):
    """One launcher's environment variables, for the edit modal only.

    **POST for a read, deliberately.** `same_origin_guard`'s Origin/Referer
    check is POST-only, and its docstring justifies that scope with "every other
    GET here only reads" — meaning reads of things it is content to hand a
    cross-origin page. Credentials are not that, so this route opts into the
    stricter half rather than widening the guard. `/api/workspace-settings-bulk`
    is the existing precedent for POST-to-read in this file.

    Not on `_REMOTE_ALLOWED_PATHS`, which is default-deny, so a NetBird peer is
    refused before routing ever happens — no code here depends on that, it is
    stated so the omission reads as intentional.

    Answers 404 for an unknown id rather than an empty env, because the caller
    has to tell "no variables set" apart from "this launcher is gone": the
    modal writes whatever it renders straight back on save, so the two must not
    look alike.
    """
    body = await request.json()
    lid = body.get("id")
    if not isinstance(lid, str) or not lid:
        return JSONResponse({"error": "A launcher id is required"}, status_code=400)
    config = load_config()
    entry = next((e for e in config.custom_launchers if e.get("id") == lid), None)
    if entry is None:
        return JSONResponse({"error": "No such launcher"}, status_code=404)
    env = entry.get("env")
    return {"env": env if isinstance(env, dict) else {}}


@app.post("/api/launcher/create", response_class=HTMLResponse)
async def launcher_create(request: Request):
    body = await request.json()
    config = load_config()
    entry = {
        "id": str(uuid.uuid4()),
        "name": body.get("name", ""),
        "command": body.get("command", ""),
        "custom_args": body.get("custom_args", ""),
        "cwd": body.get("cwd", ""),
        "env": body.get("env", {}),
        "color": body.get("color", ""),
        "terminal": body.get("terminal", True),
        "use_selected_workspaces": body.get("use_selected_workspaces", False),
        "show_in_workspace_hover": body.get("show_in_workspace_hover", False),
    }
    config.custom_launchers.append(entry)
    save_config(config)
    icons.extract_icon(entry["id"], entry["command"], entry["terminal"])
    return templates.TemplateResponse(request, "partials/toast.html", {"message": "Launcher created", "level": "success"})


@app.post("/api/launcher/update", response_class=HTMLResponse)
async def launcher_update(request: Request):
    body = await request.json()
    lid = body.get("id")
    config = load_config()
    for entry in config.custom_launchers:
        if entry["id"] == lid:
            for k in ("name", "command", "custom_args", "cwd", "env", "color", "terminal", "use_selected_workspaces", "show_in_workspace_hover"):
                if k in body:
                    entry[k] = body[k]
            icons.extract_icon(lid, entry.get("command", ""), entry.get("terminal", True))
            break
    save_config(config)
    return templates.TemplateResponse(request, "partials/toast.html", {"message": "Launcher updated", "level": "success"})


@app.post("/api/launcher/delete", response_class=HTMLResponse)
async def launcher_delete(request: Request):
    body = await request.json()
    lid = body.get("id")
    config = load_config()
    config.custom_launchers = [e for e in config.custom_launchers if e["id"] != lid]
    save_config(config)
    icons.remove_icon(lid)
    return templates.TemplateResponse(request, "partials/toast.html", {"message": "Launcher deleted", "level": "success"})


@app.get("/api/launcher-icon/{launcher_id}")
async def launcher_icon(launcher_id: str):
    from fastapi.responses import FileResponse, Response

    # Handle built-in terminal icon
    if launcher_id == "builtin--terminal":
        svg = icons.default_icon_svg(True, "#6b7280")
        return Response(content=svg, media_type="image/svg+xml")

    # Handle provider launcher icons
    if launcher_id.startswith("provider--"):
        provider_key = launcher_id[len("provider--"):]
        if icons.has_icon(launcher_id):
            return FileResponse(icons.icon_path(launcher_id), media_type="image/png")
        binary = launcher._PROVIDER_BINARY.get(provider_key, provider_key)
        await asyncio.to_thread(icons.extract_icon, launcher_id, binary, True)
        if icons.has_icon(launcher_id):
            return FileResponse(icons.icon_path(launcher_id), media_type="image/png")
        config = load_config()
        color = _get_provider_color(provider_key, config)
        svg = icons.default_icon_svg(True, color)
        return Response(content=svg, media_type="image/svg+xml")

    if icons.has_icon(launcher_id):
        return FileResponse(icons.icon_path(launcher_id), media_type="image/png")
    # Determine if terminal launcher for appropriate fallback
    config = load_config()
    is_terminal = True
    color = ""
    for entry in config.custom_launchers:
        if entry["id"] == launcher_id:
            is_terminal = entry.get("terminal", True)
            color = entry.get("color", "")
            break
    svg = icons.default_icon_svg(is_terminal, color)
    return Response(content=svg, media_type="image/svg+xml")


@app.post("/api/launcher/run", response_class=HTMLResponse)
async def launcher_run(request: Request):
    body = await request.json()
    config = load_config()
    use_terminal = body.get("terminal", True)
    # `env` is resolved from the stored launcher when the caller names one,
    # rather than taken from the request body. Two reasons, and the first is
    # load-bearing: the page no longer *has* it — `_launchers_without_env`
    # strips `env` from every launcher payload this app serves, so the old
    # `env: l.env` the tile sent would now arrive as `undefined` and every
    # custom launcher would start without its variables. The second is that a
    # launcher's credentials should not make a round trip through the browser
    # to reach the process that already holds them. `/api/launcher/run-batch`
    # has always resolved its entry this way; this brings the two into line.
    #
    # The body value stays as the fallback for a call that names no id, which
    # is the shape the ad-hoc "run this command" path and `test_launcher_run`
    # both use.
    env = body.get("env")
    lid = body.get("id")
    if isinstance(lid, str) and lid:
        stored = next((e for e in config.custom_launchers if e.get("id") == lid), None)
        if stored is not None:
            env = stored.get("env")
    result = launcher.launch_custom(
        name=body.get("name", ""),
        command=body.get("command", ""),
        custom_args=body.get("custom_args", ""),
        cwd=body.get("cwd", ""),
        env=env,
        launch_profile=get_active_launch_profile(config),
        use_terminal=use_terminal,
    )
    if not result.success:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": result.error, "level": "error",
        })
    if result.warning:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": result.warning, "level": "warning", "persistent": True,
        })
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": "Launcher started", "level": "success",
    })


@app.post("/api/launcher/run-batch", response_class=HTMLResponse)
async def launcher_run_batch(request: Request):
    body = await request.json()
    lid = body.get("id")
    workspaces = body.get("workspaces", [])
    config = load_config()
    entry = next((e for e in config.custom_launchers if e["id"] == lid), None)
    if not entry:
        return templates.TemplateResponse(request, "partials/toast.html", {"message": "Launcher not found", "level": "error"})
    results = launcher.launch_custom_batch(
        name=entry.get("name", ""),
        command=entry.get("command", ""),
        custom_args=entry.get("custom_args", ""),
        workspaces=workspaces,
        env=entry.get("env"),
        launch_profile=get_active_launch_profile(config),
        use_terminal=entry.get("terminal", True),
        pass_workspace_arg=not entry.get("terminal", True) and entry.get("use_selected_workspaces", False),
    )
    ok = sum(1 for r in results if r.success)
    failed = len(results) - ok
    warnings = [r.warning for r in results if r.success and r.warning]
    msg = f"Launched {ok} instance{'s' if ok != 1 else ''}"
    if failed and warnings:
        msg = f"Launched {ok} ({len(warnings)} via fallback), {failed} failed"
    elif failed:
        msg += f", {failed} failed"
    elif warnings:
        msg = f"{len(warnings)} launch{'es' if len(warnings) != 1 else ''} used fallback: {warnings[0]}"
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": msg, "level": "warning", "persistent": True,
        })
    level = "success" if not failed else ("warning" if ok else "error")
    return templates.TemplateResponse(request, "partials/toast.html", {"message": msg, "level": level})


@app.get("/api/workspace-settings")
async def get_workspace_settings_api(cwd: str = ""):
    """Return workspace settings for a given cwd."""
    config = load_config()
    from .config import get_workspace_settings
    settings = get_workspace_settings(config, cwd)
    # Also return all known tags for autocomplete
    all_tags = set()
    for ws in config.workspace_settings.values():
        all_tags.update(ws.get("tags", []))
    all_tags.update(config.tag_settings.keys())
    return {"settings": settings, "all_tags": sorted(all_tags)}


@app.post("/api/workspace-settings/save", response_class=HTMLResponse)
async def save_workspace_settings_api(request: Request):
    """Save workspace settings (tags, color) for a workspace path."""
    body = await request.json()
    cwd = body.get("cwd", "")
    tags = body.get("tags", [])
    color = body.get("color", "")
    # Validation: path
    if not cwd or len(cwd) > 512 or any(ord(ch) < 0x20 for ch in cwd):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Invalid workspace path", "level": "error"})
    # Validation: tags (max 10, each 1-64 chars, no control chars)
    if not isinstance(tags, list) or len(tags) > 10:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Max 10 tags per workspace", "level": "error"})
    for t in tags:
        if not isinstance(t, str) or not t or len(t) > 64 or any(ord(ch) < 0x20 for ch in t):
            return templates.TemplateResponse(request, "partials/toast.html", {
                "message": "Invalid tag: 1-64 chars, no control chars", "level": "error"})
    # Validation: color (hex format or empty)
    if color and (len(color) > 20 or any(ord(ch) < 0x20 for ch in color)):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Invalid color value", "level": "error"})
    config = load_config()
    # Normalize key at save time to prevent duplicate entries for same path
    from .data import _normalize_path
    norm_cwd = _normalize_path(cwd)
    for existing_key in list(config.workspace_settings.keys()):
        if _normalize_path(existing_key) == norm_cwd and existing_key != cwd:
            del config.workspace_settings[existing_key]
    config.workspace_settings[cwd] = {"tags": tags, "color": color}
    save_config(config)
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": "Workspace settings saved", "level": "success",
    })


@app.post("/api/workspace-settings/save-bulk", response_class=HTMLResponse)
async def save_workspace_settings_bulk_api(request: Request):
    """Bulk-apply tag additions/removals and color to multiple workspaces."""
    body = await request.json()
    cwds = body.get("cwds", [])
    tags_add = body.get("tags_add", [])
    tags_remove = body.get("tags_remove", [])
    color = body.get("color")  # None means don't change; "" means clear

    # Validation: cwds
    if not isinstance(cwds, list) or len(cwds) < 1 or len(cwds) > 50:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "cwds must be a list of 1-50 paths", "level": "error"})

    # Validation: tags_add / tags_remove are lists of valid tag strings
    for tag_list, label in [(tags_add, "tags_add"), (tags_remove, "tags_remove")]:
        if not isinstance(tag_list, list) or len(tag_list) > 20:
            return templates.TemplateResponse(request, "partials/toast.html", {
                "message": f"{label} must be a list (max 20)", "level": "error"})
        for t in tag_list:
            if not isinstance(t, str) or not t or len(t) > 64 or any(ord(ch) < 0x20 for ch in t):
                return templates.TemplateResponse(request, "partials/toast.html", {
                    "message": "Invalid tag: 1-64 chars, no control chars", "level": "error"})

    # Validation: no overlap between add and remove
    if set(tags_add) & set(tags_remove):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "tags_add and tags_remove must not overlap", "level": "error"})

    # Validation: color (if present)
    if color is not None:
        if not isinstance(color, str) or len(color) > 20 or any(ord(ch) < 0x20 for ch in color):
            return templates.TemplateResponse(request, "partials/toast.html", {
                "message": "Invalid color value", "level": "error"})

    config = load_config()
    from .data import _normalize_path

    # Pre-build normalized path lookup for O(1) access
    norm_key_map = {_normalize_path(k): k for k in config.workspace_settings}
    tags_remove_set = set(tags_remove)

    modified = 0
    skipped = 0

    for cwd in cwds:
        if not isinstance(cwd, str) or not cwd or len(cwd) > 512 or any(ord(ch) < 0x20 for ch in cwd):
            continue
        norm_cwd = _normalize_path(cwd)
        # Find existing key or use the raw cwd
        key = norm_key_map.get(norm_cwd, cwd)
        ws = config.workspace_settings.get(key, {"tags": [], "color": ""})

        changed = False
        # Remove tags
        ws_tags = ws.get("tags", [])
        new_tags = [t for t in ws_tags if t not in tags_remove_set]
        if len(new_tags) != len(ws_tags):
            changed = True
        ws_tags = new_tags

        # Add tags (respect 10-tag limit)
        hit_limit = False
        for t in tags_add:
            if t not in ws_tags:
                if len(ws_tags) >= 10:
                    hit_limit = True
                else:
                    ws_tags.append(t)
                    changed = True

        if hit_limit:
            skipped += 1

        # Set color if specified
        ws_color = color if color is not None else ws.get("color", "")
        if ws_color != ws.get("color", ""):
            changed = True

        if changed:
            config.workspace_settings[key] = {"tags": ws_tags, "color": ws_color}
            modified += 1

    # Auto-create tags in tag_settings for any new tags
    for t in tags_add:
        if t not in config.tag_settings:
            config.tag_settings[t] = {"color": ""}

    if modified > 0:
        save_config(config)

    if skipped > 0:
        msg = f"Updated {modified} workspace(s) ({skipped} hit 10-tag limit)"
        level = "warning"
    else:
        msg = f"Updated {modified} workspace(s)"
        level = "success"

    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": msg, "level": level})


@app.post("/api/workspace-settings-bulk")
async def get_workspace_settings_bulk_api(request: Request):
    """Return workspace settings for multiple cwds."""
    body = await request.json()
    cwds = body.get("cwds", [])

    if not isinstance(cwds, list) or len(cwds) < 1 or len(cwds) > 50:
        raise HTTPException(status_code=400, detail="cwds must be a list of 1-50 paths")

    config = load_config()
    from .config import get_workspace_settings

    workspaces = {}
    for cwd in cwds:
        if isinstance(cwd, str) and cwd and len(cwd) <= 512:
            workspaces[cwd] = get_workspace_settings(config, cwd)

    # all_tags: union of all workspace tags + all tag_settings keys
    all_tags = set()
    for ws in workspaces.values():
        all_tags.update(ws.get("tags", []))
    all_tags.update(config.tag_settings.keys())

    return {"workspaces": workspaces, "all_tags": sorted(all_tags)}


@app.post("/api/restart")
async def api_restart():
    """Trigger restart via the tray mechanism."""
    import power_atlas.tray as _tray
    _tray._restart_requested = True
    if _tray._peek_stop_callback:
        _tray._peek_stop_callback()
    _tray._shutdown_event.set()
    if _tray._icon_instance:
        _tray._icon_instance.stop()
    return {"ok": True}
