"""FastAPI web application with htmx-powered UI."""

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

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    config = load_config()
    return templates.TemplateResponse(request, "index.html", {
        "trust_all_tools": config.trust_all_tools,
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
    cwd = request.headers.get("X-Workspace", "")
    return await _render_workspace_card(request, cwd)


@app.post("/api/unpin-session")
async def unpin_session(request: Request):
    body = await request.json()
    session_id = body["session_id"]
    config = load_config()
    if session_id in config.pinned_sessions:
        config.pinned_sessions.remove(session_id)
        save_config(config)
    cwd = request.headers.get("X-Workspace", "")
    return await _render_workspace_card(request, cwd)


@app.get("/partials/workspaces", response_class=HTMLResponse)
async def partials_workspaces(request: Request):
    try:
        workspaces = data.discover_workspaces()
    except Exception:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Error: could not load session data",
            "level": "error",
        })

    config = load_config()
    workspaces = _merge_pinned_folders(workspaces, config.pinned_folders)

    if not workspaces:
        return templates.TemplateResponse(request, "partials/empty_state.html", {
            "message": "No sessions found. Pin a folder to get started.",
        })

    cards_html = ""
    for cwd in workspaces:
        try:
            sessions = data.get_sessions(cwd)
        except Exception:
            sessions = []
        sessions = _sort_pinned_first(sessions, config.pinned_sessions)
        stale = not Path(cwd).exists()
        cards_html += templates.get_template("partials/workspace_card.html").render(
            request=request, cwd=cwd, sessions=sessions, stale=stale,
            pinned_sessions=config.pinned_sessions, folder_name=Path(cwd).name or cwd,
        )
    return HTMLResponse(cards_html)


@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = ""):
    query = q.strip().lower()
    if not query:
        return await partials_workspaces(request)

    try:
        workspaces = data.discover_workspaces()
    except Exception:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Error: could not load session data",
            "level": "error",
        })

    config = load_config()
    workspaces = _merge_pinned_folders(workspaces, config.pinned_folders)

    cards_html = ""
    for cwd in workspaces:
        try:
            sessions = data.get_sessions(cwd)
        except Exception:
            sessions = []
        matched = [s for s in sessions if _session_matches(s, query)]
        if query in cwd.lower() or matched:
            display_sessions = matched if matched else sessions
            display_sessions = _sort_pinned_first(display_sessions, config.pinned_sessions)
            stale = not Path(cwd).exists()
            cards_html += templates.get_template("partials/workspace_card.html").render(
                request=request, cwd=cwd, sessions=display_sessions, stale=stale,
                pinned_sessions=config.pinned_sessions, folder_name=Path(cwd).name or cwd,
            )

    if not cards_html:
        return templates.TemplateResponse(request, "partials/empty_state.html", {
            "message": f'No results for "{q}"',
        })
    return HTMLResponse(cards_html)


@app.post("/api/toggle-trust")
async def toggle_trust():
    config = load_config()
    config.trust_all_tools = not config.trust_all_tools
    save_config(config)
    return {"trust_all_tools": config.trust_all_tools}


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


def _merge_pinned_folders(workspaces: list[str], pinned_folders: list[str]) -> list[str]:
    """Ensure pinned folders appear in workspace list even with 0 sessions."""
    import sys
    existing = set()
    for w in workspaces:
        norm = w.casefold() if sys.platform == "win32" else w
        existing.add(norm)
    for folder in pinned_folders:
        norm = folder.casefold() if sys.platform == "win32" else folder
        if norm not in existing:
            workspaces.append(folder)
            existing.add(norm)
    return workspaces


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
