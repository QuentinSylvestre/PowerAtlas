"""FastAPI web application with htmx-powered UI."""

import asyncio
import html as html_mod
import logging
import os
import re
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from datetime import timezone
from .config import load_config, save_config, get_active_launch_profile, LaunchProfile
from . import autostart, data, icons, launcher, notifications, presence
from .status_classifier import get_semantic_status, SemanticStatus

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


def _age_seconds(iso_str: str) -> float | None:
    """Seconds since an ISO-8601 timestamp, or None if unparseable.

    Normalizes both aware (Claude: mtime→UTC) and naive (Kiro: raw DB value,
    assumed local) timestamps to UTC before comparing.
    """
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, OSError):
        return None
    dt = dt.astimezone(timezone.utc)  # naive is interpreted as local time
    return (datetime.now(timezone.utc) - dt).total_seconds()


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

    # 4. Prefer the provider's own report over inference, where it is
    #    unambiguous. claude-code writes its live state to
    #    ~/.claude/sessions/<pid>.json; presence validates that file against
    #    the process before exposing it. Its four values map as:
    #      busy    - a turn is running        -> working
    #      shell   - a shell command is running -> working
    #      waiting - a dialog needs the human -> waiting
    #      idle    - none of the above        -> ambiguous, defer
    #    "idle" is deliberately not treated as "waiting": it covers finished,
    #    errored and never-started alike, so only the classifier can say which
    #    — and it is the sole source of "errored". The provider report can
    #    therefore only ever settle a state, never downgrade a richer verdict.
    reported = snapshot.reported_status(provider, session.session_id)

    if not is_explicitly_live and not has_process:
        status_value = "closed"
    elif reported in ("busy", "shell"):
        status_value = "working"
    elif reported == "waiting":
        status_value = "waiting"
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


def _workspace_status(snapshot, cwd: str,
                      providers: set[str] | None) -> str:
    """Aggregate status for a workspace card — highest-priority session status wins.

    Priority: errored > waiting > working > closed (no dot).
    Falls back to classifying the most recently updated session when no
    explicit --resume-id sessions are tracked for this cwd.
    """
    from .data import _normalize_path
    if _normalize_path(cwd) not in snapshot.live_cwds(providers):
        return "closed"
    # Check semantic status for recent sessions in this workspace
    best = "working"  # at minimum, a process is running
    best_pri = _STATUS_PRIORITY[best]
    found_any = False
    # Try to get semantic classification for sessions in this cwd
    for prov in (providers or {"kiro-cli", "claude-code"}):
        sids = snapshot.live_session_ids_for_cwd(prov, cwd)
        for sid in sids:
            semantic = get_semantic_status(sid, prov, cwd)
            if semantic is not None:
                found_any = True
                s = semantic.value
                pri = _STATUS_PRIORITY.get(s, 0)
                if pri > best_pri:
                    best = s
                    best_pri = pri
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
                semantic = get_semantic_status(recent.session_id, prov, cwd)
                # Use semantic if available; otherwise process running = working
                if semantic is not None:
                    s = semantic.value
                else:
                    s = "working"
                pri = _STATUS_PRIORITY.get(s, 0)
                if pri > best_pri:
                    best = s
                    best_pri = pri
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
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


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
# with the attacker's Host (e.g. evil.com), which would otherwise make the
# reflected same-origin check below trust the attacker's origin. "testserver" is
# Starlette's TestClient default Host — not publicly resolvable, so inert as an
# attack vector.
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "testserver"})


@app.middleware("http")
async def same_origin_guard(request: Request, call_next):
    """Reject cross-origin, non-loopback, or CSRF-suspect POST requests."""
    if request.method == "POST":
        # DNS-rebinding defense: the Host must be a loopback name, else an
        # attacker-controlled Host would make the same-origin check below reflect
        # the attacker's origin.
        if (request.url.hostname or "").lower() not in _ALLOWED_HOSTS:
            return JSONResponse({"error": "Forbidden"}, status_code=403)
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        if origin == "null":
            return JSONResponse({"error": "Forbidden"}, status_code=403)
        if not origin and not referer:
            return JSONResponse({"error": "Forbidden"}, status_code=403)
        expected_origin = f"{request.url.scheme}://{request.url.netloc}"
        if origin and origin != expected_origin:
            return JSONResponse({"error": "Forbidden"}, status_code=403)
        if not origin and referer:
            parsed = urlparse(referer)
            referer_origin = f"{parsed.scheme}://{parsed.netloc}"
            if referer_origin != expected_origin:
                return JSONResponse({"error": "Forbidden"}, status_code=403)
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    config = load_config()
    profile = get_active_launch_profile(config)
    return templates.TemplateResponse(request, "index.html", {
        "port": config.port,
        "active_launch_profile": profile,
        "launch_profiles": [asdict(p) for p in config.launch_profiles],
        "autostart": autostart.is_enabled(),
        "launchers": config.custom_launchers,
        "peek_hotkey": config.peek_hotkey,
        "default_directory": config.default_directory,
        "provider_settings": config.provider_settings,
        "autostart_label": "Start at login" if sys.platform != "win32" else "Start with Windows",
    })


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


@app.post("/api/session-status")
async def api_session_status(request: Request):
    """Lightweight status endpoint for background polling.

    Accepts {"cwds": [...]} and returns per-session and per-workspace status
    without triggering notification side-effects. Skips kiro-ide entirely and
    short-circuits cwds that have no live process.
    """
    body = await request.json()
    cwds = body.get("cwds", [])
    if not cwds:
        return JSONResponse({"sessions": {}, "workspaces": {}, "active_cwds": []})

    # Only process CLI providers (skip kiro-ide)
    poll_providers = {"kiro-cli", "claude-code"}

    # Get cached presence snapshot (3s TTL — very fast)
    snapshot = await asyncio.to_thread(presence.get_snapshot)
    active_norm_cwds = snapshot.live_cwds(poll_providers)

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
            best_status = "working"  # at minimum, a process is running
            best_pri = _STATUS_PRIORITY[best_status]
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
                        pri = _STATUS_PRIORITY.get(status, 0)
                        if pri > best_pri:
                            best_status = status
                            best_pri = pri

            # Derive workspace-level aggregate from per-session statuses already computed
            workspaces_map[cwd] = best_status
        except Exception:
            log.debug("Status poll failed for cwd %s", cwd)
            workspaces_map[cwd] = "closed"

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
            status=row_status.get((session.session_id, prov_name), "closed"),
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
                    status=row_status.get((session.session_id, prov_name), "closed"),
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

    if not matched:
        return templates.TemplateResponse(request, "partials/empty_state.html", {
            "message": f'No results for "{q}"',
        })

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

    # Apply live-status filter
    if status and status != "all":
        snap = await asyncio.to_thread(presence.get_snapshot)
        prov_names = None if provider == "all" else {provider}
        grouped = [g for g in grouped if _status_matches(
            status, _workspace_status(snap, g["cwd"], g["latest_updated"], prov_names))]

    cards_html = ""

    # Separate pinned from non-pinned results
    pinned_results = [g for g in grouped if _normalize_path(g["cwd"]) in pinned_norm_paths]
    other_results = [g for g in grouped if _normalize_path(g["cwd"]) not in pinned_norm_paths]

    # Render pinned results at top
    for group in pinned_results:
        cwd = group["cwd"]
        stale = not Path(cwd).exists()
        workspace_color = _resolve_workspace_color(cwd, config)
        cards_html += templates.get_template("partials/workspace_card.html").render(
            request=request, cwd=cwd, sessions=[], stale=stale,
            pinned_sessions=config.pinned_sessions, folder_name=group["folder_name"],
            session_count=group["total_count"], last_updated=group["latest_updated"],
            is_pinned=True,
            workspace_color=workspace_color,
            providers=group["providers"],
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
                cards_html += templates.get_template("partials/workspace_card.html").render(
                    request=request, cwd=cwd, sessions=[], stale=stale,
                    pinned_sessions=config.pinned_sessions, folder_name=group["folder_name"],
                    session_count=group["total_count"], last_updated=group["latest_updated"],
                    is_pinned=False,
                    workspace_color=workspace_color,
                    providers=group["providers"],
                )

    if not cards_html:
        return templates.TemplateResponse(request, "partials/empty_state.html", {
            "message": f'No results for "{q}"',
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
        "custom_launchers": config.custom_launchers,
        "autostart": autostart_enabled,
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
}


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
    # String-specific validation (applies to peek_hotkey, default_directory)
    if expected_type is str:
        if len(value) > 512:
            return {"ok": False, "error": f"{key} too long (max 512 chars)"}
        if any(ord(ch) < 0x20 for ch in value):
            return {"ok": False, "error": f"{key} contains invalid control characters"}
    config = load_config()
    setattr(config, key, value)
    save_config(config)
    return {"ok": True}


@app.get("/partials/session-tail", response_class=HTMLResponse)
async def partials_session_tail(request: Request, sid: str = "", provider: str = "kiro-cli", cwd: str = ""):
    messages = await asyncio.to_thread(data.get_session_tail, sid, provider, cwd)
    first_prompt = await asyncio.to_thread(data.get_first_prompt, sid, provider, cwd)
    if not messages and not first_prompt:
        return HTMLResponse('<div class="tail-empty">No recent output</div>')
    # Look up session title from cache
    session_title = ""
    cached_sessions = data.session_cache.get(cwd, provider)
    if cached_sessions:
        for s in cached_sessions:
            if s.session_id == sid:
                session_title = s.title
                break
    # Derive workspace name from cwd
    workspace_name = Path(cwd).name if cwd else ""
    return templates.TemplateResponse(request, "partials/session_tail.html", {
        "first_prompt": first_prompt,
        "messages": messages,
        "session_title": session_title,
        "workspace_name": workspace_name,
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
    return config.custom_launchers


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
            for k in ("name", "command", "custom_args", "cwd", "env", "color", "terminal", "use_selected_workspaces"):
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
    result = launcher.launch_custom(
        name=body.get("name", ""),
        command=body.get("command", ""),
        custom_args=body.get("custom_args", ""),
        cwd=body.get("cwd", ""),
        env=body.get("env"),
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
