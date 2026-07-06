"""FastAPI web application with htmx-powered UI."""

import asyncio
import logging
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import load_config, save_config
from . import autostart, data, icons, launcher
from .launcher import available_terminals

PROVIDER_COLORS = {
    "kiro-cli": "#7138cc",
    "claude-code": "#c2590f",
    "kiro-ide": "#8b5cf6",
}
PROVIDER_DISPLAY_NAMES = {
    "kiro-cli": "Kiro CLI",
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


def _get_provider_color(provider: str, config) -> str:
    """Return user-configured color for a provider, falling back to PROVIDER_COLORS."""
    user_color = config.provider_settings.get(provider, {}).get("color", "")
    return user_color or PROVIDER_COLORS.get(provider, "#888")

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


def _terminal_context() -> dict:
    """Build template context for terminal selection UI."""
    options = available_terminals()
    values = {v for v, _ in options}
    no_found = len(options) == 2  # only Auto-detect + Custom
    if no_found:
        if sys.platform == "win32":
            hint = "No terminal detected. Install Windows Terminal or PowerShell, or configure a custom terminal."
        else:
            hint = "No terminal detected. Install one of: kitty, alacritty, gnome-terminal, konsole, or xterm."
    else:
        hint = ""
    return {
        "terminal_options": options,
        "terminal_values": values,
        "autostart_label": "Start at login" if sys.platform != "win32" else "Start with Windows",
        "no_terminals_found": no_found,
        "no_terminals_hint": hint,
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    config = load_config()
    ctx = _terminal_context()
    return templates.TemplateResponse(request, "index.html", {
        "port": config.port,
        "terminal_command": config.terminal_command,
        "autostart": autostart.is_enabled(),
        "launchers": config.custom_launchers,
        "peek_hotkey": config.peek_hotkey,
        "provider_settings": config.provider_settings,
        **ctx,
    })


@app.post("/api/autostart")
async def toggle_autostart():
    if autostart.is_enabled():
        autostart.disable()
    else:
        autostart.enable()
    return {"enabled": autostart.is_enabled()}


@app.post("/api/set-workspace-icon")
async def set_workspace_icon(request: Request):
    body = await request.json()
    config = load_config()
    from .data import _normalize_path
    workspace = _normalize_path(body["workspace"])
    icon = body.get("icon", "")
    if icon:
        config.workspace_icons[workspace] = icon
    else:
        config.workspace_icons.pop(workspace, None)
    save_config(config)
    return {"ok": True}


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


@app.get("/partials/pinned-sessions", response_class=HTMLResponse)
async def partials_pinned_sessions(request: Request, provider: str = "all", fresh: int = 0):
    """Render pinned sessions for the left panel."""
    import asyncio
    if fresh:
        data._cache.pop("workspaces_with_counts:all", None)

    config = load_config()
    cards_html = ""

    if config.pinned_sessions:
        # Only run full warmup on fresh requests (initial load / manual refresh)
        if fresh:
            await asyncio.to_thread(data.warmup_all, [], config.pinned_sessions)
        pinned_rows = await _render_pinned_sessions(request, config, provider=provider)
        if pinned_rows:
            cards_html += '<div class="section-label">Pinned sessions</div>'
            cards_html += '<div class="pinned-sessions-list">' + pinned_rows + '</div>'

    if not cards_html:
        cards_html = '<div class="empty-state">No pinned sessions.</div>'

    return HTMLResponse(cards_html)


@app.get("/partials/pinned-workspaces", response_class=HTMLResponse)
async def partials_pinned_workspaces(request: Request, provider: str = "all", fresh: int = 0):
    """Render pinned workspaces for the center panel."""
    import asyncio
    import time
    t0 = time.perf_counter()
    if fresh:
        data._cache.pop("workspaces_with_counts:all", None)

    config = load_config()
    from .data import _normalize_path
    norm_icons = {_normalize_path(k): v for k, v in config.workspace_icons.items()}

    cards_html = ""

    # Build pinned set by normalized path only (workspace-level, not provider-specific)
    pinned_norm_paths: set[str] = set()
    for folder in config.pinned_folders:
        pinned_norm_paths.add(_normalize_path(folder))

    if pinned_norm_paths:
        try:
            all_workspace_data = list(await asyncio.to_thread(data.discover_workspaces_with_counts, provider=None))
        except Exception:
            all_workspace_data = []
        # Merge pinned folders not found in discovery results
        all_existing_norms = {_normalize_path(c) for c, _, _, _ in all_workspace_data}
        for folder in config.pinned_folders:
            if _normalize_path(folder) not in all_existing_norms:
                all_workspace_data.append((folder, 0, "", ""))
                all_existing_norms.add(_normalize_path(folder))
        # Filter to pinned paths only
        pinned_data = [(c, n, u, p) for c, n, u, p in all_workspace_data if _normalize_path(c) in pinned_norm_paths]
        # Group by normalized path
        grouped = _group_workspaces(pinned_data, config)

        # If filtering by a specific provider, only show groups that include that provider
        if provider != "all":
            grouped = [g for g in grouped if any(prov["name"] == provider for prov in g["providers"])]

        # Sort pinned by folder name
        grouped.sort(key=lambda x: x["folder_name"].lower())
        if grouped:
            cards_html += '<div class="section-label">Pinned workspaces</div>'
            for group in grouped:
                cwd = group["cwd"]
                stale = not Path(cwd).exists()
                cards_html += templates.get_template("partials/workspace_card.html").render(
                    request=request, cwd=cwd, sessions=[], stale=stale,
                    pinned_sessions=config.pinned_sessions, folder_name=group["folder_name"],
                    session_count=group["total_count"], is_pinned=True,
                    last_updated=group["latest_updated"],
                    icon=norm_icons.get(_normalize_path(cwd), ""),
                    providers=group["providers"],
                )

    if not cards_html:
        cards_html = '<div class="empty-state">No pinned workspaces. Pin a workspace to see it here.</div>'

    log.info("Rendered pinned workspaces in %.2fs", time.perf_counter() - t0)
    return HTMLResponse(cards_html)


@app.get("/partials/workspaces", response_class=HTMLResponse)
async def partials_workspaces(request: Request, provider: str = "all", fresh: int = 0):
    """Render non-pinned workspaces with provider tabs for the right panel."""
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
    norm_icons = {_normalize_path(k): v for k, v in config.workspace_icons.items()}
    workspace_data = list(workspace_data)

    cards_html = ""
    # Build pinned set by normalized path only (workspace-level)
    pinned_norm_paths: set[str] = set()
    for folder in config.pinned_folders:
        pinned_norm_paths.add(_normalize_path(folder))

    # Filter to non-pinned workspaces only (by normalized path)
    other_data = [(c, n, u, p) for c, n, u, p in workspace_data if _normalize_path(c) not in pinned_norm_paths]

    # Group by normalized path
    grouped = _group_workspaces(other_data, config)

    # If filtering by a specific provider, only show groups that include that provider
    if provider != "all":
        grouped = [g for g in grouped if any(prov["name"] == provider for prov in g["providers"])]

    if not grouped:
        if provider != "all" and provider:
            empty_msgs = {
                "claude-code": "No Claude Code sessions found \u2014 start one with <code>claude</code> to see it here.",
                "kiro-cli": "No Kiro CLI sessions found \u2014 start one with <code>kiro-cli</code> to see it here.",
                "kiro-ide": "No Kiro IDE sessions found \u2014 open a folder in Kiro IDE and start a conversation to see it here.",
            }
            msg = empty_msgs.get(provider, f"No {provider} sessions found.")
            cards_html += f'<div class="empty-state">{msg}</div>'
        else:
            cards_html += '<div class="empty-state">No workspaces found yet.</div>'
        return HTMLResponse(cards_html)

    for group in grouped:
        cwd = group["cwd"]
        stale = not Path(cwd).exists()
        # Filter-aware count: show provider-specific count when filtered, total when "all"
        if provider != "all":
            prov_count = sum(p["count"] for p in group["providers"] if p["name"] == provider)
            session_count = prov_count
        else:
            session_count = group["total_count"]
        cards_html += templates.get_template("partials/workspace_card.html").render(
            request=request, cwd=cwd, sessions=[], stale=stale,
            pinned_sessions=config.pinned_sessions, folder_name=group["folder_name"],
            session_count=session_count, is_pinned=False,
            last_updated=group["latest_updated"],
            icon=norm_icons.get(_normalize_path(cwd), ""),
            providers=group["providers"],
        )
    log.info("Rendered %d workspace cards in %.2fs total", len(grouped), time.perf_counter() - t0)
    return HTMLResponse(cards_html)


@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = ""):
    query = q.strip().lower()
    if not query:
        return await partials_workspaces(request)

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

    # Search pinned sessions by title across all providers
    pinned_rows = ""
    if config.pinned_sessions:
        pinned_set = set(config.pinned_sessions)
        # Check cache first for pinned session titles
        for prov_name in data.PROVIDERS:
            for norm_cwd in data.session_cache.get_loaded_cwds(prov_name):
                cached = data.session_cache.get(norm_cwd, prov_name)
                if not cached:
                    continue
                for session in cached:
                    if session.session_id in pinned_set and query in (session.title or "").lower():
                        cwd = session.cwd
                        pinned_rows += templates.get_template("partials/session_row.html").render(
                            request=request, session=session, cwd=cwd, stale=not Path(cwd).exists(),
                            pinned_sessions=config.pinned_sessions, folder_name=Path(cwd).name or cwd,
                            provider_name=prov_name,
                            show_workspace=True,
                            workspace_name=Path(cwd).name if cwd else "",
                        )
                        pinned_set.discard(session.session_id)
        # Fallback: scan all providers for remaining pinned sessions not in cache
        if pinned_set:
            for sid in list(pinned_set):
                result = data._find_pinned_session_workspace(sid)
                if not result:
                    continue
                cwd_found, prov_found = result
                try:
                    sessions = data.get_sessions(cwd_found, prov_found)
                except OSError:
                    continue
                for session in sessions:
                    if session.session_id == sid:
                        title = session.title or ""
                        if query in title.lower():
                            provider_color = _get_provider_color(prov_found, config)
                            pinned_rows += templates.get_template("partials/session_row.html").render(
                                request=request, session=session, cwd=cwd_found,
                                stale=not Path(cwd_found).exists(),
                                pinned_sessions=config.pinned_sessions,
                                folder_name=Path(cwd_found).name or cwd_found,
                                provider_name=prov_found, provider_color=provider_color,
                                show_workspace=True,
                                workspace_name=Path(cwd_found).name if cwd_found else "",
                            )
                        pinned_set.discard(sid)
                        break

    if not matched and not pinned_rows:
        return templates.TemplateResponse(request, "partials/empty_state.html", {
            "message": f'No results for "{q}"',
        })

    cards_html = ""
    if pinned_rows:
        cards_html += '<div class="section-label">Pinned sessions</div>'
        cards_html += '<div class="pinned-sessions-list">' + pinned_rows + '</div>'

    from .data import _normalize_path
    config_icons = {_normalize_path(k): v for k, v in config.workspace_icons.items()}
    pinned_norm_paths: set[str] = set()
    for folder in config.pinned_folders:
        pinned_norm_paths.add(_normalize_path(folder))

    # Group matched workspaces
    grouped = _group_workspaces(matched, config)
    for group in grouped:
        cwd = group["cwd"]
        stale = not Path(cwd).exists()
        cards_html += templates.get_template("partials/workspace_card.html").render(
            request=request, cwd=cwd, sessions=[], stale=stale,
            pinned_sessions=config.pinned_sessions, folder_name=group["folder_name"],
            session_count=group["total_count"], last_updated=group["latest_updated"],
            is_pinned=_normalize_path(cwd) in pinned_norm_paths,
            icon=config_icons.get(_normalize_path(cwd), ""),
            providers=group["providers"],
        )
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


@app.get("/api/available-providers")
async def api_available_providers():
    """Return list of available (enabled) providers with display names and colors."""
    providers = data.available_providers()
    config = load_config()
    providers = [p for p in providers if config.provider_settings.get(p, {}).get("enabled", True)]
    return [{"name": p, "display": PROVIDER_DISPLAY_NAMES.get(p, p), "color": _get_provider_color(p, config)} for p in providers]


@app.get("/api/provider/{key}")
async def get_provider_settings(key: str):
    config = load_config()
    settings = config.provider_settings.get(key, {"default_args": "", "color": "", "enabled": True})
    return {"provider": key, **settings}


@app.post("/api/provider/save", response_class=HTMLResponse)
async def save_provider_settings(request: Request):
    body = await request.json()
    provider = body.get("provider", "")
    if not provider:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Missing provider key", "level": "error",
        })
    config = load_config()
    config.provider_settings[provider] = {
        "default_args": body.get("default_args", ""),
        "color": body.get("color", ""),
        "enabled": body.get("enabled", True),
    }
    save_config(config)
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": f"Provider settings saved", "level": "success",
    })


_SETTING_TYPES: dict[str, type] = {
    "port": int,
    "peek_hotkey": str,
    "terminal_command": str,
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
    return templates.TemplateResponse(request, "partials/session_tail.html", {
        "first_prompt": first_prompt,
        "messages": messages,
    })


@app.get("/partials/sessions", response_class=HTMLResponse)
async def partials_sessions(request: Request, cwd: str = "", provider: str = "all", fresh: int = 0):
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
    html = ""
    for session in flat_sessions:
        prov_name = prov_map.get(id(session), provider if provider != "all" else "kiro-cli")
        html += templates.get_template("partials/session_row.html").render(
            request=request, session=session, cwd=cwd, stale=stale,
            pinned_sessions=config.pinned_sessions,
            provider_name=prov_name,
            provider_color=_get_provider_color(prov_name, config),
        )
    return HTMLResponse(html)


@app.post("/api/launch", response_class=HTMLResponse)
async def api_launch(request: Request):
    body = await request.json()
    config = load_config()
    provider = body.get("provider") or "kiro-cli"
    default_args = config.provider_settings.get(provider, {}).get("default_args", "")
    result = launcher.launch_session(
        cwd=body["workspace"],
        session_id=body.get("session_id"),
        provider=provider,
        default_args=default_args,
        terminal_override=config.terminal_command,
    )
    level = "success" if result.success else "error"
    msg = "Session launched" if result.success else result.error
    return templates.TemplateResponse(request, "partials/toast.html", {"message": msg, "level": level})


@app.post("/api/launch-batch", response_class=HTMLResponse)
async def api_launch_batch(request: Request):
    body = await request.json()
    config = load_config()
    results = launcher.launch_batch(
        sessions=body["sessions"],
        terminal_override=config.terminal_command,
        provider_settings=config.provider_settings,
    )
    ok = sum(1 for r in results if r.success)
    failed = len(results) - ok
    msg = f"Launched {ok} session{'s' if ok != 1 else ''}"
    if failed:
        msg += f", {failed} failed"
    level = "success" if not failed else ("warning" if ok else "error")
    return templates.TemplateResponse(request, "partials/toast.html", {"message": msg, "level": level})


@app.post("/api/new-session", response_class=HTMLResponse)
async def api_new_session(request: Request):
    body = await request.json()
    config = load_config()
    provider = body.get("provider") or "kiro-cli"
    default_args = config.provider_settings.get(provider, {}).get("default_args", "")
    result = launcher.launch_session(
        cwd=body["workspace"],
        session_id=None,
        provider=provider,
        default_args=default_args,
        terminal_override=config.terminal_command,
    )
    level = "success" if result.success else "error"
    msg = "New session launched" if result.success else result.error
    return templates.TemplateResponse(request, "partials/toast.html", {"message": msg, "level": level})


async def _render_pinned_sessions(request, config, provider: str = "all") -> str:
    """Render pinned sessions as flat rows. Uses cache when available for full prompts.

    Args:
        provider: Filter to only show sessions from this provider. "all" shows all.
    """
    from .data import _normalize_path

    pinned_ids = set(config.pinned_sessions)
    html = ""

    # Try cache first: find pinned sessions in any cached workspace
    found_ids: set[str] = set()
    providers_to_check = [provider] if provider != "all" else list(data.PROVIDERS.keys())
    for prov_name in providers_to_check:
        if prov_name not in data.PROVIDERS:
            continue
        for norm_cwd in data.session_cache.get_loaded_cwds(prov_name):
            cached = data.session_cache.get(norm_cwd, prov_name)
            if not cached:
                continue
            for session in cached:
                if session.session_id in pinned_ids and session.session_id not in found_ids:
                    found_ids.add(session.session_id)
                    cwd = session.cwd
                    html += templates.get_template("partials/session_row.html").render(
                        request=request, session=session, cwd=cwd, stale=not Path(cwd).exists(),
                        pinned_sessions=config.pinned_sessions,
                        provider_color=PROVIDER_COLORS.get(prov_name, ""),
                        provider_name=prov_name,
                        show_workspace=True,
                        workspace_name=Path(cwd).name if cwd else "",
                    )

    # Fallback: pinned sessions not found in cache — use generic resolution
    remaining = pinned_ids - found_ids
    if remaining:
        for sid in list(remaining):
            result = data._find_pinned_session_workspace(sid)
            if result:
                cwd_found, prov_found = result
                # Check provider filter
                if provider != "all" and prov_found != provider:
                    remaining.discard(sid)
                    continue
                try:
                    sessions = data.get_sessions(cwd_found, prov_found)
                except Exception:
                    sessions = []
                for s in sessions:
                    if s.session_id == sid:
                        found_ids.add(sid)
                        html += templates.get_template("partials/session_row.html").render(
                            request=request, session=s, cwd=s.cwd,
                            stale=not Path(s.cwd).exists(),
                            pinned_sessions=config.pinned_sessions,
                            provider_color=PROVIDER_COLORS.get(prov_found, ""),
                            provider_name=prov_found,
                            show_workspace=True,
                            workspace_name=Path(s.cwd).name if s.cwd else "",
                        )
                        break
                remaining.discard(sid)
    return html


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
        providers = data.available_providers()
    except Exception:
        providers = []
    for p in providers:
        settings = config.provider_settings.get(p, {})
        if not settings.get("enabled", True):
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
        terminal_override=config.terminal_command,
        use_terminal=use_terminal,
    )
    level = "success" if result.success else "error"
    msg = "Launcher started" if result.success else result.error
    return templates.TemplateResponse(request, "partials/toast.html", {"message": msg, "level": level})


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
        terminal_override=config.terminal_command,
        use_terminal=entry.get("terminal", True),
        pass_workspace_arg=not entry.get("terminal", True) and entry.get("use_selected_workspaces", False),
    )
    ok = sum(1 for r in results if r.success)
    failed = len(results) - ok
    msg = f"Launched {ok} instance{'s' if ok != 1 else ''}"
    if failed:
        msg += f", {failed} failed"
    level = "success" if not failed else ("warning" if ok else "error")
    return templates.TemplateResponse(request, "partials/toast.html", {"message": msg, "level": level})


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
