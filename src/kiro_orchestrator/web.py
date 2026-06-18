"""FastAPI web application with htmx-powered UI."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import load_config, save_config
from . import data

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


@app.get("/partials/workspaces", response_class=HTMLResponse)
async def partials_workspaces(request: Request):
    try:
        workspaces = data.discover_workspaces()
    except Exception:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Error: could not load session data",
            "level": "error",
        })

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
        stale = not Path(cwd).exists()
        cards_html += templates.get_template("partials/workspace_card.html").render(
            request=request, cwd=cwd, sessions=sessions, stale=stale,
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

    cards_html = ""
    for cwd in workspaces:
        try:
            sessions = data.get_sessions(cwd)
        except Exception:
            sessions = []
        matched = [s for s in sessions if _session_matches(s, query)]
        if query in cwd.lower() or matched:
            display_sessions = matched if matched else sessions
            stale = not Path(cwd).exists()
            cards_html += templates.get_template("partials/workspace_card.html").render(
                request=request, cwd=cwd, sessions=display_sessions, stale=stale,
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


def _session_matches(session: data.Session, query: str) -> bool:
    return (
        query in session.title.lower()
        or query in session.first_prompt.lower()
        or query in session.last_prompt.lower()
        or query in session.last_reply_tail.lower()
    )
