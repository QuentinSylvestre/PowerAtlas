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
    "kiro-cli": "#4a6ede",
    "claude-code": "#c2590f",
}
PROVIDER_DISPLAY_NAMES = {
    "kiro-cli": "Kiro CLI",
    "claude-code": "Claude Code",
}
PROVIDER_BADGES = {
    "kiro-cli": "K",
    "claude-code": "C",
}

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
        "trust_all_tools": config.trust_all_tools,
        "terminal_command": config.terminal_command,
        "autostart": autostart.is_enabled(),
        "launchers": config.custom_launchers,
        "peek_hotkey": config.peek_hotkey,
        **ctx,
    })


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    config = load_config()
    ctx = _terminal_context()
    return templates.TemplateResponse(request, "settings.html", {
        "config": config,
        "autostart_enabled": autostart.is_enabled(),
        **ctx,
    })


@app.post("/api/settings", response_class=HTMLResponse)
async def save_settings(request: Request):
    form = await request.form()
    config = load_config()
    # Terminal
    terminal = form.get("terminal_command", "")
    if terminal == "custom":
        terminal = form.get("custom_terminal_value", "")
    config.terminal_command = terminal
    # Toggles
    config.trust_all_tools = "trust_all_tools" in form
    # Pinned folders from hidden field
    folders_raw = form.get("pinned_folders", "")
    config.pinned_folders = [f for f in folders_raw.split("|") if f.strip()] if folders_raw else []
    save_config(config)
    ctx = _terminal_context()
    return templates.TemplateResponse(request, "settings.html", {
        "config": config,
        "autostart_enabled": autostart.is_enabled(),
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


@app.get("/partials/workspaces", response_class=HTMLResponse)
async def partials_workspaces(request: Request, provider: str = "all"):
    import asyncio
    import time
    t0 = time.perf_counter()
    try:
        workspace_data = await asyncio.to_thread(
            data.discover_workspaces_with_counts,
            provider=None if provider == "all" else provider,
        )
        log.info("Discovered %d workspaces in %.2fs", len(workspace_data), time.perf_counter() - t0)
    except Exception:
        log.exception("Failed to discover workspaces")
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Error: could not load session data",
            "level": "error",
        })

    config = load_config()
    # Get available providers for tab rendering
    try:
        providers = data.available_providers()
    except Exception:
        providers = []

    # Merge pinned folders (with count=0)
    from .data import _normalize_path
    norm_icons = {_normalize_path(k): v for k, v in config.workspace_icons.items()}
    workspace_data = list(workspace_data)
    existing = {_normalize_path(cwd) for cwd, _, _, _ in workspace_data}
    for pf in config.pinned_folders:
        if _normalize_path(pf) not in existing:
            default_provider = providers[0] if providers else "kiro-cli"
            workspace_data.append((pf, 0, "", default_provider))

    # Render tab bar (only if multiple providers available)
    cards_html = ""
    if len(providers) > 1:
        cards_html += '<div class="provider-tabs" id="providerTabs" role="tablist">'
        active_cls = ' active' if provider == "all" else ''
        aria_sel = ' aria-selected="true"' if provider == "all" else ' aria-selected="false"'
        cards_html += f'<button class="provider-tab{active_cls}" role="tab"{aria_sel} hx-get="/partials/workspaces?provider=all" hx-target="#workspace-cards" hx-swap="innerHTML">All</button>'
        for p in providers:
            active_cls = ' active' if provider == p else ''
            aria_sel = ' aria-selected="true"' if provider == p else ' aria-selected="false"'
            display_name = PROVIDER_DISPLAY_NAMES.get(p, p)
            cards_html += f'<button class="provider-tab{active_cls}" role="tab"{aria_sel} hx-get="/partials/workspaces?provider={p}" hx-target="#workspace-cards" hx-swap="innerHTML">{display_name}</button>'
        cards_html += '</div>'

    if not workspace_data:
        # Provider-specific empty state
        if provider != "all" and provider:
            empty_msgs = {
                "claude-code": "No Claude Code sessions found \u2014 start one with <code>claude</code> to see it here.",
                "kiro-cli": "No Kiro CLI sessions found \u2014 start one with <code>kiro-cli</code> to see it here.",
            }
            msg = empty_msgs.get(provider, f"No {provider} sessions found.")
            cards_html += f'<div class="empty-state">{msg}</div>'
            return HTMLResponse(cards_html)
        cards_html += '<div class="empty-state">No sessions found. Pin a folder to get started.</div>'
        return HTMLResponse(cards_html)

    pinned_set = {_normalize_path(f) for f in config.pinned_folders}

    # Pinned sessions section (flat list, no card wrapper)
    if config.pinned_sessions:
        pinned_rows = await _render_pinned_sessions(request, config)
        if pinned_rows:
            cards_html += '<div class="section-label">Pinned sessions</div>'
            cards_html += '<div class="pinned-sessions-list">' + pinned_rows + '</div>'

    # Pinned workspaces (deduplicate by normalized path + provider, keep highest count)
    pinned_cards_raw = [(c, n, u, p) for c, n, u, p in workspace_data if _normalize_path(c) in pinned_set]
    pinned_seen: dict[tuple[str, str], tuple[str, int, str, str]] = {}
    for c, n, u, p in pinned_cards_raw:
        key = (_normalize_path(c), p)
        if key not in pinned_seen or n > pinned_seen[key][1]:
            pinned_seen[key] = (c, n, u, p)
    pinned_cards = list(pinned_seen.values())
    if pinned_cards:
        cards_html += '<div class="section-label">Pinned workspaces</div>'
        for cwd, count, updated, prov in pinned_cards:
            stale = not Path(cwd).exists()
            cached = data.session_cache.get(cwd, prov)
            card_sessions = _sort_pinned_first(cached, config.pinned_sessions) if cached else []
            cards_html += templates.get_template("partials/workspace_card.html").render(
                request=request, cwd=cwd, sessions=card_sessions, stale=stale,
                pinned_sessions=config.pinned_sessions, folder_name=Path(cwd).name or cwd,
                session_count=count, is_pinned=True, last_updated=updated,
                icon=norm_icons.get(_normalize_path(cwd), ""),
                provider=prov,
                provider_color=PROVIDER_COLORS.get(prov, "#888"),
                provider_badge=PROVIDER_BADGES.get(prov, "?"),
            )

    # All other workspaces
    other_cards = [(c, n, u, p) for c, n, u, p in workspace_data if _normalize_path(c) not in pinned_set]
    if other_cards:
        if pinned_cards:
            cards_html += '<div class="section-label">All workspaces</div>'
        for cwd, count, updated, prov in other_cards:
            stale = not Path(cwd).exists()
            cards_html += templates.get_template("partials/workspace_card.html").render(
                request=request, cwd=cwd, sessions=[], stale=stale,
                pinned_sessions=config.pinned_sessions, folder_name=Path(cwd).name or cwd,
                session_count=count, is_pinned=False, last_updated=updated,
                icon=norm_icons.get(_normalize_path(cwd), ""),
                provider=prov,
                provider_color=PROVIDER_COLORS.get(prov, "#888"),
                provider_badge=PROVIDER_BADGES.get(prov, "?"),
            )
    log.info("Rendered %d cards in %.2fs total", len(workspace_data), time.perf_counter() - t0)
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

    # Search pinned sessions by title
    pinned_rows = ""
    if config.pinned_sessions:
        import json as _json
        from .data import SESSION_DIR
        for meta_file in SESSION_DIR.glob("*.json"):
            if meta_file.suffix == ".jsonl" or meta_file.stem not in set(config.pinned_sessions):
                continue
            try:
                d = _json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            title = d.get("title", "")
            if query in title.lower():
                cwd = d.get("cwd", "")
                session = data.Session(
                    session_id=d.get("session_id", meta_file.stem),
                    title=title or "<untitled>", cwd=cwd,
                    created_at=d.get("created_at", ""),
                    updated_at=d.get("updated_at", ""),
                    first_prompt="", last_prompt="", last_reply_tail="",
                )
                pinned_rows += templates.get_template("partials/session_row.html").render(
                    request=request, session=session, cwd=cwd, stale=not Path(cwd).exists(),
                    pinned_sessions=config.pinned_sessions, folder_name=Path(cwd).name or cwd,
                )

    if not matched and not pinned_rows:
        return templates.TemplateResponse(request, "partials/empty_state.html", {
            "message": f'No results for "{q}"',
        })

    cards_html = ""
    if pinned_rows:
        cards_html += '<div class="section-label">Pinned sessions</div>'
        cards_html += '<div class="pinned-sessions-list">' + pinned_rows + '</div>'
    config_icons = {data._normalize_path(k): v for k, v in config.workspace_icons.items()}
    pinned_set = {data._normalize_path(f) for f in config.pinned_folders}
    for cwd, count, updated, prov in matched:
        stale = not Path(cwd).exists()
        cards_html += templates.get_template("partials/workspace_card.html").render(
            request=request, cwd=cwd, sessions=[], stale=stale,
            pinned_sessions=config.pinned_sessions, folder_name=Path(cwd).name or cwd,
            session_count=count, last_updated=updated,
            is_pinned=(data._normalize_path(cwd) in pinned_set),
            icon=config_icons.get(data._normalize_path(cwd), ""),
            provider=prov,
            provider_color=PROVIDER_COLORS.get(prov, "#888"),
            provider_badge=PROVIDER_BADGES.get(prov, "?"),
        )
    return HTMLResponse(cards_html)


@app.post("/api/toggle-trust")
async def toggle_trust():
    config = load_config()
    config.trust_all_tools = not config.trust_all_tools
    save_config(config)
    return {"trust_all_tools": config.trust_all_tools}


@app.post("/api/refresh")
async def api_refresh():
    import asyncio
    data.session_cache.clear()
    data._cache.clear()
    config = load_config()
    await asyncio.to_thread(data.warmup_all, config.pinned_folders, config.pinned_sessions)
    return {"last_refresh": data.session_cache.last_refresh}


@app.get("/api/last-refresh")
async def api_last_refresh():
    return {"last_refresh": data.session_cache.last_refresh}


_SETTING_TYPES: dict[str, type] = {
    "trust_all_tools": bool,
    "terminal_command": str,
    "peek_hotkey": str,
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
    if not isinstance(value, expected_type):
        return {"ok": False, "error": f"Invalid type for {key}"}
    if expected_type is list and not all(isinstance(x, str) for x in value):
        return {"ok": False, "error": f"All elements of {key} must be strings"}
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
async def partials_sessions(request: Request, cwd: str = "", provider: str = "kiro-cli"):
    """Lazy-load sessions for a single workspace card."""
    import asyncio
    import time
    t0 = time.perf_counter()
    log.info("Loading sessions for %s", cwd[-40:])
    config = load_config()
    try:
        sessions = await asyncio.to_thread(data.get_sessions, cwd, provider)
    except Exception:
        sessions = []
    log.info("Got %d sessions for %s in %.2fs", len(sessions), Path(cwd).name, time.perf_counter() - t0)
    sessions = _sort_pinned_first(sessions, config.pinned_sessions)
    if not sessions:
        return HTMLResponse('<div class="new-session-inline">+ New session</div>')
    stale = not Path(cwd).exists()
    html = ""
    for session in sessions:
        html += templates.get_template("partials/session_row.html").render(
            request=request, session=session, cwd=cwd, stale=stale,
            pinned_sessions=config.pinned_sessions,
        )
    return HTMLResponse(html)


@app.post("/api/launch", response_class=HTMLResponse)
async def api_launch(request: Request):
    body = await request.json()
    config = load_config()
    result = launcher.launch_session(
        cwd=body["workspace"],
        session_id=body.get("session_id"),
        provider=body.get("provider", "kiro-cli"),
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
    result = launcher.launch_session(
        cwd=body["workspace"],
        session_id=None,
        provider=body.get("provider", "kiro-cli"),
        terminal_override=config.terminal_command,
    )
    level = "success" if result.success else "error"
    msg = "New session launched" if result.success else result.error
    return templates.TemplateResponse(request, "partials/toast.html", {"message": msg, "level": level})


async def _render_pinned_sessions(request, config) -> str:
    """Render pinned sessions as flat rows. Uses cache when available for full prompts."""
    from .data import SESSION_DIR, _normalize_path
    import json as _json

    pinned_ids = set(config.pinned_sessions)
    html = ""

    # Try cache first: find pinned sessions in any cached workspace
    found_ids: set[str] = set()
    for prov_name in data.PROVIDERS:
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
                    )

    # Fallback: pinned sessions not found in cache — read metadata directly (empty prompts)
    remaining = pinned_ids - found_ids
    if remaining and SESSION_DIR.is_dir():
        for meta_file in SESSION_DIR.glob("*.json"):
            if meta_file.suffix == ".jsonl":
                continue
            if meta_file.stem not in remaining:
                continue
            try:
                d = _json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            cwd = d.get("cwd", "")
            session = data.Session(
                session_id=d.get("session_id", meta_file.stem),
                title=d.get("title", "<untitled>"),
                cwd=cwd,
                created_at=d.get("created_at", ""),
                updated_at=d.get("updated_at", ""),
                first_prompt="", last_prompt="", last_reply_tail="",
            )
            html += templates.get_template("partials/session_row.html").render(
                request=request, session=session, cwd=cwd, stale=not Path(cwd).exists(),
                pinned_sessions=config.pinned_sessions,
            )
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
            for k in ("name", "command", "custom_args", "cwd", "env", "color", "terminal"):
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

    if icons.has_icon(launcher_id):
        return FileResponse(icons.icon_path(launcher_id), media_type="image/png")
    # Determine if terminal launcher for appropriate fallback
    config = load_config()
    is_terminal = True
    for entry in config.custom_launchers:
        if entry["id"] == launcher_id:
            is_terminal = entry.get("terminal", True)
            break
    svg = icons.default_icon_svg(is_terminal)
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
