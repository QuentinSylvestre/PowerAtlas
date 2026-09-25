"""FastAPI application serving the PowerAtlas UI over two surfaces.

The htmx-driven pages and partials are ordinary request/response routes. The
Agent orchestrator adds a second surface — the ``/ws/acp`` WebSocket — which is
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
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import jinja2 as _jinja2

from fastapi import (BackgroundTasks, FastAPI, HTTPException, Request,
                     Response, WebSocket)
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import (load_config, save_config, ConfigUnreadableError,
                     unreadable_config_message, get_active_launch_profile,
                     LaunchProfile, ensure_remote_secret, load_remote_secret,
                     rotate_remote_secret, validate_remote_bind_address,
                     REMOTE_SECRET_MIN_LEN, REMOTE_SECRET_PATH,
                     ensure_local_secret, hold_local_secret_in_memory,
                     local_secret_status, rotate_local_secret,
                     ACP_PERMISSION_MODES)
from . import agent_profile, autostart, data, icons, launcher, notifications, presence
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
        "Agent orchestrator failed to import: /acp is disabled, the rest of the UI is "
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
    "claude-code": "#c2590f",
    "kiro-ide": "#8b5cf6",
    "kiro-cli-v3": "#7138cc",
}
PROVIDER_DISPLAY_NAMES = {
    "claude-code": "Claude Code",
    "kiro-ide": "Kiro IDE",
    "kiro-cli-v3": "kiro-cli v3",
}
PROVIDER_BADGES = {
    "claude-code": "C",
    "kiro-ide": "I",
    "kiro-cli-v3": "V",  # V for v3; unused today but kept for consistency
}
_PROVIDER_BINARY_DISPLAY = {
    "claude-code": "claude",
    "kiro-ide": "kiro",
    "kiro-cli-v3": "kiro-cli chat --agent-engine v3 --trust-tools *",
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
    """Resolve a workspace's own accent color: explicit workspace color, else
    its first tag's color, else "" (the rail then draws no stripe at all --
    dashboard/ACP-merge QA follow-up, the group's `color` field in
    `_acp_listing`'s `meta`). Deliberately not a provider color: a
    workspace's sessions can span several providers, so no single provider
    color would represent the group, only its own tags meaningfully can."""
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


def _session_is_live(snapshot, session, provider: str) -> bool:
    """Cheap liveness gate: is a process for this exact session running.

    Two signals, no transcript read: (a) the session_id is on a process
    cmdline (--resume-id), OR (b) a provider process is running in the
    session's cwd AND the session's JSONL was written recently (a single
    mtime stat, not a parse) — recency avoids false-positive dots on an old
    session that happens to share a workspace with an unrelated running
    process. Factored out of `_session_status` so a caller that only needs
    "is it alive", not the richer working/waiting/errored verdict, can skip
    the expensive transcript-tail classify entirely — that classify, not
    this gate, is what `_acp_status_for_held` reserves for held sessions
    only.
    """
    if snapshot.is_live(provider, session.cwd, session.session_id):
        return True
    from .data import _normalize_path
    norm_cwd = _normalize_path(session.cwd)
    if norm_cwd not in snapshot.live_cwds({provider}):
        return False
    from .status_classifier import _resolve_jsonl_path
    import os, time as _time
    jsonl_path = _resolve_jsonl_path(session.session_id, provider, session.cwd)
    if jsonl_path is None:
        return False
    try:
        return (_time.time() - os.path.getmtime(jsonl_path)) <= 300
    except OSError:
        return False


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


def _combined_pinned_list(
    pinned_sessions_with_prov: list[tuple],
    pinned_workspace_groups: list[dict],
    pinned_session_order: list[str],
) -> list[dict]:
    """Merge pinned sessions and pinned workspaces into one ordered list.

    Sessions come first, in `pinned_session_order` (config.pinned_sessions)
    order; workspaces follow, alphabetically by folder name — the same
    ordering each list already had on its own, just concatenated with
    sessions on top. Each entry is tagged `"kind"` ("session"/"workspace") so
    a renderer can draw either row shape. Duplicate session ids or workspace
    cwds (e.g. a stale/duplicated config entry) collapse to their first
    occurrence rather than rendering twice.
    """
    from .data import _normalize_path

    order_index = {sid: i for i, sid in enumerate(pinned_session_order)}
    seen_sessions: set[str] = set()
    session_entries = []
    for session, prov_name in pinned_sessions_with_prov:
        if session.session_id in seen_sessions:
            continue
        seen_sessions.add(session.session_id)
        session_entries.append({
            "kind": "session",
            "session": session,
            "provider_name": prov_name,
            "_order": order_index.get(session.session_id, len(order_index)),
        })
    session_entries.sort(key=lambda e: e["_order"])
    for entry in session_entries:
        del entry["_order"]

    seen_cwds: set[str] = set()
    workspace_entries = []
    for group in sorted(pinned_workspace_groups, key=lambda g: g["folder_name"].lower()):
        norm = _normalize_path(group["cwd"])
        if norm in seen_cwds:
            continue
        seen_cwds.add(norm)
        workspace_entries.append({"kind": "workspace", "group": group})

    return session_entries + workspace_entries


_PKG_DIR = Path(__file__).parent
_TEMPLATES_DIR = _PKG_DIR / "templates"
_STATIC_DIR = _PKG_DIR / "static"
log = logging.getLogger("power_atlas.web")


# Turn endings worth a desktop toast. `cancelled` and `interrupted` are
# deliberately absent: the operator caused those, so they already know.
_NOTIFY_STOP_REASONS = frozenset({"end_turn", "error"})


def _notify_label(cwd: str, session_id: str) -> str:
    """A short, human-readable name for a session in a notification.

    Workspace basename plus a session-id fragment. The fragment is not
    decoration: `_new_session_record` carries no title, so the workspace is all
    the identity a session has -- and `MAX_SESSIONS` is 8, with several
    sessions in one repository being the ordinary case rather than the
    exception, so the basename alone would render two concurrent sessions as
    indistinguishable toasts.
    """
    name = os.path.basename((cwd or "").rstrip("\\/")) or "session"
    suffix = session_id[-6:] if session_id else ""
    return f"{name} {suffix}".strip()


def _notify_from_acp(event: str, session_id: str, cwd: str, detail: str,
                     watched: bool) -> None:
    """Decide whether an ACP event earns a desktop toast, and fire it.

    Installed into `acp` at lifespan startup. `acp` supplies facts (what
    happened, whether a socket was attached); every policy judgement lives
    here, which is what keeps the protocol layer free of product decisions.

    **The watched/unwatched split is half a mechanism, and only half by
    design.** `watched` means "a WebSocket is attached", which is not "a human
    is looking" -- a backgrounded tab is still attached. The server cannot tell
    those apart, because tab visibility never crosses the wire. The other half
    lives in `acp.html`, which fires a browser `Notification` when it *is*
    attached but hidden. Between them the three states -- detached, attached
    and hidden, attached and visible -- are covered exactly once each, so
    neither surface needs to know about the other.

    Never raises: `acp._notify` already swallows and logs, but this runs on the
    event loop inside a turn boundary, so it does not rely on that.
    """
    try:
        if not load_config().notifications.get("enabled", False):
            return
        label = _notify_label(cwd, session_id)
        if event == "permission_request":
            # No `watched` gate: the turn is stopped until someone answers.
            notifications.notify_permission_needed(label, detail)
        elif event == "turn_end":
            if not watched and detail in _NOTIFY_STOP_REASONS:
                notifications.notify_turn_end(label, detail)
        elif event == "agent_error":
            if not watched:
                notifications.notify_agent_error(label, detail)
    except Exception:
        log.exception("notification dispatch failed for %s on session %s",
                      event, session_id)


async def _sync_derived_agent() -> None:
    """Regenerate `~/.kiro/agents/poweratlas-acp.md` from the current settings.

    The startup pass. 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL Phase 1: the
    derived agent is always written, in every permission mode (D-8), so this
    never deletes anything. Settings writes do not come through here; they call
    `agent_profile.apply_settings` with their change, which saves and
    regenerates under one lock (D-16).

    `asyncio.to_thread` because the work is a few synchronous file operations and
    this runs on the event loop. `load_config()` is *not* called here:
    `agent_profile.sync_from_config` reads it inside the generation lock.

    It swallows everything. `agent_profile` reports a failed generation through
    `last_generation()` rather than raising, and an unexpected bug must not abort
    startup (260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL D-10/SC-8) —
    the posture stays whatever is already on disk, and the session gate refuses
    Default while it is not in effect (260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL
    D-34).
    """
    try:
        await asyncio.to_thread(agent_profile.sync_from_config)
    except Exception:
        log.exception("derived agent sync failed; "
                      "the permission posture is unchanged")


# How long `lifespan` waits for each of its two filesystem steps (the derived
# agent sync and the local-secret load) before carrying on without it.
# `__main__` gives the whole startup 10 s before it reports "Server failed to
# start", so a stalled disk (an antivirus scan, a hung network profile) must
# not be allowed to spend that budget. `asyncio.wait_for` stops waiting; it
# cannot stop the worker thread, which finishes, or not, in the background.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review (F5)
_LIFESPAN_STEP_TIMEOUT_SECONDS = 3.0


async def _startup_sync_derived_agent() -> None:
    """`_sync_derived_agent`, bounded for `lifespan` (F5).

    On a timeout the posture is left as it is on disk: this process does not
    touch the file, and `derived_block_state(config)` keeps reading whatever is there,
    so the settings panel stays truthful. A sync still running in its thread
    may yet complete; it holds `agent_profile`'s lock, so a settings write
    queues behind it rather than racing it, and the session gate's bounded
    acquire refuses Default until it finishes
    (260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-16).
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review (F5)
    """
    try:
        await asyncio.wait_for(_sync_derived_agent(),
                               _LIFESPAN_STEP_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        log.error("derived agent sync did not finish within %.0f s; startup "
                  "continues with the permission posture unchanged",
                  _LIFESPAN_STEP_TIMEOUT_SECONDS)


async def _startup_load_local_secret() -> None:
    """Load the local secret for `lifespan`, bounded and never fatal.

    A load that fails, or does not finish within
    `_LIFESPAN_STEP_TIMEOUT_SECONDS`, degrades to D-22's in-memory secret with
    the reason recorded for `/api/settings`. A late-finishing load is
    discarded, and re-records the reason when it lands, because a successful
    write clears it and the settings panel would then claim a persisted secret
    this process is not using.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review (F5)
    """
    # `run_in_executor` rather than `to_thread`: a plain future that completes
    # when the thread does, never a task that could be cancelled first, so the
    # late-result callback below always sees the thread's real finish.
    load = asyncio.get_running_loop().run_in_executor(None, ensure_local_secret)
    try:
        done, _ = await asyncio.wait({load}, timeout=_LIFESPAN_STEP_TIMEOUT_SECONDS)
        if not done:
            reason = (f"Loading the local secret took longer than "
                      f"{_LIFESPAN_STEP_TIMEOUT_SECONDS:.0f} s")
            log.error("%s; using an in-memory local secret until PowerAtlas "
                      "exits", reason)
            set_local_secret(hold_local_secret_in_memory(reason))

            def _late(fut) -> None:
                if not fut.cancelled():
                    fut.exception()  # retrieved, so asyncio does not log it
                hold_local_secret_in_memory(reason)
            load.add_done_callback(_late)
            return
        set_local_secret(load.result())
    except Exception as exc:
        # Guarded like `_sync_derived_agent` (R-13): an unpredicted failure
        # here must not become "the application will not start".
        # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4 review
        log.exception("local secret setup failed; using an in-memory local "
                      "secret until PowerAtlas exits")
        set_local_secret(hold_local_secret_in_memory(
            f"Local secret setup failed: {type(exc).__name__}: {exc}"))


# How long the session gate waits for `agent_profile`'s generation lock before
# it gives up and raises, which `acp._handle_new` turns into a refusal. Long
# enough for an ordinary regeneration, short enough that a stalled one (a hung
# disk, an antivirus scan) never hangs session creation.
# 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-16
_GATE_LOCK_TIMEOUT_SECONDS = 2.0


def _permission_in_effect(state: str, config) -> bool:
    """The one in-effect predicate the gate and the settings panel share (SC-9).

    The file must be exactly what the settings compile to (D-15), and the
    settings must have been read: a config.toml that did not parse loads as the
    defaults, so a file that happens to match them proves nothing about what
    the user set (Phase 1 review, finding 1).
    """
    return state == "on" and not getattr(config, "_load_error", "")


def _not_in_effect_reason(state: str, compile_error: str, config,
                          healed: bool | None, *,
                          detail: bool = True) -> tuple[str, str]:
    """`(cause, fix)` for a derived agent that is not in effect (D-34).

    Plain words for the session refusal and the log: what is wrong, and the
    concrete step that fixes it. `healed` is the D-30 self-heal's outcome, or
    None when it did not run. `detail=False` leaves out raw error text, for a
    remote client (Phase 1 review, finding 11); `_for_remote` then shortens
    the paths.
    """
    path = str(agent_profile.derived_agent_path())
    last = agent_profile.last_generation()
    settings_step = ("save the permission mode again in Settings > Agent "
                     "permissions on the dashboard, or restart PowerAtlas")

    def said(text: str, fmt: str) -> str:
        return fmt.format(text) if detail and text else ""

    load_error = getattr(config, "_load_error", "")
    if load_error:
        from . import config as config_mod
        cfg = config_mod.CONFIG_PATH
        return (f"PowerAtlas's config.toml could not be read"
                f"{said(load_error, ' ({})')}, so which permission mode is set "
                "is unknown",
                f"Fix {cfg} by hand "
                f"({config_mod.unreadable_backup_note(config)}), then start "
                "the session again.")
    if compile_error:
        return (f"the permission rules in config.toml cannot be applied"
                f"{said(compile_error, ' ({})')}",
                "Fix or remove acp_permission_rules in PowerAtlas's "
                f"config.toml, then {settings_step}.")
    if state == "absent":
        why = said(last.error if last.attempted else "", ": {}")
        return (f"its agent file {path} has not been written{why}",
                settings_step[0].upper() + settings_step[1:]
                + "; if it keeps failing, the warning there names the error.")
    if state == "stale":
        if healed is False and last.error:
            why = (said(last.error, ", and regenerating it failed ({})")
                   or ", and regenerating it failed")
        else:
            why = ""
        return (f"its agent file {path} does not match the current permission "
                f"settings{why}",
                settings_step[0].upper() + settings_step[1:] + ".")
    return (f"{path} was not written by PowerAtlas, or cannot be read, so "
            "PowerAtlas will not use or replace it",
            "Remove or rename that file, then " + settings_step + ".")


def _for_remote(text: str) -> str:
    """`text` with the user's home folder shown as `~` (finding 11).

    Both separator spellings, case-insensitively on Windows, because the
    paths in a refusal come from `Path` objects and from kiro-cli's error text.

    A folder is matched only as a whole path component: followed by a
    separator or the end of the text, so `C:\\Users\\me2` is not shortened to
    `~2`. PowerAtlas's config folder is replaced too when it is not under the
    home folder (Linux `XDG_CONFIG_HOME`), before the home folder, so neither
    reaches a remote client (Phase 1 re-review, finding 3).
    """
    from . import config as config_mod
    home = os.path.expanduser("~").rstrip("\\/")
    if home == "~":
        home = ""
    folders: list[tuple[str, str]] = []
    config_dir = str(config_mod.CONFIG_DIR).rstrip("\\/")
    if config_dir and not (home and _path_within(config_dir, home)):
        folders.append((config_dir, "<PowerAtlas config folder>"))
    if home:
        folders.append((home, "~"))
    flags = re.IGNORECASE if sys.platform == "win32" else 0
    # The deeper folder first, so a home folder inside the config folder (or
    # the reverse) is shortened by its own name.
    for folder, shown in sorted(folders, key=lambda f: len(f[0]), reverse=True):
        for spelling in {folder, folder.replace("\\", "/"),
                         folder.replace("/", "\\")}:
            text = re.sub(re.escape(spelling) + r"(?=[\\/]|$)",
                          lambda _m, s=shown: s, text, flags=flags)
    return text


def _path_within(path: str, folder: str) -> bool:
    """Whether `path` is `folder` or lies under it, compared as `_for_remote` does."""
    norm = lambda p: p.replace("\\", "/").rstrip("/")  # noqa: E731
    p, f = norm(path), norm(folder)
    if sys.platform == "win32":
        p, f = p.lower(), f.lower()
    return p == f or p.startswith(f + "/")


class _GateBudgetTimeout(TimeoutError):
    """The gate's own wait ran out. Still a `TimeoutError`, which is what `acp`
    reads as "being applied"; every other `TimeoutError` the gate meets is
    re-raised as a failure (Phase 1 re-review, finding 6)."""


def _derived_agent_in_effect() -> dict:
    """`acp.mode_gate_hook`: whether the derived agent is in effect, and why not.

    260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL Phase 1. Returns
    `{in_effect, state, mode, cause, fix}`; `acp` reads `in_effect` and, when it
    is False, refuses a Default create naming `cause` and `fix` (D-34).
    In effect means the file on disk is exactly what the current settings
    compile to (`derived_block_state(config) == "on"`, D-15) — the same
    predicate the settings panel shows, so the two cannot disagree.

    Takes `agent_profile._generation_lock` with a bounded wait (D-16): it is
    the only acquirer besides `agent_profile.apply_settings`, so it never reads
    a config.toml that is ahead of the file. A timeout raises, and `acp`
    refuses on a raise. While holding the lock, a `"stale"` file whose config
    loaded cleanly is regenerated once and re-checked (D-30); a regeneration
    that compiled a different mode or rule set raises D-35's dashboard notice.
    Never calls `find_protected_links` (D-39).

    `acp`'s `load_session` consults it too, for the `modeId` a reload sends.
    There a raising call falls back to `kiro_default` instead of refusing the
    load. 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 7 (K5).

    The whole call, heal included, waits at most `_GATE_LOCK_TIMEOUT_SECONDS`
    (D-30; Phase 1 review, finding 2). The heal runs in a worker thread that
    takes over this call's hold on the lock and releases it when the
    regeneration and its re-check finish; this call waits for it with what is
    left of the budget and raises `TimeoutError` when that runs out. The
    regeneration then completes in the background, and a later gate call sees
    its result. A config.toml that did not parse is never in effect and never
    healed (finding 1). When not in effect, `remote_cause`/`remote_fix` carry
    the same words without raw error text and with `~` for the home folder,
    for a remote client (finding 11).
    """
    lock = agent_profile._generation_lock
    deadline = time.monotonic() + _GATE_LOCK_TIMEOUT_SECONDS

    def timed_out(what: str) -> TimeoutError:
        return _GateBudgetTimeout(
            f"the permission settings are being applied ({what}) and did not "
            f"finish within {_GATE_LOCK_TIMEOUT_SECONDS:.0f} s")

    if not lock.acquire(timeout=_GATE_LOCK_TIMEOUT_SECONDS):
        raise timed_out("waiting for a settings change")
    handed_off = False
    try:
        config = load_config()
        state, compile_error = agent_profile.block_state_detail(config)
        healed = None
        if state == "stale" and not getattr(config, "_load_error", ""):
            done = threading.Event()
            box: dict = {}

            def heal() -> None:
                # Owns the lock from here on, and always releases it.
                try:
                    box["healed"] = agent_profile.heal_stale_locked(config)
                    box["state"] = agent_profile.block_state_detail(config)
                except BaseException as exc:  # noqa: BLE001 - re-raised below
                    box["error"] = exc
                finally:
                    lock.release()
                    done.set()

            worker = threading.Thread(target=heal, daemon=True,
                                      name="acp-permission-heal")
            worker.start()
            handed_off = True
            if not done.wait(max(0.0, deadline - time.monotonic())):
                raise timed_out("regenerating the agent file")
            if "error" in box:
                # Wrapped, so only this call's own budget reads as "being
                # applied": a `TimeoutError` raised inside the regeneration is
                # a failure, and `acp` must report it as one (Phase 1
                # re-review, finding 6).
                raise RuntimeError("regenerating the ACP agent file failed: "
                                   f"{box['error']!r}") from box["error"]
            healed = box["healed"]
            state, compile_error = box["state"]
        elif _permission_in_effect(state, config):
            # config.toml reads cleanly again and the file already matches it:
            # a "could not be read" status from earlier no longer holds
            # (finding 4). The lock is still this call's here.
            agent_profile.clear_unreadable_status(config)
    except TimeoutError as exc:
        if exc.__class__ is _GateBudgetTimeout:
            raise
        # Any other `TimeoutError` (from reading config.toml or the agent
        # file) is a failure, not "being applied" (finding 6).
        raise RuntimeError(f"the ACP permission check failed: {exc!r}") from exc
    finally:
        if not handed_off:
            lock.release()
    in_effect = _permission_in_effect(state, config)
    verdict = {"in_effect": in_effect, "state": state,
               "mode": config.acp_permission_mode, "cause": "", "fix": ""}
    if not in_effect:
        verdict["cause"], verdict["fix"] = _not_in_effect_reason(
            state, compile_error, config, healed)
        remote_cause, remote_fix = _not_in_effect_reason(
            state, compile_error, config, healed, detail=False)
        verdict["remote_cause"] = _for_remote(remote_cause)
        verdict["remote_fix"] = _for_remote(remote_fix)
    return verdict


@asynccontextmanager
async def lifespan(app_instance):
    # Before the sweeper starts and before the first request is served, so a
    # session created seconds after startup already sees the current posture.
    # Guarded inside `_sync_derived_agent` rather than here, so that every
    # caller of it inherits the same "never fatal" contract.
    # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 1
    # Bounded by `_LIFESPAN_STEP_TIMEOUT_SECONDS`, as is the secret load below.
    # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review (F5)
    await _startup_sync_derived_agent()
    # Before the first request, so the first door's code already exchanges for
    # a cookie that verifies. `ensure_local_secret` never returns "" (D-22): an
    # unwritable file degrades to an in-memory secret, reported in settings.
    # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
    #
    # Guarded like `_sync_derived_agent` above (R-13): an unpredicted failure
    # here must not become "the application will not start". It degrades to
    # the same D-22 in-memory secret a failed write gets, with the reason
    # recorded for `/api/settings`.
    # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4 review
    # The guard, and now a timeout, live in `_startup_load_local_secret`.
    # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review (F5)
    await _startup_load_local_secret()
    task =asyncio.create_task(_background_refresh())
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
        # The other half of the same arrangement: `acp` may not import
        # `notifications` any more than it may import `presence`, so this
        # module -- which imports all three -- connects them. `acp` reports
        # what happened and whether anyone was attached; `_notify_from_acp`
        # owns the policy and the config read.
        acp.set_notify_hook(_notify_from_acp)
        # And the third: `acp` may not import `agent_profile` either (D-20),
        # but it must refuse the derived agent as a modeId while that agent is
        # not in effect, or kiro-cli silently runs the session as "vibe" — and
        # it must bind Default to the derived agent while it is.
        # `acp` calls this off the loop and refuses the create if it raises.
        # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 2 review,
        # Phase 3 (G1). While it is not in effect a Default create is refused
        # too, never bound to `kiro_default` without the floor
        # (260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-34).
        # `acp`'s `load_session` calls it as well, for the `modeId` a reload
        # sends; there a raise falls back to `kiro_default` rather than
        # refusing the load.
        # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 7 (K5).
        acp.set_mode_gate_hook(_derived_agent_in_effect)
    sweeper =acp.start_sweeper() if acp is not None else None
    watchdog = acp.start_watchdog() if acp is not None else None
    try:
        yield
    finally:
        task.cancel()
        if sweeper is not None:
            sweeper.cancel()
        if watchdog is not None:
            watchdog.cancel()
        try:
            # One gather for all background tasks, with `return_exceptions=True`,
            # and inside this block rather than as bare awaits. That is what makes
            # the nested teardown below unconditional: `gather` in this mode cannot
            # propagate whatever any task raised on its way out, so there is no
            # exception here that could skip `acp.shutdown()`.
            await asyncio.gather(
                *(t for t in (task, sweeper, watchdog) if t is not None),
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
            # The count from the last burst of refused login-code exchanges
            # is otherwise only written by the next refusal, which never comes.
            # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4 review
            try:
                _flush_login_refusal_warnings()
            except Exception:
                log.exception("login-refusal log flush failed")
            # The loopback gate's twin of the flush above.
            # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5 review
            try:
                _flush_gate_refusal_warnings()
            except Exception:
                log.exception("gate-refusal log flush failed")


async def _background_refresh():
    while True:
        await asyncio.sleep(30)
        try:
            await asyncio.to_thread(data.refresh_stale_entries)
        except Exception:
            log.exception("Background refresh failed")


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
templates = Jinja2Templates(
    env=_jinja2.Environment(
        loader=_jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        auto_reload=True,
        autoescape=True,
    )
)
# StaticFiles serves /static/style.css with an ETag/Last-Modified but no
# Cache-Control, so a browser's own heuristic caching can keep serving a
# pre-restart copy indefinitely -- the URL never changes, so nothing tells it
# to revalidate (observed directly: a CSS-only fix landed, the server
# restarted, and the rail's dot stayed invisible in an already-open tab until
# a hard reload). A `?v=` query string busts that cache on every edit,
# computed once at import time from the file's own mtime -- process restart
# is already required for any static-asset change to reach a running
# PowerAtlas, so a value fixed for the process lifetime is exactly as fresh
# as the file it points at.
templates.env.globals["static_version"] = str(int((_STATIC_DIR / "style.css").stat().st_mtime))


@app.exception_handler(ConfigUnreadableError)
async def _config_unreadable_refusal(request: Request, exc: ConfigUnreadableError):
    """A route tried to save over a config.toml that did not parse.

    260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL, Phase 1 re-review finding 1.
    `save_config` refuses such a write, so every route that loads, changes and
    saves the config answers here rather than with a bare 500 and none needs
    its own check. `save_config` has already logged the refusal.

    A JSON route gets `{"ok": false, "error": ...}` with 409. A route that
    answers with a toast partial gets the same toast its own validation
    refusals use, level `error`, with status 200: its callers show the body of
    a 2xx response as a toast, and some of them drop a non-2xx body in favour
    of a generic "failed" line, which would hide the fix.
    """
    message = str(exc)
    route = request.scope.get("route")
    response_class = getattr(route, "response_class", None)
    if isinstance(response_class, type) and issubclass(response_class, HTMLResponse):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": message, "level": "error"})
    return JSONResponse({"ok": False, "error": message}, status_code=409)


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

# The WebSocket transport for the ACP page — the first-ever named constant for
# this path. Previously a bare `"/ws/acp"` literal on the route decorator and
# inside `_REMOTE_ALLOWED_PATHS`; named now for the same reason its HTTP
# The secret-exchange surface: one path, GET renders the form and POST trades
# the device secret for the cookie. Named once because three things must agree
# about it — the routes, the remote path allowlist, and the cookie exemption.
_REMOTE_AUTH_PATH = "/remote-auth"

# The login-code exchange: a GET, because a door opens it as a URL in a browser
# and the first load has to be the exchange. It is the second GET here that
# changes state, and it needs no Origin check: it does nothing without a live
# login code, and a code only exists by an in-process mint. Loopback-only by
# omission — it is not in `_REMOTE_ALLOWED_PATHS`, so a remote peer never
# reaches it. Named once because Phase 5's gate must exempt exactly this path.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
_LOCAL_AUTH_PATH = "/local-auth"

# The session browser's listing route. Defined up here, far from its own route,
# for one mechanical reason: `_REMOTE_ALLOWED_PATHS` below now names it, and a
# module-level dict is built at import time, so the name has to exist before
# that literal is evaluated. The route, its page sizes and its rationale stay
# together further down.
_ACP_LISTING_PATH = "/api/acp/sessions"

# The create picker's workspace list, up here for exactly the same mechanical
# reason as its neighbour above — `_REMOTE_ALLOWED_PATHS` names it, and that
# dict is built at import time. Its rationale stays with the route further down.
_ACP_WORKSPACES_PATH = "/api/acp/workspaces"

# The session delete route. Up here for the same reason as the two paths above —
# `_REMOTE_ALLOWED_PATHS` names it and the dict is built at import time. The full
# rationale (auth posture, mobile-UA advisory gate, rename-staging safety net)
# stays with the route and the block comment that precede it further down.
_ACP_DELETE_PATH = "/api/acp/sessions/delete"

# The restart route. Up here for the same mechanical reason as the paths above:
# `_REMOTE_ALLOWED_PATHS` names it and that dict is built at import time. The
# route and its rationale are further down near the other remote-access routes.
_ACP_RESTART_PATH = "/api/restart"

# The ACP websocket transport. Defined up here, far from its own route, for
# the same mechanical reason as the paths above: `_REMOTE_ALLOWED_PATHS` names
# it and that dict is built at import time.
_ACP_WS_PATH = "/ws/acp"

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
      rebound page every response body this app has — including
      ``custom_launchers``, whose ``env`` holds cleartext credentials. (The
      list once also named the ACP token, retired by
      260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6.)
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
    * the page's own ``fetch`` of itself in ``explainRefusedHandshake``
      (renamed by 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL
      Phase 6) — same-origin ``Referer``, no ``Origin``.

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
    and spawns ``kiro-cli acp --agent-engine v3``. A cross-origin top-level
    navigation was therefore enough to start an ACP agent process with no user
    gesture; v3 gates each tool call behind an interactive
    ``session/request_permission`` prompt rather than a blanket trust mode, but
    an unwanted spawn is still an unwanted spawn.
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
# sits behind this code is `kiro-cli acp --agent-engine v3` — an agent that
# answers `session/request_permission` interactively rather than trusting
# every tool, but whose approval channel a cookie-holding client can drive
# itself (see `ws_acp` below), so the practical consequence is still arbitrary
# command execution as the user. Every check here is load-bearing on its own.

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


# --- Restart callback --------------------------------------------------------
#
# Wired by __main__ at startup to tray.trigger_restart().  None means no tray
# is running (e.g. test environment); the route returns 503 in that case.
_restart_callback: "Callable[[], None] | None" = None


def set_restart_callback(cb: "Callable[[], None]") -> None:
    """Register the function that triggers a PowerAtlas restart.

    Called from ``__main__`` after the tray thread has been set up.  The
    callback is ``tray.trigger_restart``, which sets ``_restart_requested``,
    fires the shutdown event and stops the icon — the same path the tray menu's
    own Restart item uses.
    """
    global _restart_callback
    _restart_callback = cb


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
    # holding non-ASCII, and a cookie is entirely attacker-chosen, so a `str`
    # comparison would turn a refusal into a 500 any caller can drive.
    return secrets.compare_digest(
        sig.encode("utf-8", "replace"),
        _device_cookie_sig(secret, device_id, issued_at).encode("utf-8"))


# --- Loopback credential: the local secret and its cookie ---------------------
#
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4. The loopback
# twin of the device cookie above, and deliberately a *separate* cookie under a
# *separate* key (D-17): `pa_device` is never sent to a loopback spelling, and
# one unified cookie would let a copied login link hand out remote reach. The
# peer class (Phase 5's gate) selects which of the two applies.
#
# Host-only by construction: no `Domain` attribute, a fixed name and `path="/"`,
# none of which depends on the request's `Host`. That is what lets Phase 5 pick
# one canonical loopback spelling for every door without touching this code.
#
# Nothing here gates a route yet; Phase 5 calls `_local_cookie_ok`.

# Loaded at startup by `lifespan` through `set_local_secret`. Empty is the
# fail-closed state: no cookie verifies and no code exchanges.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
_LOCAL_SECRET = ""

_LOCAL_COOKIE_NAME = "pa_local"

# The first field of the three-field value. `pa_device` carries a user-chosen
# device id there; the loopback cookie has no device, so the field is a constant
# that keeps the shape — and the parser — identical to the device cookie's.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
_LOCAL_COOKIE_SUBJECT = "loopback"

# 90 days, the device cookie's figure. The doors mint a fresh code (and so a
# fresh cookie) on every open, so the ceiling bounds only a browser nobody has
# re-entered from the tray in three months.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
LOCAL_COOKIE_MAX_AGE_SECONDS = 90 * 24 * 3600


def set_local_secret(secret: str) -> None:
    """Load the local secret, mirroring `set_remote_secret`.

    ``""`` restores the fail-closed state. A value shorter than
    ``REMOTE_SECRET_MIN_LEN`` is refused rather than trusted.
    """
    global _LOCAL_SECRET
    value = (secret or "").strip()
    if value and len(value) < REMOTE_SECRET_MIN_LEN:
        log.error("local secret is shorter than %d characters; no loopback "
                  "cookie will verify", REMOTE_SECRET_MIN_LEN)
        value = ""
    _LOCAL_SECRET = value


def make_local_cookie(issued_at: int | None = None) -> str:
    """Mint a loopback cookie value, or `""` when there is no usable secret.

    `make_device_cookie`'s shape — ``subject.stamp.hmac`` — over the local key.
    """
    if not _LOCAL_SECRET:
        return ""
    stamp = str(int(time.time()) if issued_at is None else issued_at)
    sig = _device_cookie_sig(_LOCAL_SECRET, _LOCAL_COOKIE_SUBJECT, stamp)
    return f"{_LOCAL_COOKIE_SUBJECT}.{stamp}.{sig}"


def _local_cookie_ok(scope) -> bool:
    """Whether a scope carries a valid, unexpired loopback cookie.

    `_cookie_ok`'s rules over `pa_local` and the local key: fails closed on
    every path, never raises, and compares with `compare_digest` over UTF-8
    bytes. The wall-clock stamp and its future-skew bound are inherited from the
    device cookie on purpose (R-18): a skewed clock costs one fresh mint.
    """
    secret = _LOCAL_SECRET
    if not secret or len(secret) < REMOTE_SECRET_MIN_LEN:
        return False
    raw = _scope_cookie(scope, _LOCAL_COOKIE_NAME)
    if not raw or len(raw) > 160:
        return False
    subject, sep_a, rest = raw.partition(".")
    issued_at, sep_b, sig = rest.partition(".")
    if not sep_a or not sep_b or not sig:
        return False
    if subject != _LOCAL_COOKIE_SUBJECT:
        return False
    if not _ISSUED_AT_RE.fullmatch(issued_at):
        return False
    now = int(time.time())
    issued = int(issued_at)
    if issued > now + _COOKIE_FUTURE_SKEW_SECONDS:
        return False
    if now - issued > LOCAL_COOKIE_MAX_AGE_SECONDS:
        return False
    return secrets.compare_digest(
        sig.encode("utf-8", "replace"),
        _device_cookie_sig(secret, subject, issued_at).encode("utf-8"))


def _set_local_cookie(response: Response) -> bool:
    """Attach a fresh loopback cookie to ``response``. False when none minted.

    One place for the attributes, because two routes set this cookie (the
    exchange and the rotation) and they must not drift apart. No `Domain`, so
    host-only; no `Secure`, because loopback is plain HTTP.
    """
    value = make_local_cookie()
    if not value:
        return False
    response.set_cookie(
        _LOCAL_COOKIE_NAME, value,
        max_age=LOCAL_COOKIE_MAX_AGE_SECONDS,
        httponly=True, samesite="strict", path="/")
    return True


# --- Login codes ---------------------------------------------------------------
#
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4 (D-21). A login
# code is what a door puts in the URL it opens; `/local-auth` trades it, once,
# for the cookie above. Minting is an in-process call only (D-3, SC-6): the
# tray, peek and uvicorn share one process, and an HTTP mint would be
# self-service login for any local process under another name.

# `secrets.token_urlsafe(32)` — 43 characters of the URL-safe alphabet. Entropy
# is load-bearing: a same-user attacker reaches the exchange directly.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
_LOGIN_CODE_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_LOGIN_CODE_TTL_SECONDS = 120.0
# Three doors plus "Copy login link"; 64 outstanding codes is far past any real
# use, and past it the oldest is evicted rather than the store growing.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
_LOGIN_CODE_MAX_OUTSTANDING = 64
# The clock the TTL is measured on (D-21, R-18): monotonic, because the codes
# are process-local and wall time only adds NTP and sleep corrections. A module
# seam so a test can move this clock without moving the event loop's.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
_login_now = time.monotonic
# code -> `_login_now()` at mint. Insertion-ordered, so the first key is oldest.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
_login_codes: dict[str, float] = {}
# The mint runs on the tray/peek threads (Phase 5) and the exchange on the loop
# thread; purge-then-insert and find-then-pop are not single dict operations.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
_login_codes_lock = threading.Lock()


def _purge_expired_login_codes(now: float) -> None:
    """Drop codes past their TTL. Caller holds `_login_codes_lock`."""
    for code in [c for c, t in _login_codes.items()
                 if now - t >= _LOGIN_CODE_TTL_SECONDS]:
        del _login_codes[code]


def mint_login_code() -> str:
    """Return a fresh single-use login code. **In-process only — never a route.**

    The doors (tray, peek double-tap, peek webview, "Copy login link") call
    this and append ``?code=`` to `_LOCAL_AUTH_PATH`. Returns ``""`` when there
    is no local secret, because a code would exchange for a cookie that
    verifies nowhere.
    """
    if not _LOCAL_SECRET:
        return ""
    code = secrets.token_urlsafe(32)
    with _login_codes_lock:
        now = _login_now()
        _purge_expired_login_codes(now)
        while len(_login_codes) >= _LOGIN_CODE_MAX_OUTSTANDING:
            _login_codes.pop(next(iter(_login_codes)))
        _login_codes[code] = now
    return code


def login_path(code: str) -> str:
    """The path-and-query a door opens: `_LOCAL_AUTH_PATH` plus the code."""
    return f"{_LOCAL_AUTH_PATH}?code={code}"


def _consume_login_code(supplied: str) -> bool:
    """Take ``supplied`` out of the store if it is live. True exactly once.

    Constant-time per candidate: every outstanding code is compared with
    `compare_digest` rather than looked up by hash, so response timing does
    not narrow a guess. The store is bounded, so this is bounded work.
    """
    probe = supplied.encode("utf-8", "replace")
    with _login_codes_lock:
        now = _login_now()
        _purge_expired_login_codes(now)
        match = None
        for code in _login_codes:
            if secrets.compare_digest(probe, code.encode("ascii")):
                match = code
        if match is None:
            return False
        del _login_codes[match]
        return True


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


# Lowercase substrings that identify a mobile browser's User-Agent string.
# Used by `_is_mobile_ua` to gate `ACP_CAN_DELETE` in the `/acp` template.
# Advisory only — this affects whether the \u22ef menu is *rendered*, not whether
# the delete route is reachable. A mobile browser that POSTs directly (bypassing
# the JS gate) is stopped by the device cookie + Origin/Referer check in
# `same_origin_guard`, the same posture the listing route already accepts. The
# mobile exclusion is a UX promise, not a security boundary.
_MOBILE_UA_TOKENS = ("mobile", "android", "iphone", "ipod", "ipad")


def _is_mobile_ua(ua: str) -> bool:
    """Return True when the User-Agent string looks like a mobile browser.

    Matches case-insensitively against `_MOBILE_UA_TOKENS`. If the user
    configures their mobile browser to request the desktop site, it sends a
    desktop UA and gets the delete button — their deliberate choice.
    """
    return any(token in ua.lower() for token in _MOBILE_UA_TOKENS)


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
    _REMOTE_AUTH_PATH: "http",
    # Restart: admitted to authenticated remote peers so the mobile user can
    # restart PowerAtlas from /acp without walking to the machine.  The device
    # cookie + Origin/Referer check is the transport-level auth boundary.
    # The stop switch blocks this path when remote access is stopped.
    _ACP_RESTART_PATH: "http",
    _ACP_PATH: "http",
    _ACP_WS_PATH: "websocket",
    _ACP_LISTING_PATH: "http",
    # The create picker's workspace list. Here because creating a session is
    # already a remote capability — `session/new` rides the allowlisted
    # `/ws/acp` — so a picker that could not list workspaces from a phone would
    # remove something that works today. It carries workspace paths and session
    # *counts* and no session content, i.e. a strict subset of what
    # `_ACP_LISTING_PATH` above already discloses.
    _ACP_WORKSPACES_PATH: "http",
    # Destructive / irreversible: admitted to authenticated remote peers so that
    # desktop browsers at the NetBird address can delete sessions from the rail.
    # The mobile-UA exclusion (`ACP_CAN_DELETE=false` in the rendered page) is
    # advisory / UI-only — it affects whether the \u22ef menu is *rendered*, not
    # whether the route is reachable. The device cookie + Origin/Referer check
    # in `same_origin_guard` (`allow_missing=False` for all POSTs) is the
    # transport-level auth boundary, identical to `_ACP_LISTING_PATH` above.
    # The stop switch (`_REMOTE_SURFACE_STOPPED`) blocks this path when stopped.
    _ACP_DELETE_PATH: "http",
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


# --- The loopback gate ---------------------------------------------------------
#
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5 (SC-5, D-2).
# Default-deny for loopback peers: every route needs a valid `pa_local`, with
# exactly two exemptions — the login-code exchange, which is how a browser gets
# the cookie in the first place, and the `/static` mount, which serves only the
# files already in the package. Everything else, `GET /` included, is behind
# it: `GET /` is where the page-injected token used to be scraped, and
# `GET /api/remote-access` hands out the permanent remote device secret.

# The single loopback spelling every door opens and every cookie is therefore
# issued for (D-17). `127.0.0.1`, `localhost` and `::1` all pass the Host check
# but do not share a browser cookie jar, and `pa_local` is host-only, so a
# second spelling is a signed-out browser. `__main__` binds and builds
# `server_url` from this name rather than from its own literal.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5
LOOPBACK_HOST = "127.0.0.1"

# HTTP paths a browser navigates to as a page. A refused GET to one of these
# gets the "open PowerAtlas from the tray" page rather than JSON, because the
# reader is a person looking at a browser tab. A path set rather than `Accept`
# sniffing: deterministic, and a fragment fetch (`/partials/*`) or an API call
# never lands a whole HTML page into a caller expecting something else.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5
_LOCAL_PAGE_PATHS = frozenset({"/", _ACP_PATH, _REMOTE_AUTH_PATH})

# The gate's refusals share one rate-limited window, as the login-code
# exchange's do (`_warn_login_refused`), and on the same interval: a browser
# that lost its cookie refuses on every poll, and a local process can send
# refusals as fast as it likes. Without a line here a locked-out user leaves no
# trace at all — uvicorn runs at warning level, so there is no access log.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5 review
_gate_warn_state = {"last": float("-inf"), "suppressed": 0}

# A path is attacker-chosen; the logged form is `repr`-escaped (no forged log
# lines) and cut to this length.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5 review
_GATE_LOG_PATH_MAX = 120


def _rate_limited_log(state: dict, level: int, msg: str, *args,
                      detail=None) -> None:
    """One line per `_LOGIN_WARN_INTERVAL_SECONDS` for ``state``, counting the rest.

    The one implementation behind the login-code exchange's refusals and the
    loopback gate's. ``state`` is that caller's ``{"last", "suppressed"}``
    dict, so each keeps its own window. ``detail``, when given, is called only
    for a line that is actually written and returns extra ``%`` arguments —
    formatting a refusal that is then suppressed would be wasted work on the
    path a flood arrives on. The suppressed count is always the last argument.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review (F11)
    """
    now = time.monotonic()
    if now - state["last"] < _LOGIN_WARN_INTERVAL_SECONDS:
        state["suppressed"] += 1
        return
    extra = tuple(detail()) if detail is not None else ()
    log.log(level, msg, *args, *extra, state["suppressed"])
    state["last"] = now
    state["suppressed"] = 0


def _flush_rate_limited_log(state: dict, label: str) -> None:
    """Write ``state``'s suppressed-refusal count now, if there is one.

    Called from `lifespan` teardown, after the precedent of
    `__main__._RepeatedRecordFilter.flush`: a suppressed count is otherwise
    only reported by the next refusal, so the final burst's count would never
    reach the log.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review (F11)
    """
    if state["suppressed"]:
        log.warning("%s: %d further refusals suppressed since the last line",
                    label, state["suppressed"])
        state["suppressed"] = 0


def _warn_gate_refused(scope) -> None:
    """One WARNING per `_LOGIN_WARN_INTERVAL_SECONDS`, counting the rest.

    Names the scope type, method and path of the refusal that opened the
    window — never a header, a cookie or the query string, which can carry a
    login code.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5 review
    """
    def detail():
        path = scope.get("path") or ""
        shown = repr(path[:_GATE_LOG_PATH_MAX])
        if len(path) > _GATE_LOG_PATH_MAX:
            shown += "..."
        return scope.get("type"), scope.get("method") or "-", shown

    _rate_limited_log(
        _gate_warn_state, logging.WARNING,
        "loopback request refused without a valid pa_local cookie: "
        "%s %s %s (%d further refusals suppressed since the last line)",
        detail=detail)


def _flush_gate_refusal_warnings() -> None:
    """Write the gate's suppressed-refusal count now, if there is one.

    `_flush_login_refusal_warnings`'s twin, called beside it from `lifespan`
    teardown for the same reason: the final burst's count is otherwise never
    written.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5 review
    """
    _flush_rate_limited_log(_gate_warn_state, "loopback gate")


def _local_gate_exempt(scope) -> bool:
    """Whether a loopback scope may pass without `pa_local`.

    Scope-typed, like `_remote_path_allowed`: both exemptions are HTTP GET (and
    HEAD) only, the only methods either route serves. A websocket upgrade to
    `/static/x` is never exempt — it would otherwise reach
    `StaticFiles.__call__`, which asserts an http scope. The mount is matched
    as a directory, so `/staticfoo` is not exempt.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5

    A path with a `..` segment is never exempt: `/static/../api/settings`
    matches the prefix, and only `StaticFiles`' own traversal check (a 404)
    kept it from anything. The exemption no longer depends on that. `scope
    ["path"]` is already percent-decoded, so `..%2F` arrives here as `../`;
    a backslash counts as a separator because the static directory is
    resolved with Windows path rules.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final QA
    """
    if scope["type"] != "http" or scope.get("method") not in ("GET", "HEAD"):
        return False
    path = scope.get("path") or ""
    if path == _LOCAL_AUTH_PATH:
        return True
    if not (path == _REMOTE_STATIC_MOUNT
            or path.startswith(_REMOTE_STATIC_MOUNT + "/")):
        return False
    return ".." not in re.split(r"[/\\]", path)


async def _refuse_local(scope, receive, send) -> None:
    """The gate's refusal: `_refuse`, plus a page for a browser navigation.

    Three shapes. A websocket closes 1008 and anything that is not a page GET
    gets the JSON 403, both through `_refuse`. A GET (or HEAD) of a page path
    gets the same script-free page `/local-auth` refuses with, telling the user
    to open PowerAtlas from the tray — the only way a browser gets signed in.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5
    """
    _warn_gate_refused(scope)
    if (scope["type"] == "http" and scope.get("method") in ("GET", "HEAD")
            and (scope.get("path") or "") in _LOCAL_PAGE_PATHS):
        page = _local_auth_refusal("This browser is not signed in.", 403)
        await page(scope, receive, send)
        return
    await _refuse(scope, send)


class LoopbackCredentialGate:
    """Default-deny `pa_local` check for loopback peers (SC-5, D-2, D-17).

    The mirror of `RemoteAccessGuard`: that guard acts only when the peer is
    remote, this one only when it is not, so each peer class meets exactly one
    credential — a NetBird browser holding `pa_device` is never also asked for
    `pa_local`. A raw ASGI class for the same reason as its sibling: it has to
    see `websocket` scopes, which `BaseHTTPMiddleware` never does.

    Registered **after** `RemoteAccessGuard`, which makes it the outermost
    layer (`add_middleware` inserts at index 0 and the stack is built over
    `reversed(middleware)`), so it refuses before any inner guard runs or logs.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            if not _is_remote_peer((scope.get("client") or (None,))[0]):
                if not _local_gate_exempt(scope) and not _local_cookie_ok(scope):
                    await _refuse_local(scope, receive, send)
                    return
        await self.app(scope, receive, send)


# After `RemoteAccessGuard`'s line, so this gate is the outermost layer. Moving
# it above that line silently inverts the order; a test pins it.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5
app.add_middleware(LoopbackCredentialGate)


def login_url(server_url: str) -> str:
    """The URL a door opens: ``server_url`` plus a freshly minted login code.

    The one builder every door uses — tray Open, "Copy login link", the peek
    double-tap, and the peek webview at creation and on every show — so none
    of them assembles the path by hand. ``server_url`` is built by `__main__`
    from `LOOPBACK_HOST`. With no local secret there is no code to mint;
    the bare URL is returned and the gate's page tells the user why.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5
    """
    code = mint_login_code()
    if not code:
        log.error("no usable local secret; opening %s without a login code",
                  server_url)
        return server_url
    return f"{server_url.rstrip('/')}{login_path(code)}"


# The per-launch page-embedded `/ws/acp` token that used to live here is
# retired: it was readable by any local process that could fetch `/` or
# `/acp`, so it added no barrier a cookie-holder does not already pass (D-4).
# `/ws/acp` is authenticated by `LoopbackCredentialGate` (`pa_local`) for a
# loopback peer and `RemoteAccessGuard` (`pa_device`) for a remote one.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6


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
        # Read off the `config` already loaded above rather than through
        # `_notifications_enabled()`, which would be a second whole-file TOML
        # parse on a route that has one in hand.
        "notifications_enabled": bool(config.notifications.get("enabled", False)),
        # Stripped: this one lands in the page source via `|tojson`.
        "launchers": _launchers_without_env(config.custom_launchers),
        "peek_hotkey": config.peek_hotkey,
        "default_directory": config.default_directory,
        "provider_settings": config.provider_settings,
        "autostart_label": "Start at login" if sys.platform != "win32" else "Start with Windows",
        # Whether the guarded `acp` import succeeded. The dashboard's
        # ACP-only affordances (its transcript panel opens `/ws/acp` directly
        # for held/available kiro-cli-v3 sessions) key off this. It used to be
        # read from whether a per-launch token was issued; the token is gone
        # and the `pa_local` cookie authenticates the socket instead.
        # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6 (D-15)
        "acp_available": acp is not None,
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
        f"style-src 'self' 'nonce-{nonce}'",
        "img-src 'self' blob:",
        f"connect-src 'self' ws://{host} wss://{host}",
        "object-src 'none'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
    ))


@app.get(_ACP_PATH, response_class=HTMLResponse)
async def acp_page(request: Request, sid: str = ""):
    """The Agent orchestrator page. ``sid`` names the session to re-subscribe to.

    This page repeats the ``_ALLOWED_HOSTS`` check that ``same_origin_guard``
    now runs for every method. The duplication is deliberate: the middleware
    was POST-only until recently, and narrowing it again would silently make
    this route serve its session-bearing page to whatever Host a rebinding
    attack chooses. (It was once also the ACP token's delivery vehicle; that
    token is retired — 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL
    Phase 6 — and the check stays for the page itself.) It calls the same
    ``_request_host_allowed`` helper, so the rule has one home and the two
    copies cannot drift into disagreeing about what a loopback Host is.
    """
    if not _request_host_allowed(request):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    # Fresh per response, so a nonce read out of one page's markup is already
    # spent by the time it could be replayed into another.
    nonce = secrets.token_urlsafe(16)
    response = templates.TemplateResponse(request, "acp.html", {
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
        # True for loopback viewers unconditionally; true for remote desktop
        # browsers (non-mobile UA); false for remote mobile browsers. Governs
        # whether the \u22ef rail-row menu (and its Delete option) is rendered.
        # The mobile exclusion is UI-only — see `_MOBILE_UA_TOKENS`.
        "can_delete": (
            not _is_remote_peer((request.scope.get("client") or (None,))[0])
            or not _is_mobile_ua(request.headers.get("user-agent", ""))
        ),
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
    # Kept after the ACP token's retirement
    # (260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6): the body
    # no longer carries a credential, but it is served only to a signed-in
    # browser, and a copy on disk or in an intermediary would outlive that
    # sign-in. Removing a header is a separate decision from retiring the
    # token, so it is not made here. `StaticFiles` deliberately sets no
    # caching headers at all.
    response.headers["Cache-Control"] = "no-store"
    return response


@app.websocket(_ACP_WS_PATH)
async def ws_acp(ws: WebSocket) -> None:
    """Transport for the ACP page. Origin, then hand off.

    Authentication happens before this route runs: ``LoopbackCredentialGate``
    refuses a loopback upgrade without a valid ``pa_local`` and
    ``RemoteAccessGuard`` a remote one without a valid ``pa_device``, so the
    socket is accepted on the cookie alone. The per-launch ``?t=`` token that
    used to be checked here is retired
    (260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6).

    The origin check runs before ``accept()``. uvicorn converts a pre-accept
    close into an HTTP 403 handshake rejection and discards the code, so 1008
    is the intent recorded here rather than what a client observes.

    Past the handoff this route is an opaque router: ``acp`` owns the frames
    and this function never inspects a ``type``, which is what lets later
    phases add message types without touching ``web.py``.

    **From a remote peer the only control on this upgrade is the device
    cookie** (it was the device cookie and the now-retired per-launch token
    until 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6). An
    earlier note claimed ``/ws/acp`` was
    incidentally browser-only from remote, on the grounds that ``_ws_origin_ok``
    demands an ``Origin`` that non-browser clients do not send. That is false
    and was disproved by execution, not by reading: ``_ws_origin_ok`` requires
    only a *self-consistent* ``Host``/``Origin`` pair, which any scripted
    client sets in one line, and ``_host_allowed`` admits loopback names
    without reference to the peer's actual address — so a remote client may
    simply claim ``Host: 127.0.0.1:4915``. A non-browser client on a remote
    address, presenting a valid cookie, reaches ``accept()`` and
    ``acp.serve_socket``.

    Recorded here because a phantom control is worse than a missing one: a
    later cleanup of ``_ws_origin_ok`` would otherwise be priced as removing a
    real defence against non-browser clients when it removes nothing of the
    sort. ``_ws_origin_ok`` is browser-CSRF hygiene — it stops a *web page* on
    another origin from opening this socket — and nothing more.
    """
    if not _ws_origin_ok(ws):
        await ws.close(code=1008)
        return
    if acp is None:
        # The guarded import failed. Close after accept so the reason survives:
        # a pre-accept close becomes a bare 403 handshake rejection.
        await ws.accept()
        await ws.close(code=1011, reason="Agent orchestrator unavailable")
        return
    await ws.accept()
    await acp.serve_socket(ws)


# --- The session browser's data source -----------------------------------
#
# A purpose-built read-only listing (D18) rather than a reuse of the
# dashboard's old session-row partial (since removed): that partial rendered
# hover-driven markup carrying the launch-action cluster — dashboard markup
# that is useless on a phone and undesirable on a surface intended to leave
# loopback. A narrow route is also auditable against the remote allowlist,
# which a partial that renders whatever the template grows is not.
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
# bounds the per-row lock check to ~30 rather than a count that scales with
# the size of the store.
_ACP_GROUPS_PER_PAGE = 10
_ACP_SESSIONS_PER_GROUP = 3
# A caller-supplied page size is an amplification lever, so both axes are
# clamped — but they are not equally expensive. A row costs one `.lock` read
# plus one `psutil` query; a *group* costs that for its rows **plus a full
# session load**, because the group's `total` needs its whole list. The group
# axis is therefore the amplification axis: measured pre-cutover against the
# v2 store (2026-08-01, before `data_kiro_v3.py` existed), the previous
# 50-group ceiling answered a single 50x50 request with 472 rows and 975 of
# that store's 1,210 sessions loaded — illustrative of the shape of the cost,
# not a current figure.
#
# 20 is twice what the product asks for — the rail shows 10 groups with a
# show-more — which leaves headroom for a client wanting a larger first page
# while halving the worst-case group fan-out. The session axis stays at 50: an
# extra row there costs one slice of an already-loaded list plus one lock read,
# and paging a large workspace is a real use. This route becomes remotely
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
# useful rather than degenerate: measured pre-cutover against the v2 store
# (2026-08-03, before `data_kiro_v3.py` existed), 30 rows reached back about
# two weeks, so a first page was roughly two weeks of day groups rather than
# one enormous "Today" — illustrative of the shape, not a current figure.
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

# kiro-cli-v3 is registered as a provider in data.PROVIDERS so the generic
# data.discover_workspaces_with_counts and data.get_sessions calls work with it.
_ACP_V3_LISTING_PROVIDER = "kiro-cli-v3"


def _acp_row_title(session) -> str:
    """The rail's label for a session.

    `data_kiro_v3` stamps `"<untitled>"` when the store carries no title, and the
    `session-tab-title` steering rework that would populate it is out of scope
    for this plan — so the honest fallback is the raw first prompt, which is
    what the user actually typed and what they will recognise.
    """
    title = (session.title or "").strip()
    if not title or title == "<untitled>":
        title = (session.first_prompt or "").strip()
    return title[:_ACP_TITLE_MAX_CHARS]


def _acp_availability(session_ids, held,
                       workspace_hashes: dict[str, str] | None = None) -> dict[str, str]:
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
    hint may only add a refusal, never grant one.

    A `sess_`-prefixed id routes to `acp._lock_holder_v3` (session.json's
    `status` field, zero round-trip). Non-`sess_`-prefixed ids (v2 bare UUIDs)
    are treated as always available — v2 sessions are no longer in the store.

    `workspace_hashes`, when the caller has it (e.g. `_acp_listing`, which
    already knows each row's workspace), maps `session_id -> hash-dir name`
    and is threaded through to `_lock_holder_v3` so a v3 id skips the
    full-directory scan. Omitted or missing an id falls back to the full scan,
    unchanged in outcome, just slower.
    """
    out: dict[str, str] = {}
    for sid in session_ids:
        if sid in held:
            out[sid] = "held"
            continue
        # Non-sess_-prefixed IDs are v2 bare UUIDs; they are no longer in the
        # store, so treat them as always available without scanning.
        if not sid.startswith("sess_"):
            out[sid] = "available"
            continue
        state = "available"
        try:
            if acp is not None:
                wh = workspace_hashes.get(sid) if workspace_hashes else None
                holder = acp._lock_holder_v3(sid, wh) if wh else acp._lock_holder_v3(sid)
                if holder is not None:
                    state = "locked"
        except Exception:
            state = "available"
        out[sid] = state
    return out


def _acp_status_for_held(sessions, snapshot=None) -> dict[str, str]:
    """The dashboard's verdict for the sessions this PowerAtlas is driving.

    Blocking — a transcript-tail classify per session — so this runs inside
    `_acp_listing`'s thread hop, beside `_acp_availability`.

    **Held sessions and no others, which is also what bounds the cost.** The
    rich working/waiting/errored verdict costs a transcript-tail classify per
    row, so it stays scoped to the sessions this ACP holds — the cap is
    `MAX_SESSIONS` (8) however many rows the page shows. A `locked` or plain
    `available` row can still be live in a foreign process (`_session_is_live`
    answers that cheaply, without a classify — see the `"live"` field the
    listing routes attach for exactly those rows); it just never gets the
    richer verdict this function produces.

    `_resolved_session_status`, and deliberately not `_session_status`. The
    latter opens with a liveness gate that asks `presence` whether a process is
    running — which for a held session is answered first-hand, because we *are*
    that process — and it can answer `closed`, which for a held session is not a
    state but a lag in a 3 s-cached process scan. `_resolved_session_status`
    takes liveness as given and settles among errored/waiting/working, the same
    precedence `session_row.html` settles its own dot through, so the two
    surfaces cannot disagree about a session both of them are showing.

    `snapshot`, when the caller already has one (both listing routes do, for
    the `"live"` field above), is reused rather than taking a second — see
    that field's own computation site.
    """
    if not sessions:
        return {}
    if snapshot is None:
        snapshot = presence.get_snapshot()
    out: dict[str, str] = {}
    for session in sessions:
        try:
            semantic = get_semantic_status(
                session.session_id, _ACP_V3_LISTING_PROVIDER, session.cwd)
            out[session.session_id] = _resolved_session_status(
                snapshot, _ACP_V3_LISTING_PROVIDER, session.session_id, semantic)
        except Exception:
            log.exception("ACP listing: could not settle status for %s",
                          session.session_id)
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
    liveness and nothing else. A workspace whose directory has been deleted
    still has sessions that report `available`, correctly, because no process
    holds a lock on a session in a deleted tree — so without this check the
    rail would offer sessions from a vanished workspace, including a large
    one, that fail the moment one is tapped.

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
                 capacity: dict,
                 providers: frozenset[str] = frozenset({_ACP_V3_LISTING_PROVIDER}),
                 include_provider: bool = False,
                 tag: str = "", time_filter: str = "",
                 sort: str = "recent", q: str = "") -> dict:
    """Build the listing payload. Blocking; runs off the loop.

    Paginated **independently at both levels** (D19). A post-pagination
    filter that sets `has_more = False` filters the loaded page and then
    declares there is nothing after it — inheriting that here would silently
    truncate a large workspace's session list at whatever the first page
    happened to hold. So each group carries its own `total`/`has_more`
    computed from its own session list, and the group axis carries its own,
    and moving one does not move the other.

    A `cwd` selects a single workspace and bypasses the group axis entirely:
    that is the shape the rail's per-group "show more" needs, and it is what
    makes paging a large workspace cost one workspace's sessions rather than
    the whole page's.

    **Honours the `hidden` workspace tag and each provider's enabled flag** —
    a workspace the user hid from the dashboard has not asked to be visible
    from a phone, and a disabled provider is not a listing this route may
    serve. The config read costs an uncached TOML parse, which is exactly why
    D15 forbids it in `at_capacity()`; D15's ban is **on the event loop**, and
    this function body runs entirely inside `asyncio.to_thread`. `load_config`
    is guarded by a `threading.Lock` (`config.py:_lock`) and returns a fresh
    `Config` per call, so it is safe to call from a worker thread. Unlike the
    dashboard routes this one takes no `tag` parameter: there is no "show
    hidden" view to reveal them, so `hidden` here means hidden.

    `providers` (dashboard/ACP-merge Phase 4): defaults to the single-provider
    set `/api/acp/sessions` has always used, so that route's call is
    byte-for-byte the same query it always ran — see this module's own
    docstring on that route for why it must never be repointed at a wider
    set. A caller passing more than one provider gets one row per *workspace*
    rather than per (workspace, provider): `_group_workspaces` folds same-path
    rows from different providers together, and each merged workspace's
    sessions come from every provider that touches it, interleaved by
    `updated_at` — the same per-workspace, cross-provider merge the dashboard
    has always applied, just applied across a whole page of them here.

    `include_provider`, off by default: adds a `"provider"` field to every
    session dict (grouped and pinned alike). Left off for `/api/acp/sessions`
    on purpose — `test_the_payload_carries_exactly_the_documented_fields`
    asserts that route's session fields by exact set equality specifically so
    a field added later fails there rather than reaching a phone, and every
    session that route ever returns is `kiro-cli-v3` anyway, so the field
    would say nothing a v3-only caller does not already know. The dashboard's
    own multi-provider route passes `True`: unlike that route, its rows can
    be any registered provider, and the client needs to know which one a row
    is to know how to open or resume it.

    `tag`/`time_filter` (dashboard/ACP-merge Phase 4): the dashboard's
    existing tag-filter/tag-management feature and time filter, preserved
    across the move from the dashboard's old server-rendered workspace list
    to its client-side rail. `tag` is the same three-way rule the dashboard
    has always used — empty excludes `hidden` (the default), `"hidden"` shows
    only `hidden`, anything else shows only workspaces carrying that exact
    tag — applied here instead of the plain `"hidden" not in tags` check.
    `time_filter` buckets by `latest_updated` exactly as `_time_bucket` does.
    Both apply before pagination, for the reason `get_all_sessions_paginated`
    already documents on `exclude_cwds`: filtering a page after the fact
    would cut it short and make `has_more` stop describing what is shown.
    Neither reaches `/api/acp/sessions`, which passes neither argument, so
    that route's behavior is unaffected. `time_filter` is grouped-mode only
    — the dashboard's own Date grouping mode already buckets by day, which
    makes a separate time filter mostly redundant there; `_acp_flat_listing`
    does not take it.

    `sort` (dashboard/ACP-merge QA follow-up): orders the workspace list
    itself in Project grouping mode — `"recent"` (the default, and the only
    value `/api/acp/sessions` ever asks for) by `latest_updated` descending,
    `"alpha"` by folder name case-insensitively. A workspace with a live
    process in it (any provider, `include_provider`-only — see below) surfaces
    next, ahead of that ordering; pinned workspaces surface ahead of that in
    turn, regardless of `sort`. The three stable sorts below run in a fixed
    order (recency-or-alpha, then active, then pinned) for that reason, not
    the other way round. Each group's own `meta` also carries this verdict as
    `"active"` (`include_provider`-only, alongside `"pinned"`) — a lazily-
    skipped group (see below) carries no session rows for the rail to put a
    per-session liveness dot on, so this is the only signal its collapsed
    header has to show.

    **Lazy per-workspace loading** (dashboard/ACP-merge QA follow-up,
    `include_provider`-only): an unpinned workspace's sessions are not
    fetched at all on a plain group-page listing — its `meta` carries the
    cheap `discover_workspaces_with_counts` total, `session_page: 0` and
    `has_more: total > 0`, and the rail's existing "More in <workspace>"
    control (already wired to a `cwd`-scoped follow-up request) is what
    actually loads its first page. A pinned *folder*, a `cwd`-scoped request,
    or a workspace found to hold an individually pinned *session* still gets
    the full fetch unconditionally. See the `lazy_mode`/`force_cwds` block
    below for the exact rule.
    """
    from .config import get_workspace_settings
    from .data import _normalize_path
    from . import data_kiro_v3

    config = load_config()
    # Fetched once and threaded through to the active-workspace sort below and
    # to `_row_dict`'s `"live"` field / `_acp_status_for_held`, rather than
    # each reaching for its own — same cached scan either way, one fewer call.
    snapshot = presence.get_snapshot()

    def _tag_keep(ws_cwd: str) -> bool:
        tags = get_workspace_settings(config, ws_cwd)["tags"]
        if not tag:
            return "hidden" not in tags
        if tag == "hidden":
            return "hidden" in tags
        return tag in tags

    enabled = frozenset(p for p in providers if _enabled(config, p))
    if not enabled:
        workspaces: list[tuple[str, int, str, list[str]]] = []
    elif len(enabled) == 1:
        only = next(iter(enabled))
        workspaces = [
            (w[0], w[1], w[2], [only])
            for w in data.discover_workspaces_with_counts(only)
            if _tag_keep(w[0])
        ]
    else:
        grouped = _group_workspaces(
            [w for w in data.discover_workspaces_with_counts(None) if w[3] in enabled],
            config)
        workspaces = [
            (g["cwd"], g["total_count"], g["latest_updated"],
             [p["name"] for p in g["providers"]])
            for g in grouped
            if _tag_keep(g["cwd"])
        ]

    if time_filter:
        workspaces = [w for w in workspaces if _time_bucket(w[2]) == time_filter]

    # Project-mode ordering (dashboard/ACP-merge QA follow-up): recent-first
    # or alphabetical per `sort`, with active workspaces surfaced ahead of
    # that and pinned workspaces (config.pinned_folders — a dashboard-only
    # concept /acp's own UI has no button for, so kept off its listing) ahead
    # of that in turn, regardless of which. Stable sorts applied coarsest-last
    # rather than one composite key: "pinned" is coarser than "active", which
    # is coarser than "recency"/"alpha", and Python's sort is stable, so each
    # later pass only reorders across its own partition boundary and never
    # disturbs the ordering already established within it.
    if sort == "alpha":
        workspaces.sort(key=lambda w: (Path(w[0]).name or w[0]).lower())
    else:
        workspaces.sort(key=lambda w: w[2] or "", reverse=True)
    if include_provider:
        # Any live provider process, not just the ones this row's own
        # `providers` column names — a workspace with something running in it
        # is what "active" means here, regardless of which agent it is.
        # Reused below (unconditionally computed, like `pinned_folders_norm`
        # just after it) to mark each group `"active"` in its own meta dict —
        # a lazily-skipped group carries no session rows to put a liveness
        # dot on, so this is the only signal the rail's collapsed header has.
        active_cwds = snapshot.live_cwds(None)
        workspaces.sort(key=lambda w: _normalize_path(w[0]) not in active_cwds)
    else:
        active_cwds = frozenset()
    pinned_folders_norm = frozenset(_normalize_path(f) for f in config.pinned_folders)
    if include_provider and pinned_folders_norm:
        workspaces.sort(key=lambda w: _normalize_path(w[0]) not in pinned_folders_norm)

    if cwd:
        target = _normalize_path(cwd)
        matched = [w for w in workspaces if _normalize_path(w[0]) == target]
        page_groups = matched[:1]
        group_total = len(matched)
        group_page = 1
        groups_has_more = False
    else:
        if q:
            q_lower = q.lower()
            workspaces = [w for w in workspaces if q_lower in w[0].lower()]
        group_total = len(workspaces)
        start = (group_page - 1) * group_size
        page_groups = workspaces[start:start + group_size]
        groups_has_more = start + group_size < group_total

    pinned_set: set[str] = set(config.pinned_sessions)

    # Lazy per-workspace loading (dashboard/ACP-merge QA follow-up): skips the
    # full `get_sessions()` scan — a disk read and parse per provider per
    # workspace, the expensive part `_ACP_WORKSPACES_PATH`'s own docstring
    # measures at 975 of 1,210 sessions loaded for one 50x50 request — for a
    # workspace that is neither pinned nor the explicit target of a `cwd`-
    # scoped request. `include_provider` gates this exactly like the `pinned`
    # field itself: `/api/acp/sessions` has no pinned-workspace concept and
    # must keep paying for every group's sessions up front, unchanged. A
    # `cwd`-scoped request — the rail's own "More in <workspace>" follow-up —
    # always pays the full cost for that one workspace; it is an explicit ask
    # for its sessions.
    #
    # A workspace holding an individually pinned *session* (`pinned_set`,
    # distinct from a pinned *folder*) must still be fetched even while lazy,
    # or that session would silently drop out of the rail's "Pinned" section
    # until its workspace happened to be paged in some other way.
    # `find_session_workspace` is the same cheap per-session directory probe
    # `data.warmup_all` already runs at startup for this exact purpose — a
    # stat per workspace per pinned id, not a full session load per workspace.
    lazy_mode = include_provider and not cwd
    force_cwds: frozenset[str] = frozenset()
    if lazy_mode and pinned_set:
        found_cwds = set()
        for sid in pinned_set:
            found = data._find_pinned_session_workspace(sid)
            if found:
                found_cwds.add(_normalize_path(found[0]))
        force_cwds = frozenset(found_cwds)

    rows: list[tuple[dict, list]] = []  # (meta, [(session, provider), ...])
    sids: list[str] = []
    pinned_sessions_found: list[tuple[str, str, object, str]] = []  # (cwd, name, session, provider)
    # One hash lookup per workspace group, not per session — _lock_holder_v3's
    # workspace_hash fast path (Phase 1 cycle-2) needs a session_id -> hash-dir
    # mapping, and every session in a group shares the group's own hash dir.
    hash_by_sid: dict[str, str] = {}
    exists_flags = _acp_exists_flags([w[0] for w in page_groups])
    for index, (ws_cwd, ws_count, _updated, ws_provs) in enumerate(page_groups):
        ws_norm = _normalize_path(ws_cwd)
        is_pinned_folder = include_provider and ws_norm in pinned_folders_norm
        is_active = ws_norm in active_cwds
        ws_name = Path(ws_cwd).name or ws_cwd

        if lazy_mode and not is_pinned_folder and ws_norm not in force_cwds:
            meta = {
                "cwd": ws_cwd,
                "name": ws_name,
                "total": ws_count,
                "session_page": 0,
                "has_more": ws_count > 0,
                "exists": exists_flags[index],
                "pinned": is_pinned_folder,
                "active": is_active,
                # lazy_mode implies include_provider (see its definition
                # above), so this needs no separate guard the way the
                # non-lazy meta dict's color field does below.
                "color": _resolve_workspace_color(ws_cwd, config),
            }
            rows.append((meta, []))
            continue

        # One fetch per provider touching this workspace — a 1-item `ws_provs`
        # (every existing caller) makes this the exact same single call the
        # pre-Phase-4 code made, with no merge and no re-sort applied after.
        tagged: list[tuple[object, str]] = []
        for prov_name in ws_provs:
            try:
                tagged.extend((s, prov_name) for s in data.get_sessions(ws_cwd, prov_name))
            except Exception:
                log.exception("ACP listing: could not read %s sessions for %s",
                              prov_name, ws_cwd)
        if len(ws_provs) > 1:
            # Same cross-provider interleave the dashboard has always used.
            tagged.sort(key=lambda x: (x[0].updated_at or "").replace("Z", "+00:00"),
                        reverse=True)
        if pinned_set:
            # A session individually pinned within this workspace surfaces at
            # the top of the workspace's own row list too, not just the
            # separate "Pinned sessions" section above it — the same
            # coarsest-last stable sort the pinned-*workspace* ordering above
            # uses, so recency order is preserved within each of the two
            # partitions this only splits into. Must run before the
            # session_page slicing below: a pinned session that is not among
            # the most recent `session_size` would otherwise land on a later
            # page and never show pinned-at-top on the first one.
            tagged.sort(key=lambda x: x[0].session_id not in pinned_set)
        ws_hash = data_kiro_v3.hash_dir_for_cwd(ws_cwd)
        if pinned_set:
            for s, prov_name in tagged:
                if s.session_id in pinned_set:
                    pinned_sessions_found.append((ws_cwd, ws_name, s, prov_name))
                    if ws_hash:
                        hash_by_sid[s.session_id] = ws_hash
            # Pinned sessions remain in the workspace rows (so they appear both
            # in the "Pinned sessions" section and under their workspace) — the
            # client marks the pin button active via the `pinned` flag below.
        total = len(tagged)
        s_start = (session_page - 1) * session_size
        page_tagged = tagged[s_start:s_start + session_size]
        sids.extend(s.session_id for s, _p in page_tagged)
        if ws_hash:
            hash_by_sid.update({s.session_id: ws_hash for s, _p in page_tagged})
        meta = {
            "cwd": ws_cwd,
            "name": ws_name,
            "total": total,
            "session_page": session_page,
            "has_more": s_start + session_size < total,
            "exists": exists_flags[index],
        }
        if include_provider:
            meta["pinned"] = is_pinned_folder
            meta["active"] = is_active
            meta["color"] = _resolve_workspace_color(ws_cwd, config)
        rows.append((meta, page_tagged))

    # Pinned sessions from workspaces outside the current page (dashboard/ACP
    # provider-filter bug fix): `force_cwds` already identifies every workspace
    # that holds a pinned session, but the loop above only scans workspaces in
    # `page_groups` (the paginated slice). When a provider filter is wide (e.g.
    # "All") and there are many workspaces, a pinned session's workspace can
    # fall entirely outside the current page and its pinned session silently
    # disappears from the "Pinned sessions" section. This block fetches the
    # missing workspaces separately so every pinned session always appears,
    # regardless of which page its workspace would normally land on.
    if lazy_mode and force_cwds:
        page_group_norms = frozenset(_normalize_path(w[0]) for w in page_groups)
        off_page_cwds = force_cwds - page_group_norms
        if off_page_cwds:
            for ws_cwd, _ws_count, _updated, ws_provs in workspaces:
                ws_norm = _normalize_path(ws_cwd)
                if ws_norm not in off_page_cwds:
                    continue
                ws_name = Path(ws_cwd).name or ws_cwd
                off_tagged: list[tuple[object, str]] = []
                for prov_name in ws_provs:
                    try:
                        off_tagged.extend(
                            (s, prov_name)
                            for s in data.get_sessions(ws_cwd, prov_name)
                        )
                    except Exception:
                        log.exception(
                            "ACP listing: could not read %s sessions for %s "
                            "(off-page pinned scan)",
                            prov_name, ws_cwd,
                        )
                ws_hash = data_kiro_v3.hash_dir_for_cwd(ws_cwd)
                for s, prov_name in off_tagged:
                    if s.session_id in pinned_set:
                        pinned_sessions_found.append((ws_cwd, ws_name, s, prov_name))
                        if ws_hash:
                            hash_by_sid[s.session_id] = ws_hash

    pinned_sids = [s.session_id for _cwd, _name, s, _p in pinned_sessions_found]
    availability = _acp_availability(sids + pinned_sids, held, workspace_hashes=hash_by_sid)
    all_page_sessions = [s for _meta, page_tagged in rows for s, _p in page_tagged]
    statuses = _acp_status_for_held([
        s for s in all_page_sessions + [s for _c, _n, s, _p in pinned_sessions_found]
        if availability.get(s.session_id) == "held"], snapshot)

    def _row_dict(s, prov_name: str, pinned: bool = False) -> dict:
        d = {
            "id": s.session_id,
            "title": _acp_row_title(s),
            "updated_at": s.updated_at,
            "availability": availability.get(s.session_id, "available"),
            "status": statuses.get(s.session_id, ""),
        }
        if pinned:
            d["pinned"] = True
        if include_provider:
            d["provider"] = prov_name
            # Dashboard-only: its transcript header shows when the session
            # was started. Empty string when a provider does not record it.
            d["created_at"] = s.created_at
            # Cheap all-provider liveness dot (dashboard-only, see
            # _session_is_live): a row this ACP doesn't hold still gets a
            # binary alive/dead signal, just not the richer classified
            # verdict `status` carries for held rows.
            d["live"] = _session_is_live(snapshot, s, prov_name)
        return d

    groups = []
    for meta, page_tagged in rows:
        meta["sessions"] = [_row_dict(s, p, pinned=s.session_id in pinned_set) for s, p in page_tagged]
        groups.append(meta)

    pinned_cwds = list(dict.fromkeys(cwd for cwd, _n, _s, _p in pinned_sessions_found))
    pinned_exists = dict(zip(pinned_cwds, _acp_exists_flags(pinned_cwds)))
    pinned: list[dict] = [
        {**_row_dict(s, prov_name, pinned=True), "cwd": cwd, "name": name,
         "exists": pinned_exists.get(cwd, True)}
        for cwd, name, s, prov_name in pinned_sessions_found
    ]

    return {
        "groups": groups,
        "group_page": group_page,
        "group_total": group_total,
        "has_more": groups_has_more,
        "pinned": pinned,
        "capacity": capacity,
    }


def _acp_flat_listing(page: int, size: int, held, capacity: dict,
                       providers: frozenset[str] = frozenset({_ACP_V3_LISTING_PROVIDER}),
                       include_provider: bool = False, tag: str = "") -> dict:
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

    `providers` (dashboard/ACP-merge Phase 4) defaults to the single-provider
    set `/api/acp/sessions?mode=recent` has always used, so that call is
    byte-for-byte the same query as before — a row served there for another
    provider would be a session the phone cannot resume, the same constraint
    that makes the grouped listing single-provider by default, arriving from
    the opposite direction. `get_all_sessions_paginated` already spans
    whichever provider set it is given (`provider=None` plus
    `enabled_providers`), so widening this to several providers costs nothing
    beyond passing that same set through — unlike the grouped listing, no
    per-workspace merge is needed here: this route's rows are sessions, not
    workspaces, so a session from two different providers was always going
    to be two different rows.

    `include_provider`, off by default: adds a `"provider"` field to every
    session dict. See `_acp_listing`'s own parameter of the same name for why
    `/api/acp/sessions` must never turn this on and the dashboard's route
    always does.

    `tag` (dashboard/ACP-merge Phase 4): same three-way rule as
    `_acp_listing`'s own `tag` parameter — see that docstring. No
    `time_filter` here; see `_acp_listing` for why grouped mode alone
    carries it.
    """
    from .config import get_workspace_settings

    config = load_config()
    enabled = frozenset(p for p in providers if _enabled(config, p))
    if not enabled:
        return {"sessions": [], "pinned": [], "page": page, "has_more": False,
                "capacity": capacity}
    # See `_acp_listing`'s identical fetch: shared across the "live" field
    # below and `_acp_status_for_held`, one cached scan either way.
    snapshot = presence.get_snapshot()

    pinned_set = set(config.pinned_sessions)
    workspaces_list = (
        data.discover_workspaces_with_counts(next(iter(enabled)))
        if len(enabled) == 1 else
        [w for w in data.discover_workspaces_with_counts(None) if w[3] in enabled]
    )

    def _tag_keep(ws_cwd: str) -> bool:
        tags = get_workspace_settings(config, ws_cwd)["tags"]
        if not tag:
            return "hidden" not in tags
        if tag == "hidden":
            return "hidden" in tags
        return tag in tags

    hidden = {w[0] for w in workspaces_list if not _tag_keep(w[0])}
    try:
        rows, has_more = data.get_all_sessions_paginated(
            page=page, page_size=size,
            provider=next(iter(enabled)) if len(enabled) == 1 else None,
            enabled_providers=enabled,
            exclude_cwds=hidden,
            pinned_sessions=config.pinned_sessions if pinned_set else None)
    except Exception:
        log.exception("ACP flat listing: could not collect sessions")
        rows, has_more = [], False

    pinned_raw = [(s, prov) for s, prov in rows if s.session_id in pinned_set]
    flat_rows  = [(s, prov) for s, prov in rows if s.session_id not in pinned_set]

    if pinned_set:
        found_ids = {s.session_id for s, _ in pinned_raw}
        remaining = pinned_set - found_ids
        if remaining:
            for ws_cwd, _count, _updated, ws_prov in workspaces_list:
                if not remaining:
                    break
                if ws_cwd in hidden:
                    continue
                try:
                    ws_sessions = data.get_sessions(ws_cwd, ws_prov)
                except Exception:
                    continue
                for s in ws_sessions:
                    if s.session_id in remaining:
                        pinned_raw.append((s, ws_prov))
                        remaining.discard(s.session_id)

    sessions = [s for s, _prov in flat_rows]
    pinned_sessions_list = [s for s, _prov in pinned_raw]

    all_sids = [s.session_id for s in sessions] + [s.session_id for s in pinned_sessions_list]
    availability = _acp_availability(all_sids, held)
    statuses = _acp_status_for_held(
        [s for s in sessions + pinned_sessions_list
         if availability.get(s.session_id) == "held"], snapshot)

    order = list(dict.fromkeys(s.cwd for s in sessions + pinned_sessions_list))
    flags = dict(zip(order, _acp_exists_flags(order)))

    def _session_dict(s: object, prov_name: str) -> dict:
        d = {
            "id": s.session_id,
            "title": _acp_row_title(s),
            "updated_at": s.updated_at,
            "availability": availability.get(s.session_id, "available"),
            "status": statuses.get(s.session_id, ""),
            "cwd": s.cwd,
            "name": Path(s.cwd).name or s.cwd,
            "exists": flags.get(s.cwd, True),
        }
        if include_provider:
            d["provider"] = prov_name
            d["created_at"] = s.created_at  # see _acp_listing's _row_dict
            # See _acp_listing's identical field for what this is and why.
            d["live"] = _session_is_live(snapshot, s, prov_name)
        return d

    return {
        "sessions": [_session_dict(s, prov) for s, prov in flat_rows],
        "pinned": [_session_dict(s, prov) for s, prov in pinned_raw],
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

    Returns only the workspace path, display name and whether that directory
    still exists, and per session the id, title, updated timestamp,
    availability state and — for a held session alone — the semantic status
    the dashboard would show for it. No `env`, no launcher data, no action
    affordances — the payload is the whole audit surface, so what is not here
    cannot leak from here.

    `status` is `""` for every session this ACP does not hold, and that is a
    narrowing rather than an omission: it is a reading of a transcript's last
    lines, and the sessions worth spending that on are the ones the rail draws
    a dot for. See `_acp_status_for_held`.

    **The whole store is reachable through this route, not a sample of it.**
    Paging is the entire access-control story here: `group_page` walks the
    workspace axis and `session_page` walks the sessions inside each one, and
    neither has a ceiling other than the data running out — so an authorized
    peer that keeps asking enumerates every workspace path and every session
    title on this machine. The rail's default page size is a page size, not a
    bound.

    `mode=recent` does not widen that exposure — same route, same store, same
    two exclusions — but it does make collecting it cheaper: it answers one
    flat recency-ordered walk with `page`/`has_more`, so a peer enumerating the
    store follows a single cursor to the end instead of crossing two nested
    axes and reconciling them.

    **`title` may be raw user prompt text.** `_acp_row_title` falls back to
    the first 120 characters of the session's first prompt whenever the store
    holds no title or the literal `"<untitled>"`. Anyone deciding whether to
    enable remote access should read this route as publishing the names of
    everything worked on, to every peer holding a valid device cookie.

    Workspaces tagged `hidden` and a disabled `kiro-cli-v3` provider are
    excluded; see `_acp_listing`.

    `no-store`: availability is a liveness reading with a lifetime of
    seconds, and a phone rendering a cached `available` for a session another
    process took in the meantime is exactly the wrong failure to cache.
    """
    response.headers["Cache-Control"] = "no-store"
    supervisor = getattr(acp, "_supervisor", None) if acp is not None else None
    held = frozenset(supervisor.sessions) if supervisor is not None else frozenset()
    capacity = {
        "held": ((len(held) + supervisor._reserved) if supervisor is not None else 0),
        "max": acp.MAX_SESSIONS if acp is not None else 0,
    }
    if mode == "recent":
        return await asyncio.to_thread(
            _acp_flat_listing, max(1, page),
            max(1, min(size, _ACP_MAX_FLAT_PAGE_SIZE)), held, capacity)
    return await asyncio.to_thread(
        _acp_listing, cwd,
        max(1, group_page), max(1, min(group_size, _ACP_MAX_GROUPS_PER_PAGE)),
        max(1, session_page), max(1, min(session_size, _ACP_MAX_SESSIONS_PER_GROUP)),
        held, capacity)


_DASHBOARD_LISTING_PATH = "/api/dashboard/sessions"


@app.get(_DASHBOARD_LISTING_PATH)
async def api_dashboard_sessions(response: Response, cwd: str = "", group_page: int = 1,
                                 group_size: int = _ACP_GROUPS_PER_PAGE,
                                 session_page: int = 1,
                                 session_size: int = _ACP_SESSIONS_PER_GROUP,
                                 mode: str = "", page: int = 1,
                                 size: int = _ACP_FLAT_PAGE_SIZE,
                                 provider: str = "", tag: str = "",
                                 time_filter: str = "", project_sort: str = "recent",
                                 q: str = ""):
    """The dashboard's own rail feed (dashboard/ACP-merge Phase 4): every
    enabled, available provider, not only kiro-cli-v3. Same parameters,
    pagination, `hidden`-tag/disabled-provider exclusions and grouped/
    `mode=recent` shapes as `/api/acp/sessions` — this route only widens
    `_acp_listing`/`_acp_flat_listing`'s own `providers` argument and turns
    on `include_provider` (each session dict names which provider it came
    from, since unlike the v3-only route this one's rows are not all the
    same provider). See `api_acp_sessions` for the full field documentation,
    which otherwise applies here verbatim.

    `provider`, `tag`, `time_filter`: the dashboard's existing workspace
    filters (provider tabs, tag filter/management, time filter), preserved
    across the move from the dashboard's old server-rendered workspace list
    to its client-side rail — see `_acp_listing`'s own docstring for the
    exact `tag`/`time_filter` semantics. `provider=""` (the default) means
    every enabled+available provider; a specific name narrows `providers` to
    just that one, the same choice the dashboard's provider tabs have always
    made.

    `project_sort` (dashboard/ACP-merge QA follow-up): forwarded to
    `_acp_listing`'s `sort` verbatim except for validation — anything other
    than the two values it recognizes falls back to `"recent"` rather than
    reaching a `.sort()` call with a key function that silently does nothing
    a caller could notice. `/api/acp/sessions` has no equivalent parameter;
    its Project-mode ordering is always `"recent"`.

    Not scoped any differently than the rest of the dashboard: this app's
    `RemoteAccessGuard` middleware already covers every route including this
    one, and a session's title/path is no more exposed here than it already
    was through the dashboard's old server-rendered listing — this is a
    second reader of the same store, not a wider one.
    """
    response.headers["Cache-Control"] = "no-store"
    supervisor = getattr(acp, "_supervisor", None) if acp is not None else None
    held = frozenset(supervisor.sessions) if supervisor is not None else frozenset()
    capacity = {
        "held": ((len(held) + supervisor._reserved) if supervisor is not None else 0),
        "max": acp.MAX_SESSIONS if acp is not None else 0,
    }
    available = frozenset(data.available_providers())
    providers = frozenset({provider}) & available if provider else available
    sort = project_sort if project_sort in ("recent", "alpha") else "recent"
    if mode == "recent":
        return await asyncio.to_thread(
            _acp_flat_listing, max(1, page),
            max(1, min(size, _ACP_MAX_FLAT_PAGE_SIZE)), held, capacity,
            providers, True, tag)
    return await asyncio.to_thread(
        _acp_listing, cwd,
        max(1, group_page), max(1, min(group_size, _ACP_MAX_GROUPS_PER_PAGE)),
        max(1, session_page), max(1, min(session_size, _ACP_MAX_SESSIONS_PER_GROUP)),
        held, capacity, providers, True, tag, time_filter, sort, q)


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
# because each group's `total` needs the whole list — measured pre-cutover
# against the v2 store at 975 of 1,210 sessions loaded for a single 50x50
# request — so building the workspace picker out of several pages of it would
# be one of the most expensive requests the app makes.
# `discover_workspaces_with_counts` already carries the count, is cached for 30 s
# and is the same call the dashboard makes, so this is a filter over a warm list.
#
# `_ACP_WORKSPACES_PATH` itself is defined next to `_REMOTE_AUTH_PATH` at the top
# of this file, because the allowlist literal is evaluated before this point.


def _acp_workspaces(capacity: dict) -> dict:
    """Workspaces a session can be created in. Blocking; runs off the loop.

    Excludes the same two sets `_acp_listing` excludes — `hidden`-tagged
    workspaces and a disabled provider — plus a third this route needs and that
    one does not: **workspaces whose directory is gone**. `_resolve_session_cwd`
    refuses every one of them with `BadCwd`, so offering them as create targets
    would be offering guaranteed failures. The listing route keeps them because
    reading an old conversation from a deleted tree is perfectly reasonable;
    creating a new one there is not.

    The count of what was dropped is reported rather than swallowed: a picker
    that silently shows fewer workspaces than actually exist reads as a broken
    list.
    """
    from .config import get_workspace_settings

    config = load_config()
    if not _enabled(config, _ACP_V3_LISTING_PROVIDER):
        return {"workspaces": [], "missing": 0, "capacity": capacity}
    found = [
        w for w in data.discover_workspaces_with_counts(_ACP_V3_LISTING_PROVIDER)
        if "hidden" not in get_workspace_settings(config, w[0])["tags"]
    ]
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
    supervisor = getattr(acp, "_supervisor", None) if acp is not None else None
    held = frozenset(supervisor.sessions) if supervisor is not None else frozenset()
    capacity = {
        "held": ((len(held) + supervisor._reserved) if supervisor is not None else 0),
        "max": acp.MAX_SESSIONS if acp is not None else 0,
    }
    return await asyncio.to_thread(_acp_workspaces, capacity)


# --- The session browser's delete action ---------------------------------
#
# **The first thing in PowerAtlas that writes to kiro-cli's store.** Everything
# else that touches `~/.kiro/sessions/` reads it: `data_kiro_v3` parses and
# caches, `acp._lock_holder_v3` reads `session.json` status, and the listing
# route above says "Read-only" in its first line. That is worth stating once,
# here, because it is the property a reviewer would otherwise assume still held.
#
# **On `_REMOTE_ALLOWED_PATHS`**: `_ACP_DELETE_PATH` is now in that map, so
# authenticated remote peers can reach this route. The mobile-UA exclusion
# (`ACP_CAN_DELETE=false`) is advisory / UI-only — the auth boundary is the
# device cookie + Origin/Referer check (`same_origin_guard`, `:809`), identical
# to `_ACP_LISTING_PATH`. A mobile browser that POSTs directly (bypassing the
# JS gate) is stopped by those two layers, not by a UA check here. The page
# cooperates: `/acp` renders the menu only when `ACP_CAN_DELETE` is true, so
# a mobile viewer is not offered a control in the first place. Being a POST,
# it also inherits `same_origin_guard`'s Origin/Referer check (`:809`) for free.
# (`_ACP_DELETE_PATH` is declared near `_REMOTE_ALLOWED_PATHS` above for the
# same reason `_ACP_LISTING_PATH` and `_ACP_WORKSPACES_PATH` are — module-level
# dict built at import time.)

# A caller-supplied list is a loop bound, so it is capped. The UI sends exactly
# one; the list form exists so that adding bulk deletion later is a change to
# the page rather than to the protocol.
_ACP_MAX_DELETE_IDS = 200


def _acp_delete_session(session_id: str) -> tuple[str, str]:
    """Delete one session from kiro-cli's store. Returns (error_code, message) or ("", "")."""
    from . import data_kiro_v3
    result = data_kiro_v3.delete_session(session_id)
    if result is None:
        return "not_found", "Session not found in the kiro-cli store."
    if result is False:
        return "in_use", "A process still has this session's files open. Close it there, then try again."
    return "", ""


def _acp_delete_many(session_ids: list[str], held: frozenset) -> dict:
    """Delete each id, refusing the ones that are not safe. Blocking.

    `held` is a snapshot taken on the loop and passed in, for the reason
    `_acp_availability` gives at length: `_supervisor.sessions` is loop-owned
    and unlocked, and iterating it from a worker thread is a torn read (D9).
    Its membership already answers "does *this* process (whichever supervisor
    the caller snapshotted — v2's or v3's) have this session open" correctly
    for either id shape, since the caller is the one that chooses which
    supervisor to snapshot.

    Every other per-id check below dispatches on id shape, exactly like
    `_acp_availability` already does: all remaining session IDs are v3
    (`sess_`-prefixed), routed to `acp._lock_holder_v3` /
    `acp._stored_session_cwd_v3` / `_acp_delete_session`. A non-`sess_`-prefixed
    id passed by a browser returns gracefully: `_lock_holder_v3` returns `None`,
    `_stored_session_cwd_v3` returns `""`, and `_acp_delete_session` returns
    `"not_found"`.
    """
    deleted: list[str] = []
    failed: list[dict] = []
    touched_v3: set[str] = set()

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
            holder = acp._lock_holder_v3(session_id)
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
        cwd = acp._stored_session_cwd_v3(session_id)
        code, message = _acp_delete_session(session_id)
        if code:
            failed.append({"id": session_id, "code": code, "message": message})
            continue
        deleted.append(session_id)
        if cwd:
            touched_v3.add(cwd)

    if deleted:
        # The store has changed under caches that key on it.
        # `session_cache` holds the parsed list per workspace and
        # `discover_workspaces_with_counts` holds the counts for 30 s — so
        # without these the deleted row comes back on the next Refresh and
        # the workspace header keeps counting it.
        for cwd in touched_v3:
            data.session_cache.forget(cwd, _ACP_V3_LISTING_PROVIDER)
        data.invalidate_workspace_counts()
        log.info("ACP delete: removed %d session(s) across %d workspace(s)",
                 len(deleted), len(touched_v3))

    return {"deleted": deleted, "failed": failed}



def _remove_workspace_from_config(cwd: str, config) -> None:
    """Remove *cwd* from pinned_folders and workspace_settings (in-place mutation)."""
    from .data import _normalize_path
    norm = _normalize_path(cwd)
    config.pinned_folders = [
        f for f in config.pinned_folders
        if _normalize_path(f) != norm
    ]
    for k in [k for k in config.workspace_settings if _normalize_path(k) == norm]:
        del config.workspace_settings[k]


def _acp_delete_workspace_folder(cwd: str) -> tuple[bool, str]:
    """Delete the workspace directory and clean its config entries.

    Validates the path, removes the directory with shutil.rmtree, then removes
    *cwd* from config.pinned_folders and config.workspace_settings. All three
    steps run here in a single worker-thread call so no pre-loaded Config needs
    to be passed in (passing one would create a lost-update race window with
    concurrent settings mutations).

    Returns ``(deleted: bool, error: str)``. *error* is ``""`` on full success
    and ``"folder_already_gone"`` when the folder was missing but config was
    cleaned. A non-empty *error* with *deleted=False* signals a validation or
    OS failure.

    TOCTOU note: the is_dir() check and rmtree are not atomic; a symlink swapped
    in during that window could be followed. The is_symlink() guard at entry
    mitigates the common case. Accepted on a single-user desktop.
    """
    import platform
    p = Path(cwd)
    # --- Path safety checks (ordered, fail-fast) ---
    if not p.is_absolute():
        return False, "Path is not absolute."
    # Symlink check must run before resolve() — resolve() follows symlinks and
    # returns the real target, so is_symlink() on the resolved path is always False.
    if p.is_symlink():
        return False, "Refusing to delete a symbolic link."
    try:
        p = p.resolve()
    except OSError:
        return False, "Could not resolve path."
    if str(p).startswith("\\\\") or str(p).startswith("//"):
        return False, "Refusing UNC path."
    min_depth = 4 if platform.system() == "Windows" else 3
    if len(p.parts) < min_depth:
        return False, (
            f"Path too shallow to be a workspace (need >= {min_depth} parts)."
        )
    home = Path.home().resolve()
    if p == home or p == home.parent:
        return False, "Refusing to delete home directory or its parent."
    # --- Load, mutate, save config (all in this worker thread) ---
    config = load_config()
    refusal = unreadable_config_message(config)
    if refusal:
        # Before anything is deleted: the final `save_config` would refuse this
        # config (Phase 1 re-review, finding 1), leaving the folder gone and
        # its workspace entries still in config.toml.
        return False, refusal + "."
    _remove_workspace_from_config(cwd, config)
    if not p.exists():
        save_config(config)  # still clean config even if folder is gone
        return False, "folder_already_gone"
    if not p.is_dir():
        save_config(config)  # H3: still persist config cleanup for non-directory paths
        return False, "Path is not a directory."
    try:
        import shutil as _shutil
        _shutil.rmtree(p)
    except OSError as exc:
        return False, f"Could not delete folder: {exc}"
    save_config(config)
    return True, ""


@app.post(_ACP_DELETE_PATH)
async def api_acp_delete_sessions(request: Request):
    """Delete sessions from kiro-cli's store. Irreversible.

    Reachable from authenticated remote peers (device cookie + Origin/Referer).
    The mobile-UA exclusion (`ACP_CAN_DELETE=false`) is advisory / UI-only;
    the auth boundary is `same_origin_guard`'s cookie + Origin/Referer check,
    the same posture as `_ACP_LISTING_PATH`. Note that the `held` snapshot
    taken on the event loop is a primary check; the rename-staging
    sharing-violation (`winerror 32`) is the correctness backstop for the
    TOCTOU gap between the snapshot and the rename.

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

    # --- Workspace-level delete path ---
    # Handled first so the existing `session_ids` guard never sees a `cwd` request.
    cwd: str | None = body.get("cwd") if isinstance(body, dict) else None
    delete_folder: bool = (
        bool(body.get("delete_folder", False)) if isinstance(body, dict) else False
    )
    if cwd is not None:
        if not isinstance(cwd, str) or not cwd.strip():
            return JSONResponse(
                {"error": "'cwd' must be a non-empty string."}, status_code=400)
        # Enumerate all session IDs for this workspace (off the event loop).
        from . import data_kiro_v3
        _v3_sessions, _ = await asyncio.to_thread(data_kiro_v3.load_sessions, cwd)
        all_ids = [s.session_id for s in _v3_sessions]
        # Batch delete, D9: re-snapshot `held` on the event loop before each
        # thread hop, because `_supervisor.sessions` is loop-owned and unlocked.
        deleted_total: list[str] = []
        failed_total: list[dict] = []
        while all_ids:
            batch, all_ids = all_ids[:_ACP_MAX_DELETE_IDS], all_ids[_ACP_MAX_DELETE_IDS:]
            sv3 = getattr(acp, "_supervisor", None)
            held = (frozenset(acp._supervisor.sessions)  # event-loop snapshot (D9)
                    | frozenset(sv3.sessions if sv3 is not None else ()))
            result = await asyncio.to_thread(_acp_delete_many, batch, held)
            deleted_total.extend(result["deleted"])
            failed_total.extend(result["failed"])
        response: dict = {
            "deleted": deleted_total,
            "failed": failed_total,
            "total_found": len(deleted_total) + len(failed_total),
        }
        # Folder delete: loopback-only guard — irreversible local filesystem
        # operation must not be triggerable from a remote device.
        # D26: use scope["client"] (transport-level peer IP) not the Host header,
        # which is attacker-controlled and would let a remote peer spoof loopback.
        if delete_folder:
            peer_ip = (request.scope.get("client") or (None,))[0]
            if _is_remote_peer(peer_ip):
                response["folder_deleted"] = False
                response["folder_error"] = (
                    "Folder deletion is not available from remote access."
                )
            else:
                try:
                    folder_deleted, folder_error = await asyncio.to_thread(
                        _acp_delete_workspace_folder, cwd
                    )
                except Exception as exc:
                    folder_deleted = False
                    folder_error = f"Folder delete failed unexpectedly: {exc}"
                response["folder_deleted"] = folder_deleted
                response["folder_error"] = folder_error
        return JSONResponse(response)

    # --- Per-session delete path (existing, unchanged) ---
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
    #
    # Cross-engine held-set union (Step 9 final review fix, High): the
    # per-session-ID path never got the same union the workspace-cwd path
    # above already has — see that comment for the full rationale.
    sv3 = getattr(acp, "_supervisor", None)
    held = (frozenset(acp._supervisor.sessions)
            | frozenset(sv3.sessions if sv3 is not None else ()))
    result = await asyncio.to_thread(_acp_delete_many, session_ids, held)
    return JSONResponse({
        "deleted": result["deleted"],
        "failed": result["failed"],
        "total_found": len(session_ids),
    })




# --- v3 ACP session browser endpoints ---------------------------------
#
# Mirrors of the v2 listing, workspaces, and delete endpoints for the
# ``/acp-v3`` surface. Same security posture; supervisor calls route to
# ``acp._supervisor`` instead of ``acp._supervisor``.


@app.get(_ACP_LISTING_PATH)
async def api_acp_v3_sessions(response: Response, cwd: str = "",
                              group_page: int = 1,
                              group_size: int = _ACP_GROUPS_PER_PAGE,
                              session_page: int = 1,
                              session_size: int = _ACP_SESSIONS_PER_GROUP,
                              mode: str = "", page: int = 1,
                              size: int = _ACP_FLAT_PAGE_SIZE):
    """v3 session listing. Mirrors ``api_acp_sessions`` with ``_supervisor``."""
    response.headers["Cache-Control"] = "no-store"
    sv3 = getattr(acp, "_supervisor", None) if acp is not None else None
    held = frozenset(sv3.sessions) if sv3 is not None else frozenset()
    capacity = {
        "held": ((len(held) + sv3._reserved) if sv3 is not None else 0),
        "max": acp.MAX_SESSIONS if acp is not None else 0,
    }
    if mode == "recent":
        return await asyncio.to_thread(
            _acp_flat_listing_v3, max(1, page),
            max(1, min(size, _ACP_MAX_FLAT_PAGE_SIZE)), held, capacity)
    return await asyncio.to_thread(
        _acp_listing_v3, cwd,
        max(1, group_page), max(1, min(group_size, _ACP_MAX_GROUPS_PER_PAGE)),
        max(1, session_page), max(1, min(session_size, _ACP_MAX_SESSIONS_PER_GROUP)),
        held, capacity)


@app.get(_ACP_WORKSPACES_PATH)
async def api_acp_v3_workspaces(response: Response):
    """v3 workspace list for the create picker. Mirrors ``api_acp_workspaces``."""
    response.headers["Cache-Control"] = "no-store"
    sv3 = getattr(acp, "_supervisor", None) if acp is not None else None
    held = frozenset(sv3.sessions) if sv3 is not None else frozenset()
    capacity = {
        "held": ((len(held) + sv3._reserved) if sv3 is not None else 0),
        "max": acp.MAX_SESSIONS if acp is not None else 0,
    }
    return await asyncio.to_thread(_acp_workspaces_v3, capacity)


@app.post(_ACP_DELETE_PATH)
async def api_acp_v3_delete_sessions(request: Request):
    """v3 session delete. Mirrors ``api_acp_delete_sessions`` with ``_supervisor``.

    Uses the same workspace-level and per-session delete paths as the v2
    endpoint, but snapshots ``_supervisor.sessions`` for the held set.
    """
    if acp is None:
        return JSONResponse(
            {"error": "The ACP module is not loaded, so its store is not "
                      "reachable from here."}, status_code=503)
    sv3 = getattr(acp, "_supervisor", None)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Expected a JSON body."}, status_code=400)

    # --- Workspace-level delete path ---
    cwd: str | None = body.get("cwd") if isinstance(body, dict) else None
    delete_folder: bool = (
        bool(body.get("delete_folder", False)) if isinstance(body, dict) else False
    )
    if cwd is not None:
        if not isinstance(cwd, str) or not cwd.strip():
            return JSONResponse(
                {"error": "'cwd' must be a non-empty string."}, status_code=400)
        from . import data_kiro_v3
        _v3_sessions, _ = await asyncio.to_thread(data_kiro_v3.load_sessions, cwd)
        all_ids = [s.session_id for s in _v3_sessions]
        # Enumerate v3 session IDs for this workspace (off the event loop).
        # `data_kiro_v3.load_sessions(cwd)` returns (list[Session], dict) — unpack correctly.
        # `acp._supervisor.sessions` already carries every live id regardless of shape.

        deleted_total: list[str] = []
        failed_total: list[dict] = []
        while all_ids:
            batch, all_ids = all_ids[:_ACP_MAX_DELETE_IDS], all_ids[_ACP_MAX_DELETE_IDS:]
            held = frozenset(acp._supervisor.sessions)
            result = await asyncio.to_thread(_acp_delete_many, batch, held)
            deleted_total.extend(result["deleted"])
            failed_total.extend(result["failed"])
        resp_data: dict = {
            "deleted": deleted_total,
            "failed": failed_total,
            "total_found": len(deleted_total) + len(failed_total),
        }
        if delete_folder:
            peer_ip = (request.scope.get("client") or (None,))[0]
            if _is_remote_peer(peer_ip):
                resp_data["folder_deleted"] = False
                resp_data["folder_error"] = (
                    "Folder deletion is not available from remote access."
                )
            else:
                try:
                    folder_deleted, folder_error = await asyncio.to_thread(
                        _acp_delete_workspace_folder, cwd
                    )
                except Exception as exc:
                    folder_deleted = False
                    folder_error = f"Folder delete failed unexpectedly: {exc}"
                resp_data["folder_deleted"] = folder_deleted
                resp_data["folder_error"] = folder_error
        return JSONResponse(resp_data)

    # --- Per-session delete path ---
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
    # `_acp_delete_many` dispatches each id by shape — `sess_`-prefixed routes
    # to `data_kiro_v3.delete_session` (via `_acp_delete_session`'s own v3
    # branch) and `_lock_holder_v3`'s externally-held check, anything else to
    # the historical v2 path — so every requested id is handled by one call.
    held = frozenset(acp._supervisor.sessions)
    result = await asyncio.to_thread(_acp_delete_many, session_ids, held)
    return JSONResponse({
        "deleted": result["deleted"],
        "failed": result["failed"],
        "total_found": len(session_ids),
    })


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


def _exchange_backoff_remaining(peer: str, store: dict | None = None) -> float:
    """Seconds left on this peer's lockout, or 0.0.

    Exponential from the first failure, capped. Checked **before** the secret
    is compared, so it throttles guessing rather than merely recording it.

    ``store`` defaults to the per-peer `_exchange_failures`; the login-code
    exchange passes its per-code `_login_failures` instead (D-16,
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4).
    """
    store = _exchange_failures if store is None else store
    count, last, _ = store.get(peer, (0, 0.0, False))
    if count <= 0:
        return 0.0
    delay = min(_EXCHANGE_BASE_BACKOFF_SECONDS * (2 ** min(count - 1, 8)),
                _EXCHANGE_MAX_BACKOFF_SECONDS)
    remaining = delay - (time.monotonic() - last)
    return remaining if remaining > 0 else 0.0


def _record_exchange_failure(peer: str, store: dict | None = None) -> None:
    # `store`: see `_exchange_backoff_remaining`.
    # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
    store = _exchange_failures if store is None else store
    count = store.get(peer, (0, 0.0, False))[0]
    if peer not in store and len(store) >= _EXCHANGE_MAX_TRACKED_PEERS:
        store.pop(next(iter(store)), None)
    # `False`: a new failure opens a new lockout window, and the first refusal
    # inside it is worth one line.
    store[peer] = (count + 1, time.monotonic(), False)


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

    The comparison is constant-time over UTF-8 bytes because
    `compare_digest` raises `TypeError` on a `str`
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


# --- The login-code exchange ---------------------------------------------------
#
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4. Mirrors
# `remote_auth_exchange`'s hardening — bounded input before any parse,
# `compare_digest` over bytes, a backoff consulted before the comparison, a
# bounded tracking dict, never a 500 — with one deliberate difference (D-16):
# the throttle is keyed **per code**, not per peer. Every loopback caller is
# `127.0.0.1`, so a peer-keyed lockout would let one bad local process throttle
# the real user's next door for up to five minutes.

# Per-code failure records, `_exchange_failures`'s tuple shape. Keys are only
# ever strings matching `_LOGIN_CODE_RE`, so each key is bounded, and the dict
# is bounded by `_EXCHANGE_MAX_TRACKED_PEERS` through `_record_exchange_failure`.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
_login_failures: dict[str, tuple[int, float, bool]] = {}

# A refused exchange logs one WARNING per this many seconds, however many
# arrive. Per-code windows would not bound the log: a local process can invent
# a new code per request, and each would open a new window.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
_LOGIN_WARN_INTERVAL_SECONDS = 60.0
_login_warn_state = {"last": float("-inf"), "suppressed": 0}

# The query string carries one 43-character field; 512 bytes is a >10x margin
# and is checked before `parse_qsl` builds anything.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
_LOCAL_AUTH_MAX_QUERY = 512

_LOCAL_AUTH_REFUSED = """<!doctype html>
<title>PowerAtlas</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<h1>PowerAtlas</h1>
<p>{message}</p>
<p>Open PowerAtlas from its tray icon to sign in again.</p>
"""


def _warn_login_refused(reason: str, level: int = logging.WARNING) -> None:
    """One line per `_LOGIN_WARN_INTERVAL_SECONDS`, counting the rest.

    ``level`` lets the no-secret refusal keep its ERROR severity while sharing
    this one window, so no refusal path logs once per request.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4 review
    """
    _rate_limited_log(_login_warn_state, level,
                      "login-code exchange refused: %s (%d further refusals "
                      "suppressed since the last line)", reason)


def _flush_login_refusal_warnings() -> None:
    """Write the suppressed-refusal count now, if there is one.

    Called from `lifespan` teardown, after the precedent of
    `__main__._RepeatedRecordFilter.flush`: a suppressed count is otherwise
    only reported by the next refusal, so the final burst's count would never
    reach the log.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4 review
    """
    _flush_rate_limited_log(_login_warn_state, "login-code exchange")


def _local_auth_refusal(message: str, status_code: int) -> HTMLResponse:
    response = HTMLResponse(_LOCAL_AUTH_REFUSED.format(message=message),
                            status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get(_LOCAL_AUTH_PATH)
async def local_auth_exchange(request: Request):
    """Trade a one-time login code for the loopback cookie, then go to `/`.

    Script-free and self-contained for the same reason `remote_auth_page` is.
    A GET because a door opens it as a URL; there is no body to bound, so the
    body and field ceilings of `remote_auth_exchange` become a query-length
    ceiling checked before parsing.
    """
    query = request.scope.get("query_string", b"") or b""
    if len(query) > _LOCAL_AUTH_MAX_QUERY:
        _warn_login_refused("query string too long")
        return _local_auth_refusal("That sign-in link is not valid.", 400)
    try:
        pairs = parse_qsl(query.decode("latin-1"), keep_blank_values=True,
                          max_num_fields=_REMOTE_AUTH_MAX_FIELDS)
    except ValueError:
        _warn_login_refused("too many fields")
        return _local_auth_refusal("That sign-in link is not valid.", 400)
    supplied = dict(pairs).get("code", "")
    # Shape before anything else, so a throttle key and a log line are never
    # attacker-sized.
    # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
    if not _LOGIN_CODE_RE.fullmatch(supplied):
        _warn_login_refused("malformed code")
        return _local_auth_refusal("That sign-in link is not valid.", 400)
    remaining = _exchange_backoff_remaining(supplied, _login_failures)
    if remaining > 0:
        _warn_login_refused("code throttled")
        return _local_auth_refusal(
            f"Too many attempts with this link. Try again in "
            f"{int(remaining) + 1}s.", 429)
    if not _LOCAL_SECRET:
        # Rate-limited like every other refusal, so a local process cannot
        # write one ERROR line per request.
        # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4 review
        _warn_login_refused("no usable local secret", logging.ERROR)
        return _local_auth_refusal("PowerAtlas has no usable local secret.", 503)
    if not _consume_login_code(supplied):
        _record_exchange_failure(supplied, _login_failures)
        _warn_login_refused("unknown, expired or already-used code")
        return _local_auth_refusal(
            "That sign-in link has expired or was already used.", 403)
    _login_failures.pop(supplied, None)
    # 303 so the browser GETs `/` and the code leaves the address bar and the
    # history entry the user will see. `no-referrer` keeps the (now dead) code
    # out of any Referer the landing page sends.
    # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
    response = Response(status_code=303, headers={
        "Location": "/", "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer"})
    _set_local_cookie(response)
    log.info("loopback browser signed in with a login code")
    return response


@app.post("/api/autostart")
async def toggle_autostart():
    if autostart.is_enabled():
        autostart.disable()
    else:
        autostart.enable()
    return {"enabled": autostart.is_enabled()}


# Its own pair of routes rather than a `/api/save-setting` key, and that is not
# arbitrary. That endpoint validates against `_SETTING_TYPES`, which holds only
# `int`/`str`/`list`, and it rejects every boolean outright -- a guard that
# exists because `isinstance(True, int)` is True in Python and a stray bool
# would otherwise sail through the int check for an unrelated key. Teaching it
# booleans to carry this one flag would weaken that guard for all nine existing
# keys. `notifications` is also a nested dict, not the flat scalar that
# endpoint's `setattr` shape assumes. `/api/autostart` is the precedent for a
# boolean that owns its own route.
#
# Read live on every event, so a change takes effect immediately -- this is
# deliberately NOT a `_RESTART_TO_APPLY` key. The ACP tunables are snapshotted
# at startup because `at_capacity()` runs on the event loop and would pay an
# uncached TOML parse per call; a notification fires once per turn, which is
# orders of magnitude rarer than the ~16 routes already parsing per request.
def _notifications_enabled() -> bool:
    """Whether desktop notifications are on.

    Always `.get`, never `["enabled"]`: a bare `[notifications]` table in
    `config.toml` loads as `{}` rather than the dataclass default, because
    `load_config` passes the empty dict explicitly and a dataclass default
    applies only to an omitted kwarg. Indexing would raise on a hand-edited
    config.
    """
    return bool(load_config().notifications.get("enabled", False))


@app.get("/api/notifications")
async def get_notifications():
    return {"enabled": _notifications_enabled()}


@app.post("/api/notifications")
async def toggle_notifications():
    config = load_config()
    # Mutate and save the *same* instance `load_config` returned: it carries
    # unknown top-level keys on `_extra`, which `save_config` restores from
    # that attribute. Saving a freshly built Config would drop them.
    enabled = not bool(config.notifications.get("enabled", False))
    config.notifications = {"enabled": enabled}
    save_config(config)
    return {"enabled": enabled}


# --- ACP permission mode ---------------------------------------------------
# 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL Phase 1, replacing the on/off
# route pair of 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 1.
#
# Its own pair of routes rather than a `_SETTING_TYPES` entry: a mode change
# must save and regenerate under one lock (`agent_profile.apply_settings`,
# D-16). Neither route is in `_REMOTE_ALLOWED_PATHS`, so a remote peer gets a
# 403 (D-9, SC-7); a POST also needs a same-origin Origin or Referer and the
# `pa_local` cookie, like every loopback write.
#
# **Set**, not toggled: the caller states the mode it wants, so a lost response
# turning into a second click cannot land on the wrong mode.


def _acp_permission_state(config) -> dict:
    """The permission settings as the settings panel has to render them.

    `mode` is what the user chose; `state` is what a session created right now
    would actually get, read back off the derived agent on disk and compared
    with what `mode` and the rules compile to (D-15). `in_effect` is exactly
    the session gate's predicate, so the panel and the gate cannot disagree
    (SC-9). They part exactly when generation failed or the file changed, and
    `generation_error` says which.

    Pure reads, no lock: the routes call this after `apply_settings` has
    released it (D-16). Never calls `find_protected_links` (D-39); the GET
    route adds that.
    """
    state, compile_error = agent_profile.block_state_detail(config)
    last = agent_profile.last_generation()
    return {
        "mode": config.acp_permission_mode,
        "mode_warning": getattr(config, "_mode_warning", ""),
        # Phase 1 review, finding 1: config.toml did not parse, so `mode` and
        # the rules below are the defaults, not what the user set; the panel
        # shows this instead of a mode.
        "config_error": agent_profile.config_load_error_message(config),
        # Finding 7: what loading had to change in the stored rules.
        "rules_warning": getattr(config, "_rules_warning", ""),
        "base_agent": config.acp_permission_base_agent,
        "derived_agent": str(agent_profile.derived_agent_path()),
        "state": state,
        "in_effect": _permission_in_effect(state, config),
        "generation_attempted": last.attempted,
        "generation_ok": last.ok and not compile_error,
        "generation_error": compile_error or last.error,
        "generation_note": last.note,
        "floor": agent_profile.floor_display(),
        "protected": agent_profile.protected_display(
            config.acp_permission_rules),
        "posture_notice": agent_profile.posture_notice(),
    }


async def _current_acp_permission_state(*, links: bool = False) -> dict:
    """`_acp_permission_state(load_config())`, off the event loop.

    Both halves touch the filesystem — the config parse and the derived agent's
    read-back — so the settings routes run them through `asyncio.to_thread`.
    `links=True` adds `protected_links` (D-39), for the GET route only: a walk
    of `~/.kiro/{agents,steering,skills,hooks}` that must never sit on the
    session-creation path.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review (F9)
    """
    def read() -> dict:
        state = _acp_permission_state(load_config())
        if links:
            state["protected_links"] = agent_profile.find_protected_links()
        return state

    return await asyncio.to_thread(read)


@app.get("/api/acp-permissions")
async def get_acp_permissions():
    return await _current_acp_permission_state(links=True)


# How long a permission settings route waits for `agent_profile`'s generation
# lock before it answers "try again" (Phase 1 review, finding 5): a stalled
# regeneration must not hold an HTTP request, and its worker thread, forever.
_SETTINGS_LOCK_TIMEOUT_SECONDS = 5.0


def _apply_result(result: dict) -> dict:
    """The fields D-32 adds to a permission write's answer.

    Saved but not generated is `ok: true` plus a `warning`: the setting is
    stored, and new Default sessions are refused until it is in effect (D-34).
    """
    if not result["saved"]:
        return {"ok": False,
                "error": result.get("error") or "The setting was not saved."}
    answer = {"ok": True}
    if not result["generation_ok"]:
        answer["warning"] = (
            "Saved, but not yet in effect: "
            + (result["generation_error"] or "the agent file was not written")
            + ". New Default sessions are refused until this is fixed.")
    return answer


@app.post("/api/acp-permissions")
async def set_acp_permissions(request: Request):
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return {"ok": False, "error": "Invalid JSON body"}
    mode = body.get("mode") if isinstance(body, dict) else None
    # Exactly the storable modes (D-2): Auto is shown but not selectable until
    # its decider exists, and a case variant or anything else is refused
    # rather than guessed at — this is the control that decides whether a
    # shell command asks.
    if mode not in ACP_PERMISSION_MODES:
        return {"ok": False,
                "error": "mode must be \"yolo\" or \"manual\""}

    def mutate(config) -> None:
        # The instance `load_config` returned inside the lock, so unknown
        # top-level keys riding on `_extra` survive the save.
        config.acp_permission_mode = mode

    # `sets_posture`: choosing the mode here is the dashboard's answer to
    # D-35's notice, even when the mode is unchanged.
    result = await asyncio.to_thread(
        lambda: agent_profile.apply_settings(
            mutate, lock_timeout=_SETTINGS_LOCK_TIMEOUT_SECONDS,
            sets_posture=True))
    answer = _apply_result(result)
    if not answer["ok"]:
        return answer
    return {**answer, **(await _current_acp_permission_state())}


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
    from .data import _normalize_path
    config = load_config()
    norm_folder = _normalize_path(folder)
    if not any(_normalize_path(f) == norm_folder for f in config.pinned_folders):
        config.pinned_folders.append(folder)
        save_config(config)
    return {"ok": True}


@app.post("/api/unpin-folder")
async def unpin_folder(request: Request):
    body = await request.json()
    folder = body["folder"]
    from .data import _normalize_path
    config = load_config()
    norm_folder = _normalize_path(folder)
    original_len = len(config.pinned_folders)
    config.pinned_folders = [f for f in config.pinned_folders
                              if _normalize_path(f) != norm_folder]
    if len(config.pinned_folders) < original_len:
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
        # Whether the loopback credential's key survives a restart (D-22). The
        # status only, never the secret.
        # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
        "local_secret": local_secret_status(),
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
    # The kiro-cli agent the derived ACP agent is built from. A name, never a
    # path (D-8), validated on the write path below by
    # `agent_profile.validate_base_agent_name` — which rejects before any path
    # is built, so `../x` never reaches `Path`.
    # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 1
    "acp_permission_base_agent": str,
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
    "acp_prompt_silence_seconds": (60, 86400),
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
    load_error = agent_profile.config_load_error_message(config)
    if load_error:
        # Phase 1 review, finding 1. `config` is the defaults stand-in for a
        # config.toml that did not parse; saving it would write every default
        # over the user's file, Yolo included, for any key.
        return {"ok": False, "error": load_error + "."}
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
    if key == "acp_permission_base_agent":
        # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 1.
        # The named error on the write path, before the value is persisted, for
        # the same reason `remote_bind_address` has one: the alternative is a
        # config.toml that stores a name generation can never resolve, with the
        # reason visible only in the log.
        value = value.strip()
        try:
            value = agent_profile.validate_base_agent_name(value)
        except agent_profile.AgentProfileError:
            return {"ok": False,
                    "error": "Base agent must be 1-64 characters of "
                             "letters, digits, '_' or '-', and not a Windows "
                             "reserved device name"}
    if key == "acp_permission_base_agent":
        # Saved and regenerated under the generation lock, like a mode change
        # (260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-33, D-16): the
        # derived agent is built from this file, and a save outside the lock
        # would let a session check see the new name before the file.
        def mutate(locked_config) -> None:
            locked_config.acp_permission_base_agent = value

        result = await asyncio.to_thread(
            lambda: agent_profile.apply_settings(
                mutate, lock_timeout=_SETTINGS_LOCK_TIMEOUT_SECONDS))
        answer = _apply_result(result)
        if not answer["ok"]:
            return answer
        # The same generation-outcome fields `POST /api/acp-permissions`
        # returns, for the same reason: a bare `{"ok": True}` reported
        # unqualified success for a rename whose regeneration had failed.
        return {**answer, "restart_required": key in _RESTART_TO_APPLY,
                **(await _current_acp_permission_state())}
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

    `no-store` because this body carries the **permanent** device secret.
    (`/acp` once set it for the per-launch page token, retired by
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 6; this route's
    reason never depended on that one.) Nothing fetches this route yet,
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


async def _close_loopback_acp_sockets() -> None:
    """Close every loopback ``/ws/acp`` socket with 1008 after a rotation.

    The gate checks ``pa_local`` only at the handshake, so without this a tab
    opened under the old key kept a live socket — a leaked cookie's socket
    included — and the rotate route's "signed out" was untrue for it.

    **All** loopback sockets, the caller's included. The caller's own socket
    cannot be told apart by anything the server holds: its cookie value is
    exactly what a copied, leaked cookie would carry, so keeping sockets that
    match it would keep the leak's socket too. The caller's page loses nothing
    it cannot recover: its close handler reconnects after a second, with the
    fresh cookie this route's response set, while every other tab's reconnect
    is refused and lands on the signed-out message. Remote sockets are not
    touched; they hold ``pa_device``, which a local rotation does not change.
    Runs as a background task, after the response (and so the new cookie) has
    been sent.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review (F3)
    """
    if acp is None:
        return

    def loopback(scope) -> bool:
        return not _is_remote_peer((scope.get("client") or (None,))[0])

    try:
        await acp.close_connections(
            loopback, 1008, "signed out: the local key was rotated")
    except Exception:
        log.exception("closing loopback /ws/acp sockets after a local "
                      "secret rotation failed")


@app.post("/api/local-secret/rotate")
async def api_local_secret_rotate(request: Request, response: Response,
                                  background_tasks: BackgroundTasks):
    """Issue a new local secret, signing every loopback browser out but this one.

    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4. Rotation
    invalidates every loopback cookie, the caller's included, so the caller's
    replacement is set **in this same response** — otherwise the rotate
    request would sign the user out of the surface they used to make it.

    The replacement goes only to a caller that proved the *old* credential.
    Without that check this route would hand a fresh cookie to any loopback
    process that asked, which is the self-service mint D-3 rejects; with it,
    rotation can only ever re-issue a credential its caller already held.

    Same write-then-apply ordering as `api_remote_access_rotate`, for the same
    reason; a failed write changes nothing.

    And, like it, the write runs **synchronously on the loop**, with no
    `await` between writing the file and applying the secret. Through
    `asyncio.to_thread`, two concurrent rotations could interleave as "write A,
    write B, apply B, apply A", leaving the process honouring A while the file
    holds B — every cookie then dies at the next restart. One small file write
    and `fsync`, on a user-initiated action, is the price.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4 review
    """
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    if not _local_cookie_ok(request.scope):
        return JSONResponse(
            {"ok": False, "error": "Rotating the local secret requires a "
                                   "signed-in browser"},
            status_code=403, headers={"Cache-Control": "no-store"})
    secret = rotate_local_secret()
    if not secret:
        return {"ok": False,
                "error": f"Could not write {local_secret_status()['path']}; "
                         "the previous local secret is still in effect"}
    set_local_secret(secret)
    # Codes minted under the old secret would otherwise still exchange for a
    # cookie; clearing them makes "rotate" mean every old way in is closed.
    # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4
    #
    # Known race, accepted: a code a door minted moments before the rotation
    # is cleared too, so a browser still opening from that door lands on the
    # "expired or already used" page and has to be reopened from the tray.
    # Accepted in favour of the security property — after a rotation no way in
    # issued before it remains.
    # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4 review
    with _login_codes_lock:
        _login_codes.clear()
    reissued = _set_local_cookie(response)
    # Open sockets outlive the key they were admitted under; close them.
    # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review (F3)
    background_tasks.add_task(_close_loopback_acp_sockets)
    log.warning("local secret rotated; every other loopback browser must "
                "sign in again from the tray")
    return {"ok": True, "reissued": reissued,
            "message": "Every other browser has been signed out and must be "
                       "reopened from the tray."}


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


@app.post(_ACP_RESTART_PATH)
async def api_restart(response: Response):
    """Trigger a PowerAtlas restart from /acp, including from a remote device.

    Calls the same restart path as the tray menu's Restart item — sets the
    restart flag, fires the shutdown event and stops the icon — so the process
    exits cleanly and ``__main__`` relaunches it.

    Admitted to remote peers via ``_REMOTE_ALLOWED_PATHS``, authenticated by the
    device cookie (the same boundary as ``_ACP_DELETE_PATH``).  POST so that
    ``same_origin_guard``'s Origin/Referer check applies; the client-side button
    shows a ``confirm()`` dialog before sending, but the transport-level check
    is the durable boundary.

    Returns ``{"ok": true}`` synchronously and then fires the shutdown; the
    caller should expect the connection to drop rather than waiting for further
    JSON.  Returns 503 if no restart callback is wired (no tray — test or
    headless environment).
    """
    response.headers["Cache-Control"] = "no-store"
    if _restart_callback is None:
        return JSONResponse(
            {"ok": False, "error": "no restart callback registered"},
            status_code=503,
        )
    log.warning("restart requested via /api/restart")
    # Fire after we have returned the response body so the caller receives the
    # `ok` before the connection drops.
    asyncio.get_event_loop().call_soon(_restart_callback)
    return {"ok": True}


@app.get("/api/session-transcript")
async def api_session_transcript(sid: str = "", provider: str = "kiro-cli-v3", cwd: str = ""):
    """Full transcript for the dashboard's static transcript panel (Phase 2).

    Returns `{"events": [...]}` in the same shape as an ACP `history` frame's
    event array (`acp.py`'s `_handle_subscribe`) — a client-side renderer
    shared with the live ACP page (Phase 2) can feed both through one
    dispatcher without knowing which produced them. Read once per panel-open
    (not polled), so this does no caching of its own beyond whatever
    `data.get_full_transcript`'s provider adapter already does.
    """
    if not re.fullmatch(r'(?:sess_)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', sid):
        return JSONResponse({"error": "invalid session id"}, status_code=400)
    from . import transcript_translator

    events = await asyncio.to_thread(data.get_full_transcript, sid, provider, cwd)
    frames = transcript_translator.translate_transcript(events, sid)
    return {"events": frames}


@app.get("/api/session-availability")
async def api_session_availability(response: Response, sid: str = "", cwd: str = ""):
    """Cheap held/locked/available peek for ONE session, for the dashboard's
    transcript panel to decide the composer's state without attaching.

    Reuses `_acp_availability` exactly as `_acp_listing`/`api_acp_sessions`
    do for the rail — zero ACP round-trip, same fail-open-to-`available`
    contract, same `acp is None` degradation. Only ever meaningful for
    kiro-cli-v3 (and, incidentally, retired-v2 kiro-cli — `_acp_availability`
    branches on id shape) sessions; the panel should not call this for any
    other provider, since they have no live-attach concept to be available
    *for*.

    `status` mirrors `_acp_status_for_held`'s own rule: `""` for a session
    this PowerAtlas does not hold (a `locked`/`available` id has no local
    transcript-tail reading worth doing), the resolved working/waiting/
    errored verdict when it does. Requires `cwd` to compute a status for a
    held session; without it the availability state alone is still returned.

    `no-store`, same reasoning as `api_acp_sessions`: availability is a
    liveness reading with a lifetime of seconds, not something to cache.
    """
    response.headers["Cache-Control"] = "no-store"
    if not re.fullmatch(r'(?:sess_)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', sid):
        return JSONResponse({"error": "invalid session id"}, status_code=400)

    def _compute() -> tuple[str, str]:
        supervisor = getattr(acp, "_supervisor", None) if acp is not None else None
        held = frozenset(supervisor.sessions) if supervisor is not None else frozenset()
        availability = _acp_availability([sid], held)
        state = availability.get(sid, "available")
        status = ""
        if state == "held" and cwd:
            try:
                snapshot = presence.get_snapshot()
                semantic = get_semantic_status(sid, _ACP_V3_LISTING_PROVIDER, cwd)
                status = _resolved_session_status(snapshot, _ACP_V3_LISTING_PROVIDER, sid, semantic)
            except Exception:
                log.exception("session-availability: could not settle status for %s", sid)
                status = "working"
        return state, status

    state, status = await asyncio.to_thread(_compute)
    return {"sid": sid, "availability": state, "status": status}


@app.post("/api/launch", response_class=HTMLResponse)
async def api_launch(request: Request):
    body = await request.json()
    config = load_config()
    provider = body.get("provider") or "kiro-cli-v3"
    cwd = _resolve_launch_cwd(body.get("workspace", ""), config, provider)
    default_args = config.provider_settings.get(provider, {}).get("default_args", "")
    session_id = body.get("session_id")
    session_title = ""
    if session_id:
        sessions = data.get_sessions(cwd, provider)
        matched = next((s for s in sessions if s.session_id == session_id), None)
        if matched:
            session_title = matched.title
    result = launcher.launch_session(
        cwd=cwd,
        session_id=session_id,
        provider=provider,
        default_args=default_args,
        launch_profile=get_active_launch_profile(config),
        session_title=session_title,
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
            s["workspace"] = _resolve_launch_cwd("", config, s.get("provider", "kiro-cli-v3"))
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
    provider = body.get("provider") or "kiro-cli-v3"
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
    await asyncio.to_thread(icons.extract_icon, entry["id"], entry["command"], entry["terminal"])
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
            await asyncio.to_thread(icons.extract_icon, lid, entry.get("command", ""), entry.get("terminal", True))
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

    # Handle provider launcher icons. Cached for an hour: a session row now
    # requests this per provider on every rail render (dashboard/ACP-merge),
    # and for a provider whose binary yields no extractable icon (kiro-cli,
    # kiro-cli-v3 today) every uncached request re-ran the full resolve +
    # extract_icon attempt, which is real wall-clock cost multiplied by every
    # row of that provider on every refresh.
    if launcher_id.startswith("provider--"):
        provider_key = launcher_id[len("provider--"):]
        cache_headers = {"Cache-Control": "public, max-age=3600"}
        if icons.has_icon(launcher_id):
            return FileResponse(icons.icon_path(launcher_id), media_type="image/png", headers=cache_headers)
        binary = launcher._PROVIDER_BINARY.get(provider_key, provider_key)
        await asyncio.to_thread(icons.extract_icon, launcher_id, binary, True)
        if icons.has_icon(launcher_id):
            return FileResponse(icons.icon_path(launcher_id), media_type="image/png", headers=cache_headers)
        config = load_config()
        color = _get_provider_color(provider_key, config)
        svg = icons.default_icon_svg(True, color)
        return Response(content=svg, media_type="image/svg+xml", headers=cache_headers)

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
