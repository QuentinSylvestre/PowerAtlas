"""FastAPI web application with htmx-powered UI."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import load_config, save_config
from . import autostart, data, launcher

_PKG_DIR = Path(__file__).parent
_TEMPLATES_DIR = _PKG_DIR / "templates"
_STATIC_DIR = _PKG_DIR / "static"
log = logging.getLogger("kiro_orchestrator.web")


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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    config = load_config()
    return templates.TemplateResponse(request, "index.html", {
        "trust_all_tools": config.trust_all_tools,
        "terminal_command": config.terminal_command,
        "autostart": autostart.is_enabled(),
    })


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    config = load_config()
    return templates.TemplateResponse(request, "settings.html", {
        "config": config,
        "autostart_enabled": autostart.is_enabled(),
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
    config.use_pywebview = "use_pywebview" in form
    config.trust_all_tools = "trust_all_tools" in form
    # Pinned folders from hidden field
    folders_raw = form.get("pinned_folders", "")
    config.pinned_folders = [f for f in folders_raw.split("|") if f.strip()] if folders_raw else []
    save_config(config)
    return templates.TemplateResponse(request, "settings.html", {
        "config": config,
        "autostart_enabled": autostart.is_enabled(),
    })


@app.post("/api/autostart")
async def toggle_autostart():
    if autostart.is_enabled():
        autostart.disable()
    else:
        autostart.enable()
    return {"enabled": autostart.is_enabled()}


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
async def partials_workspaces(request: Request):
    import asyncio
    import time
    t0 = time.perf_counter()
    try:
        workspace_data = await asyncio.to_thread(data.discover_workspaces_with_counts)
        log.info("Discovered %d workspaces in %.2fs", len(workspace_data), time.perf_counter() - t0)
    except Exception:
        log.exception("Failed to discover workspaces")
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Error: could not load session data",
            "level": "error",
        })

    config = load_config()
    # Merge pinned folders (with count=0)
    from .data import _normalize_path
    workspace_data = list(workspace_data)
    existing = {_normalize_path(cwd) for cwd, _, _ in workspace_data}
    for pf in config.pinned_folders:
        if _normalize_path(pf) not in existing:
            workspace_data.append((pf, 0, ""))

    if not workspace_data:
        return templates.TemplateResponse(request, "partials/empty_state.html", {
            "message": "No sessions found. Pin a folder to get started.",
        })

    pinned_set = {_normalize_path(f) for f in config.pinned_folders}

    cards_html = ""

    # Pinned sessions section (flat list, no card wrapper)
    if config.pinned_sessions:
        pinned_rows = await _render_pinned_sessions(request, config)
        if pinned_rows:
            cards_html += '<div class="section-label">Pinned sessions</div>'
            cards_html += '<div class="pinned-sessions-list">' + pinned_rows + '</div>'

    # Pinned workspaces (deduplicate by normalized path, keep highest count)
    pinned_cards_raw = [(c, n, u) for c, n, u in workspace_data if _normalize_path(c) in pinned_set]
    pinned_seen: dict[str, tuple[str, int, str]] = {}
    for c, n, u in pinned_cards_raw:
        key = _normalize_path(c)
        if key not in pinned_seen or n > pinned_seen[key][1]:
            pinned_seen[key] = (c, n, u)
    pinned_cards = list(pinned_seen.values())
    if pinned_cards:
        cards_html += '<div class="section-label">Pinned workspaces</div>'
        for cwd, count, updated in pinned_cards:
            stale = not Path(cwd).exists()
            cards_html += templates.get_template("partials/workspace_card.html").render(
                request=request, cwd=cwd, sessions=[], stale=stale,
                pinned_sessions=config.pinned_sessions, folder_name=Path(cwd).name or cwd,
                session_count=count, is_pinned=True, last_updated=updated,
            )

    # All other workspaces
    other_cards = [(c, n, u) for c, n, u in workspace_data if _normalize_path(c) not in pinned_set]
    if other_cards:
        if pinned_cards:
            cards_html += '<div class="section-label">All workspaces</div>'
        for cwd, count, updated in other_cards:
            stale = not Path(cwd).exists()
            cards_html += templates.get_template("partials/workspace_card.html").render(
                request=request, cwd=cwd, sessions=[], stale=stale,
                pinned_sessions=config.pinned_sessions, folder_name=Path(cwd).name or cwd,
                session_count=count, last_updated=updated,
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
    matched = [(c, n, u) for c, n, u in workspace_data if query in c.lower()]

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
    for cwd, count, updated in matched:
        stale = not Path(cwd).exists()
        cards_html += templates.get_template("partials/workspace_card.html").render(
            request=request, cwd=cwd, sessions=[], stale=stale,
            pinned_sessions=config.pinned_sessions, folder_name=Path(cwd).name or cwd,
            session_count=count, last_updated=updated,
        )
    return HTMLResponse(cards_html)


@app.post("/api/toggle-trust")
async def toggle_trust():
    config = load_config()
    config.trust_all_tools = not config.trust_all_tools
    save_config(config)
    return {"trust_all_tools": config.trust_all_tools}


@app.post("/api/save-setting")
async def save_setting(request: Request):
    body = await request.json()
    config = load_config()
    key, value = body["key"], body["value"]
    if hasattr(config, key):
        setattr(config, key, value)
        save_config(config)
    return {"ok": True}


@app.get("/partials/sessions", response_class=HTMLResponse)
async def partials_sessions(request: Request, cwd: str = ""):
    """Lazy-load sessions for a single workspace card."""
    import asyncio
    import time
    t0 = time.perf_counter()
    log.info("Loading sessions for %s", cwd[-40:])
    config = load_config()
    try:
        sessions = await asyncio.to_thread(data.get_sessions, cwd)
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
        trust_all=config.trust_all_tools,
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
        trust_all=config.trust_all_tools,
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
        trust_all=config.trust_all_tools,
        terminal_override=config.terminal_command,
    )
    level = "success" if result.success else "error"
    msg = "New session launched" if result.success else result.error
    return templates.TemplateResponse(request, "partials/toast.html", {"message": msg, "level": level})


async def _render_pinned_sessions(request, config) -> str:
    """Render pinned sessions as flat rows (finds them from metadata)."""
    import asyncio
    from .data import SESSION_DIR, _normalize_path
    import json as _json

    # Find session metadata for pinned IDs
    pinned_ids = set(config.pinned_sessions)
    html = ""
    if not SESSION_DIR.is_dir():
        return html
    for meta_file in SESSION_DIR.glob("*.json"):
        if meta_file.suffix == ".jsonl":
            continue
        if meta_file.stem not in pinned_ids:
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


async def _render_workspace_card(request: Request, cwd: str) -> HTMLResponse:
    config = load_config()
    try:
        sessions = data.get_sessions(cwd)
    except Exception:
        sessions = []
    sessions = _sort_pinned_first(sessions, config.pinned_sessions)
    stale = not Path(cwd).exists()
    html = templates.get_template("partials/workspace_card.html").render(
        request=request, cwd=cwd, sessions=sessions, stale=stale,
        pinned_sessions=config.pinned_sessions, folder_name=Path(cwd).name or cwd,
    )
    return HTMLResponse(html)


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
