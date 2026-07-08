"""FastAPI web application with htmx-powered UI."""

import asyncio
import logging
import re
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import load_config, save_config, get_active_launch_profile, LaunchProfile
from . import autostart, data, icons, launcher

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


def _enabled(config, prov: str) -> bool:
    """Return whether a provider is enabled in the config."""
    return config.provider_settings.get(prov, {}).get("enabled", True)


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


@app.middleware("http")
async def same_origin_guard(request: Request, call_next):
    """Reject cross-origin POST requests to prevent CSRF."""
    if request.method == "POST":
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
async def partials_workspaces(request: Request, provider: str = "all", fresh: int = 0):
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
    norm_icons = {_normalize_path(k): v for k, v in config.workspace_icons.items()}
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

    for group in pinned_grouped:
        cwd = group["cwd"]
        stale = not Path(cwd).exists()
        if provider != "all":
            session_count = sum(p["count"] for p in group["providers"] if p["name"] == provider)
        else:
            session_count = group["total_count"]
        cards_html += templates.get_template("partials/workspace_card.html").render(
            request=request, cwd=cwd, sessions=[], stale=stale,
            pinned_sessions=config.pinned_sessions, folder_name=group["folder_name"],
            session_count=session_count, is_pinned=True,
            last_updated=group["latest_updated"],
            icon=norm_icons.get(_normalize_path(cwd), ""),
            providers=group["providers"],
        )

    # --- Non-pinned workspaces (by recency) ---
    other_data = [(c, n, u, p) for c, n, u, p in workspace_data if _normalize_path(c) not in pinned_norm_paths]
    other_data = [(c, n, u, p) for c, n, u, p in other_data if _enabled(config, p)]
    other_grouped = _group_workspaces(other_data, config)
    if provider != "all":
        other_grouped = [g for g in other_grouped if any(prov["name"] == provider for prov in g["providers"])]

    for group in other_grouped:
        cwd = group["cwd"]
        stale = not Path(cwd).exists()
        if provider != "all":
            session_count = sum(p["count"] for p in group["providers"] if p["name"] == provider)
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

    if not cards_html:
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

    log.info("Rendered workspace cards in %.2fs total", time.perf_counter() - t0)
    return HTMLResponse(cards_html)


@app.get("/partials/all-sessions", response_class=HTMLResponse)
async def partials_all_sessions(request: Request, page: int = 1, provider: str = "all", q: str = ""):
    """Render paginated all-sessions panel. Pinned at top, then by updated_at."""
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

    html = ""
    for session, prov_name in sessions_with_prov:
        html += templates.get_template("partials/session_row.html").render(
            request=request, session=session, cwd=session.cwd,
            stale=not Path(session.cwd).exists(),
            pinned_sessions=config.pinned_sessions,
            provider_name=prov_name,
            provider_color=_get_provider_color(prov_name, config),
            show_workspace=True,
            workspace_name=Path(session.cwd).name if session.cwd else "",
        )

    if not html:
        html = '<div class="empty-state">No sessions found.</div>'

    if has_more:
        next_page = page + 1
        html += f'<button class="load-more-btn" onclick="loadMoreSessions({next_page})">Load more</button>'

    return HTMLResponse(html)


@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = "", provider: str = "all"):
    query = q.strip().lower()
    if not query:
        return await partials_workspaces(request, provider=provider)

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
    config_icons = {_normalize_path(k): v for k, v in config.workspace_icons.items()}
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

    cards_html = ""
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
    providers = [p for p in providers if config.provider_settings.get(p, {}).get("enabled", True)]
    return [{"name": p, "display": PROVIDER_DISPLAY_NAMES.get(p, p), "color": _get_provider_color(p, config)} for p in providers]


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
    results = launcher.launch_batch(
        sessions=body["sessions"],
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
    default_args = config.provider_settings.get(provider, {}).get("default_args", "")
    result = launcher.launch_session(
        cwd=body["workspace"],
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
