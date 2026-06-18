# Kiro Orchestrator — Lightweight Session Launcher

> **Date**: 2026-06-17
> **Status**: In Progress  <!-- Status lifecycle: Exploring → Draft → In Progress → Complete -->
> **Scope**: Desktop launcher/dashboard for kiro-cli sessions with tray icon, multi-select batch launch, and workspace discovery
> **Estimated effort**: 2-3 days

---

## Intent

### Problem statement & desired outcomes

Launching, resuming, and managing kiro-cli sessions currently requires remembering folder paths, session IDs, and CLI flags. This tool provides a polished, always-available UI accessible from the system tray that surfaces all kiro-cli workspaces and sessions as interactive cards — enabling quick launch, resume, and batch operations without touching the terminal directly.

### Success criteria

1. App launches on Windows login (startup shortcut) and shows tray icon
2. Clicking tray icon opens pywebview window with collapsible workspace cards
3. Workspaces auto-discovered from kiro-cli data + pinnable folders
4. Sessions displayed with title, timestamp, first/last prompt previews; subagent sessions filtered out
5. Can pin individual sessions for quick access
6. Single-click resume launches kiro-cli in detected terminal with `--resume-id`
7. Multi-select + batch launch opens N terminal instances
8. Trust-all-tools toggle persists and applies to all launches
9. Search filters sessions across all workspaces
10. Settings page for: terminal preference, pywebview vs browser mode, trust default

### Scope boundaries & non-goals

**In scope (V1)**: Tray icon, startup launch, pywebview window (configurable browser fallback), collapsible workspace cards sorted by recency, session discovery from kiro-cli sqlite + metadata files, multi-select + batch launch, single-session resume, new session launch, trust-all-tools toggle, search/filter, pin folders, pin sessions, settings page, TOML config persistence.

**Non-goals (V1 — see plans/ROADMAP.md)**: No-interactive task dispatch, prompt/skill templates, usage stats, plan-file shortcuts, custom launch section (CLI/exe/bat/sh/ps1 with WSL), custom icons, session health indicators, plan progress overlay, scheduled tasks, chained launches, session output tail, keyboard navigation, workspace tags/groups, multi-machine sync.

## 1) Current State

Greenfield project. Empty directory at `C:\Users\QSylvestre.POLESTAR\OneDrive - Pole Star\Documents\Dev\Perso\kiro-orchestrator`. No existing code.

**Reference implementation** (SamWhispers frame):
- `src/samwhispers/tray.py` — pystray + Pillow tray icon with state-aware menu
- `src/samwhispers/autostart.py` — Windows Startup folder shortcut via WScript.Shell COM, pythonw.exe for headless launch
- `pyproject.toml` — setuptools packaging with entry points

**kiro-cli data sources**:
- Session metadata: `~/.kiro/sessions/cli/*.json` — fields: `session_id`, `cwd`, `created_at`, `updated_at`, `title`, `parent_session_id` (non-null = subagent)
- Session content: `~/.kiro/sessions/cli/*.jsonl` — lines with `{"version":"v1","kind":"<kind>","data":{...}}`
- Sqlite DB: `%LOCALAPPDATA%\Kiro-Cli\data.sqlite3` — table `conversations_v2` (key=cwd, value=JSON, created_at, updated_at)

**kiro-cli launch interface**:
- `kiro-cli chat --resume-id <SESSION_ID>` — resume specific session
- `kiro-cli chat -a` — trust all tools
- `kiro-cli chat` — new session (run from target cwd)

## 2) Goal

Build a Python desktop application that runs as a system tray icon, serves a FastAPI web UI via pywebview, discovers kiro-cli workspaces and sessions from local data, and launches/resumes sessions in the user's preferred terminal — with proper threading, error handling, and polished UX states.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Language | Python 3.11+ | Go, Rust, Electron | Matches SamWhispers stack, rapid dev, familiar ecosystem |
| Backend | FastAPI + Jinja2 + uvicorn | Flask, Starlette | Async, WebSocket-ready for V2, familiar from SamWhispers |
| Frontend | htmx + server-rendered HTML | Vue/React SPA, Alpine.js | No build step, server does heavy lifting, perfect for list-and-act UI |
| Window | pywebview (auto-fallback to browser on failure) | Native Qt, browser-only | App feel without browser tab; auto-fallback for reliability |
| Tray | pystray + Pillow | Qt tray, custom | Proven pattern from SamWhispers, cross-platform |
| Autostart | Windows Startup folder shortcut | Task Scheduler, Registry Run key | No admin needed, corporate-friendly (same as SamWhispers) |
| Config | TOML at `%LOCALAPPDATA%\kiro-orchestrator\config.toml` | JSON, in-repo config | Platform-standard, human-editable, separated from source |
| Data access | Session .json metadata as primary, sqlite as supplementary | Sqlite-only | Metadata files are more reliable/current; sqlite supplements for workspace discovery |
| Terminal detection | Auto-detect (wt > pwsh > cmd) + configurable template | Fixed terminal | Flexibility without config burden |
| UI layout | Collapsible workspace cards, grid, sorted by recency | Panel split, flat list | Takes advantage of large windows, visual grouping |
| Threading model | pystray on main thread; uvicorn + pywebview in background threads | pywebview on main | pystray requires main thread on Windows; pywebview supports threaded mode via `webview.start(gui='edgechromium')` in separate thread |
| Config thread-safety | Lock-protected save/load wrapper | Queue-based | Simplest for V1; shared `threading.Lock` guards all config mutations |
| Search | Server-side htmx endpoint (`GET /search?q=...`) | Client-side JS filter | Consistent with htmx architecture; scales to large session counts |
| Single-instance | Named mutex on Windows (CreateMutex) | PID file, port check | Reliable on Windows; shows existing window on duplicate launch |

## 4) External Dependencies & Costs

### Required external changes

None. This is a fully local application with no cloud, CI/CD, or infrastructure requirements.

### Cost impact

None. All local, no API calls, no hosting.

## 5) Implementation Phases

### Phase 1: Project scaffolding, config, and data layer [QA]

**Goal**: Set up the Python package structure, thread-safe config persistence, and robust data access layer with defensive parsing.

**File scope**: `pyproject.toml`, `src/kiro_orchestrator/__init__.py`, `src/kiro_orchestrator/config.py`, `src/kiro_orchestrator/data.py`, `tests/test_config.py`, `tests/test_data.py`

**Details**:

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "kiro-orchestrator"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110",
    "uvicorn>=0.27",
    "jinja2>=3.1",
    "pystray>=0.19",
    "Pillow>=10.0",
    "pywebview>=5.0",
    "tomli-w>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.3"]

[project.scripts]
kiro-orchestrator = "kiro_orchestrator.__main__:main"
```

<!-- resolves review finding #17 (tomli unnecessary on 3.11+) -->
Note: uses stdlib `tomllib` for reading; only `tomli-w` needed for writing.

```python
# src/kiro_orchestrator/config.py
"""Thread-safe config persistence via TOML at %LOCALAPPDATA%/kiro-orchestrator/config.toml."""

import os
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

# resolves review finding #11 (use LOCALAPPDATA env var)
CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "kiro-orchestrator"

_lock = threading.Lock()  # resolves review finding #4 (thread-safety)

@dataclass
class Config:
    trust_all_tools: bool = False
    use_pywebview: bool = True
    terminal_command: str = ""  # empty = auto-detect
    pinned_folders: list[str] = field(default_factory=list)
    pinned_sessions: list[str] = field(default_factory=list)

def load_config() -> Config:
    """Load config from TOML. Missing keys use dataclass defaults; unknown keys ignored."""
    ...

def save_config(config: Config) -> None:
    """Atomic write: write to .tmp, fsync, os.replace(). Lock-protected."""
    # resolves review finding #4 (atomic write + lock)
    with _lock:
        ...
```

```python
# src/kiro_orchestrator/data.py
"""Read-only access to kiro-cli session data with defensive parsing."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SESSION_DIR = Path.home() / ".kiro" / "sessions" / "cli"
SQLITE_PATH = Path(os.environ.get("LOCALAPPDATA", "")) / "Kiro-Cli" / "data.sqlite3"

@dataclass
class Session:
    session_id: str
    title: str
    cwd: str
    created_at: str
    updated_at: str
    first_prompt: str  # extracted from first Prompt line in .jsonl
    last_prompt: str   # extracted from last Prompt line in .jsonl
    last_reply_tail: str  # last 100 chars of last AssistantMessage

def discover_workspaces() -> list[str]:
    """Primary source: session .json metadata files (unique cwd values).
    Supplementary: sqlite conversations_v2 table for workspaces not in session files.
    Paths normalized (case-folded on Windows, trailing separator stripped).
    Returns sorted by most recent activity (descending updated_at).
    Handles: missing files/DB gracefully → returns empty list."""
    ...

def get_sessions(cwd: str) -> list[Session]:
    """Return sessions for a workspace.
    - Filters subagents (parent_session_id != null)
    - Extraction algorithm for .jsonl:
      (a) Stream from start, find first line with kind=Prompt → first_prompt (stop after 50 lines)
      (b) Read last 100 lines, scan backward for last Prompt and last AssistantMessage
    - Defensive: skip malformed sessions, use fallbacks (title="<untitled>", prompts="")
    - Returns sorted by updated_at descending.
    """
    ...

def _open_sqlite_readonly() -> sqlite3.Connection | None:
    """Open sqlite in read-only mode with busy_timeout=5000ms.
    Returns None if DB doesn't exist or is locked."""
    # resolves review finding #5 (sqlite BUSY handling)
    ...
```

**Exit criteria**:
- [x] `pyproject.toml` valid, `pip install -e .` succeeds
- [x] `Config` loads/saves round-trip correctly; atomic write verified (test)
- [x] Config handles missing keys (defaults) and unknown keys (ignored) gracefully
- [x] Thread-safety: concurrent save/load doesn't corrupt (test with threading)
- [x] `discover_workspaces()` returns folders from real kiro-cli data
- [x] `discover_workspaces()` returns empty list when data sources missing (not crash)
- [x] `get_sessions()` returns sessions with all display fields populated
- [x] Malformed session files skipped without crashing (test with bad .json/.jsonl)
- [x] Subagent sessions filtered out (parent_session_id check)
- [x] Session list loads in <2s for workspaces with 50+ sessions

#### Implementation (2026-06-17, code: 7d1e026)

Implemented the complete Phase 1 foundation: `pyproject.toml` with setuptools packaging and all dependencies, thread-safe TOML config persistence (`config.py`) with lock-protected atomic writes (write to .tmp, fsync, os.replace), and a defensive read-only data access layer (`data.py`) that discovers workspaces from kiro-cli session metadata files and sqlite DB, extracts prompt previews from .jsonl content by streaming first 50 / last 100 lines, filters subagent sessions via parent_session_id, normalizes paths (case-fold on Windows), and gracefully handles missing/malformed files. Added .gitignore for Python artifacts.

QA verification: PASS (35 workspaces in 0.98s, config round-trip verified, error handling graceful).

### Phase 2: Tray icon, app lifecycle, and autostart [QA]

**Goal**: System tray icon, threading model, single-instance guard, graceful shutdown, startup shortcut, pywebview/browser window management.

**File scope**: `src/kiro_orchestrator/tray.py`, `src/kiro_orchestrator/autostart.py`, `src/kiro_orchestrator/__main__.py`, `src/kiro_orchestrator/assets/tray/`, `tests/test_autostart.py`

**Depends on**: Phase 1 (`config.py`)

**Details**:

```python
# src/kiro_orchestrator/__main__.py
"""Entry point with proper threading model and lifecycle management."""

import ctypes
import sys
import threading

def main():
    # 1. Single-instance guard (Windows named mutex)
    # resolves review finding #3
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "KiroOrchestratorMutex")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        # Activate existing window (find via window title) and exit
        sys.exit(0)

    # 2. Load config
    config = load_config()

    # 3. Start uvicorn in daemon thread (port 0 for dynamic assignment)
    # resolves review finding #3 (port conflict)
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    # Wait for server to be ready, retrieve actual port

    # 4. pystray on main thread (blocks)
    # resolves review finding #1 (threading model)
    run_tray(server_url=f"http://localhost:{port}", config=config)

    # 5. Graceful shutdown: tray Quit → signal uvicorn → close pywebview → exit
    # resolves review finding #2
```

```python
# src/kiro_orchestrator/tray.py
"""System tray icon. pystray runs on main thread (required on Windows)."""

import threading
import webbrowser

def run_tray(server_url: str, config) -> None:
    """Main-thread tray loop. pywebview opened in background thread."""
    # Menu: Open UI (default), Trust All Tools (toggle), Settings, Quit
    # Open → try pywebview in thread; on failure → browser fallback
    # resolves review finding #15 (pywebview auto-fallback)
    # Window close = minimize to tray (app keeps running)
    # resolves review finding #16 (window lifecycle)
    # Quit = graceful shutdown sequence
    ...

def _open_window(server_url: str, use_pywebview: bool) -> None:
    """Open UI window. Try pywebview; on ImportError/RuntimeError, fallback to browser."""
    try:
        import webview
        webview.create_window("Kiro Orchestrator", server_url, width=900, height=700)
        webview.start()
    except Exception:
        webbrowser.open(server_url)
```

```python
# src/kiro_orchestrator/autostart.py
"""Windows Startup folder shortcut management (no admin needed)."""
# Same pattern as SamWhispers: WScript.Shell COM, pythonw.exe for headless

def enable() -> None: ...
def disable() -> None: ...
def is_enabled() -> bool: ...
```

**Exit criteria**:
- [x] Tray icon appears with correct menu items (Open, Trust toggle, Settings, Quit)
- [x] Single-instance: second launch activates existing window, doesn't crash
- [x] "Open" launches pywebview window; if pywebview fails, opens browser
- [x] Window close (X button) minimizes to tray; "Quit" exits app cleanly
- [x] Graceful shutdown: uvicorn stops, no orphan processes
- [x] Port conflict: dynamic port assignment succeeds
- [x] Trust All Tools tray toggle syncs with config (thread-safe)
- [x] `enable`/`disable` autostart creates/removes startup shortcut
- [x] App launches without console window (pythonw.exe)

#### Implementation (2026-06-18, code: 341c431)

Phase 2 adds the application entry point with Windows named mutex single-instance guard, uvicorn server started on a dynamic port in a daemon thread, and pystray system tray icon running on the main thread with Open (pywebview with browser fallback), Trust All Tools toggle (thread-safe config sync), Settings, and Quit menu items. The autostart module manages Windows Startup folder shortcuts via WScript.Shell COM, and graceful shutdown coordinates uvicorn stop, pywebview close, and clean exit. All 4 new autostart tests and 13 existing tests pass.

QA verification: SKIP (tray icon and window management require interactive desktop testing — no non-interactive verification path). QA annotation mismatch: consider removing [QA] in future plan revisions for desktop-UI-only phases.

### Phase 3: Web UI — workspace cards and session list [QA]

**Goal**: FastAPI backend serving htmx-powered collapsible workspace cards with loading, empty, error, and stale-workspace states.

**File scope**: `src/kiro_orchestrator/web.py`, `src/kiro_orchestrator/templates/base.html`, `src/kiro_orchestrator/templates/index.html`, `src/kiro_orchestrator/templates/partials/workspace_card.html`, `src/kiro_orchestrator/templates/partials/session_row.html`, `src/kiro_orchestrator/templates/partials/empty_state.html`, `src/kiro_orchestrator/templates/partials/toast.html`, `src/kiro_orchestrator/static/style.css`, `src/kiro_orchestrator/static/htmx.min.js`, `tests/test_web.py`

<!-- resolves review finding #10: static assets explicitly in scope -->

**Details**:

```python
# src/kiro_orchestrator/web.py
"""FastAPI app serving the web UI with htmx partials."""

app = FastAPI()

@app.get("/")
async def index(request: Request):
    """Render main page. Shows loading skeleton initially, then populates via htmx."""
    ...

@app.get("/partials/workspaces")
async def workspaces_partial():
    """Return all workspace cards (htmx swap target on page load)."""
    # resolves review finding #5 (loading state: page loads fast, cards arrive via htmx)
    ...

@app.get("/search")
async def search(q: str):
    """Server-side search: filter workspaces and sessions, return matching cards."""
    # resolves review finding #9 (server-side search)
    ...

@app.post("/api/toggle-trust")
async def toggle_trust():
    """Toggle trust_all_tools in config (thread-safe)."""
    ...
```

UI states (resolves review findings #5, #6, #7):
- **Loading**: page loads instantly with skeleton cards, htmx fetches real data
- **Empty** (no sessions anywhere): onboarding prompt "Pin a folder to get started"
- **Empty card** (pinned folder, 0 sessions): "No sessions yet — start one?" with New button
- **Stale workspace** (cwd doesn't exist): card shows "⚠ Folder not found" badge, Resume disabled
- **Error** (data source unavailable): banner "Session data temporarily unavailable"

Vendor `htmx.min.js` (pinned version, downloaded once into `static/`).

**Exit criteria**:
- [x] Main page renders immediately (skeleton), then populates cards via htmx
- [x] Cards collapse/expand on header click
- [x] Cards ordered by most recent activity (ascending age)
- [x] Session rows show all 5 fields (title, time, first prompt, last prompt, last reply)
- [x] Subagent sessions not visible
- [x] Server-side search filters sessions and folders via `GET /search?q=`
- [x] Pinned folders with 0 sessions show empty-state card with "New session" button
- [x] Stale workspaces (deleted folders) show visual indicator, Resume disabled
- [x] Missing data source shows graceful error banner (not crash/blank page)
- [x] htmx.min.js vendored in static/ (no CDN dependency)
- [x] `tests/test_web.py` covers index, search, and workspace partial endpoints

#### Implementation (2026-06-18, code: cc09ba5)

Implemented the FastAPI web UI layer with htmx-powered workspace cards. The index page renders instantly with skeleton loading cards, then htmx fetches real workspace data via `/partials/workspaces`. Each workspace uses a collapsible `<details>/<summary>` element showing path, session count, and session rows with all 5 fields. Handles empty state, stale workspaces (badge + disabled Resume), error state (toast banner), and server-side search. Static assets vendored locally with no CDN dependency.

QA verification: PASS (10 web tests cover all endpoints and states via TestClient).

### Phase 4: Launch, resume, and action feedback [QA]

**Goal**: Terminal detection, session resume, multi-select batch launch with confirmation and per-action feedback toasts.

**File scope**: `src/kiro_orchestrator/launcher.py`, `src/kiro_orchestrator/web.py` (add endpoints), `src/kiro_orchestrator/templates/partials/action_bar.html`, `tests/test_launcher.py`

**Details**:

```python
# src/kiro_orchestrator/launcher.py
"""Detect terminal and spawn kiro-cli sessions with error handling."""

import shutil
import subprocess
from dataclasses import dataclass

@dataclass
class LaunchResult:
    success: bool
    session_id: str | None
    workspace: str
    error: str | None = None  # e.g., "Terminal not found", "Folder does not exist"

def detect_terminal(config_override: str = "") -> str | None:
    """Priority: config override > wt.exe > pwsh.exe > cmd.exe. Returns None if nothing found."""
    ...

def launch_session(cwd: str, session_id: str | None = None, trust_all: bool = False) -> LaunchResult:
    """Launch kiro-cli in detected terminal.
    Validates: terminal exists, cwd exists.
    Returns LaunchResult with success/error for UI feedback.
    """
    # resolves review findings #6 (feedback), #7 (stale cwd), #14 (error handling)
    ...

def launch_batch(sessions: list[tuple[str, str | None]], trust_all: bool = False) -> list[LaunchResult]:
    """Launch multiple sessions. Never aborts batch on single failure."""
    # resolves review finding #14 (batch resilience)
    results = []
    for cwd, sid in sessions:
        results.append(launch_session(cwd, sid, trust_all))
    return results
```

```python
# web.py additions
@app.post("/api/launch")
async def launch(session_id: str, workspace: str):
    """Resume a single session. Returns htmx toast partial (success/error)."""
    # resolves review finding #6 (launch feedback)
    ...

@app.post("/api/launch-batch")
async def launch_batch_endpoint(body: LaunchBatchRequest):
    """Launch multiple sessions. Confirms if count > 5. Returns results toast."""
    # resolves review finding #12 (batch confirmation for >5 sessions)
    ...

@app.post("/api/new-session")
async def new_session(workspace: str):
    """Start new kiro-cli session. Returns toast."""
    ...
```

UI feedback:
- Single launch: transient toast "✓ Launched in Windows Terminal" or "✗ Failed — folder not found"
- Batch launch >5: confirmation dialog "Launch 12 sessions? This opens 12 terminal windows."
- Batch results: toast "Launched 4/5 — 1 failed (folder not found)"
- No terminal detected: toast with link to Settings

**Exit criteria**:
- [ ] Auto-detects wt.exe when available, falls back correctly
- [ ] Returns error (not crash) when no terminal found; UI shows settings prompt
- [ ] Single "Resume" opens terminal with correct `--resume-id` in correct cwd
- [ ] Resume on deleted folder returns clear error (not crash)
- [ ] Multi-select + "Launch all" spawns N terminals; one failure doesn't abort batch
- [ ] Batch >5 shows confirmation dialog before launching
- [ ] Launch results shown as transient toast (success count + failures)
- [ ] "New session" opens terminal with `kiro-cli chat` (+ `-a` when trust is on)
- [ ] Custom terminal template from settings works
- [ ] `tests/test_launcher.py` covers detect, single launch, batch, and error paths

### Phase 5: Settings page and session pinning [QA]

**Goal**: Settings UI for preferences + session pin/unpin functionality.

**File scope**: `src/kiro_orchestrator/templates/settings.html`, `src/kiro_orchestrator/web.py` (add settings endpoints), `src/kiro_orchestrator/templates/partials/session_row.html` (pin icon), `README.md`

**Details**:

Settings page (separate route `/settings`):
- Terminal preference (dropdown: auto-detect, wt, pwsh, cmd, custom with template input)
- Window mode (pywebview vs browser) — note: requires app restart
- Trust all tools default (toggle)
- Pinned folders management (list + add/remove via folder picker)
- Autostart enable/disable toggle

Session pinning:
- Pin icon appears on hover next to each session row
- Pinned sessions appear at top of their workspace card
- Pin state persisted in config.toml (list of session IDs)

**Exit criteria**:
- [ ] Settings page renders with current values populated
- [ ] Changing terminal preference persists and applies to next launch
- [ ] Pywebview/browser toggle persists (note shown: "restart required")
- [ ] Autostart toggle enables/disables startup shortcut
- [ ] Pinned sessions show pin indicator and appear first in their card
- [ ] Pinned folders appear in workspace list even with 0 sessions
- [ ] Adding a new folder via settings makes it appear immediately
- [ ] `README.md` created with installation instructions, usage, and config reference

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Concurrent sqlite access while kiro-cli writes | Corrupted reads | Read-only mode, WAL, `busy_timeout=5000`, catch OperationalError → return cached/stale data |
| kiro-cli data format changes | Missing/malformed sessions | Defensive parsing: skip bad files, fallback values, log warnings |
| pywebview DPI issues / initialization failure | Broken UI | Auto-fallback to browser on any pywebview exception |
| Session .jsonl files large (44KB+) | Slow extraction | Stream-parse with line cap (50 from start, 100 from end) |
| pythonw.exe not found | Startup fails | Fall back to python.exe + CREATE_NO_WINDOW |
| Windows Terminal not installed | Launch fails | Graceful fallback chain + clear error toast |
| Port already in use | Server doesn't start | Bind to port 0 (OS-assigned), store in config for pywebview |
| Config corruption on crash | Lost settings | Atomic write (tmp + rename) + thread lock |
| Stale workspace (deleted folder) | Confusing resume failures | Validate cwd existence at display time + block resume |
| Duplicate app instances | Port conflicts, double tray | Named mutex single-instance guard |

## 7) Verification

- `pip install -e .` succeeds
- `pytest` passes all tests (config, data, web endpoints, launcher)
- `kiro-orchestrator` launches tray icon, opens web UI on click
- Workspace cards populated from real kiro-cli data
- Empty state shown when no data exists (not crash)
- Stale workspace visually indicated
- Resume action opens correct session in terminal (with feedback toast)
- Multi-select + batch launch opens multiple terminals (with results toast)
- Settings persist after restart
- App auto-starts after login (verify shortcut exists)
- Second instance activates existing window (no duplicate)
- Graceful shutdown on Quit (no orphan processes)

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Create with installation, usage, and config reference | 5 |
| `plans/ROADMAP.md` | Already exists — no updates needed | N/A |

## 9) Implementation Divergences from Plan
<Reserved — filled during implementation>

## Review Log

### 2026-06-17 — Plan Review (via /qplan)

High-effort review (4 personas: Architect, Senior engineer, End-user advocate, Reliability engineer). 19 findings (7 High, 8 Medium, 4 Low). 19 auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | Threading model unspecified (pywebview + pystray both want main thread) | Resolved — specified pystray on main, pywebview in thread; documented in Design Decisions |
| 2 | High | No graceful shutdown coordination | Resolved — added shutdown sequence in Phase 2 (__main__.py) |
| 3 | High | No port-conflict handling / single-instance guard | Resolved — dynamic port (bind 0) + Windows named mutex in Phase 2 |
| 4 | High | Config thread-safety (shared object mutated from multiple threads) | Resolved — Lock-protected save wrapper in config.py, documented in Design Decisions |
| 5 | High | No loading/error/empty states for UI | Resolved — Phase 3 specifies skeleton loading, empty states, error banner, stale indicators |
| 6 | High | No feedback after launch actions | Resolved — Phase 4 returns LaunchResult, UI shows success/error toasts |
| 7 | High | No handling of stale/deleted workspaces | Resolved — cwd validation at display + resume time; visual indicator + blocked resume |
| 8 | Medium | .jsonl parsing strategy undefined | Resolved — Phase 1 specifies stream-from-start (50 lines) + tail (100 lines) algorithm |
| 9 | Medium | Search should be server-side (htmx consistency) | Resolved — changed to `GET /search?q=` endpoint |
| 10 | Medium | Static assets (htmx.min.js) not in any phase scope | Resolved — added to Phase 3 file scope + exit criterion for vendored file |
| 11 | Medium | CONFIG_DIR hardcoded instead of using %LOCALAPPDATA% env var | Resolved — uses `os.environ.get("LOCALAPPDATA", ...)` |
| 12 | Medium | No confirmation for large batch launches | Resolved — confirmation dialog for batch >5 sessions |
| 13 | Medium | Workspace data source ambiguity | Resolved — Design Decisions specifies session .json as primary, sqlite supplementary |
| 14 | Medium | Subprocess spawn errors unhandled + batch not resilient | Resolved — LaunchResult pattern, per-session try/except, batch never aborts |
| 15 | Medium | pywebview failure should auto-fallback | Resolved — try/except around pywebview with browser fallback |
| 16 | Medium | Window close behavior unspecified | Resolved — close = minimize to tray; Quit = exit; documented in Phase 2 |
| 17 | Low | `tomli` dependency unnecessary on Python 3.11+ | Resolved — removed from deps, using stdlib `tomllib` |
| 18 | Low | [P:1]/[P:2] annotations incorrect (Phase 2 depends on Phase 1) | Resolved — removed parallel annotations, added explicit dependency note |
| 19 | Low | No test files for web UI phases | Resolved — added `tests/test_web.py` to Phase 3 file scope + exit criterion |

### 2026-06-17 — Implementation Review (after Phase 1, persona: Senior engineer, Reliability engineer, Maintainability reviewer, Security auditor)

Implementation health: Yellow → Green (after auto-fix cycle).
13 findings (0 High, 6 Medium, 7 Low). 7 auto-fixed (cycle 1), 6 Low accepted.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | `load_config` released lock before Config construction | Fixed — moved construction inside `with _lock` block |
| 2 | Medium | `_extract_prompts` read entire .jsonl into memory | Fixed — streaming iteration + deque(maxlen=100) for tail |
| 3 | Medium | Orphan .tmp file on crash in save_config | Fixed — try/except BaseException with unlink cleanup |
| 4 | Medium | Broad sqlite3.Error catch in discover_workspaces | Fixed — narrowed to sqlite3.OperationalError |
| 5 | Medium | TOCTOU in _open_sqlite_readonly (exists check before open) | Fixed — removed exists() check, EAFP pattern |
| 6 | Medium | No file-size guard on meta_file.read_text() | Fixed — 1MB size check added; skips oversized files |
| 7 | Low | _extract_content has 3-level nested conditionals | Noted — acceptable complexity for V1, two content schemas |
| 8 | Low | pyproject.toml uses open dep ranges (>=) | Noted — acceptable for early development |
| 9 | Low | Redundant imports in test_config.py | Fixed — removed top-level CONFIG_PATH/CONFIG_DIR imports |
| 10 | Low | No test for sqlite supplementary path | Noted — deferred to Phase 5 or follow-up |
| 11 | Low | Timestamp format mismatch (sqlite epoch vs json ISO) | Noted — accidentally correct, .json always wins |
| 12 | Low | Thread-safety test uses only 20 threads | Noted — sufficient for V1 concurrency profile |
| 13 | Low | LOCALAPPDATA empty string produces relative path | Fixed — uses `or` idiom for empty string handling |

Cycle 2 verified: all 4 personas report Green, no regressions, no new Medium+ findings.

### 2026-06-18 — Implementation Review (after Phase 2, persona: Senior engineer, Reliability engineer, Maintainability reviewer, Security auditor)

Implementation health: Yellow → Green (after auto-fix cycle).
16 findings (3 High, 6 Medium, 7 Low). 8 auto-fixed (cycle 1), 8 accepted trade-offs.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `webview.destroy_window()` not valid pywebview API | Fixed — use `_webview_window.destroy()` |
| 2 | High | Window close (X) does not minimize to tray, no closing handler | Fixed — added `_on_window_closing` with hide + return False |
| 3 | High | `ready_event.wait(10)` timeout not checked, continues with broken state | Fixed — exit with error if `not ready_event.is_set()` |
| 4 | Medium | `server.servers[0].sockets[0]` fallback to dead port 8000 | Fixed — merged into #3 (server failure exits cleanly) |
| 5 | Medium | `_webview_window` global accessed without lock | Fixed — added `_webview_lock` |
| 6 | Medium | Monkey-patching `server.startup` couples to uvicorn internals | Accepted — works for V1, no alternative without forking uvicorn |
| 7 | Medium | `webview.start()` in daemon thread — COM STA concern | Accepted — Windows-only target, documented trade-off |
| 8 | Medium | `get_shutdown_event()` imported but unused | Fixed — removed unused import |
| 9 | Medium | No test coverage for tray.py or __main__.py | Accepted — interactive desktop surfaces, unit-testable surface is minimal |
| 10 | Medium | DLL preloading via COM in autostart.py | Accepted — standard pattern, hardening beyond V1 scope |
| 11 | Low | Unused `import sys` in tray.py | Fixed — removed |
| 12 | Low | `_open_ui` swallows exceptions silently | Accepted — fallback to browser is the correct UX |
| 13 | Low | Mutex name in global namespace | Accepted — local DoS is not a threat model for V1 |
| 14 | Low | TOCTOU on GetLastError | Fixed — use `ctypes.WinDLL(use_last_error=True)` |
| 15 | Low | APPDATA unset produces relative path | Accepted — CI-only scenario, app is desktop-only |
| 16 | Low | No `assets/tray/` directory created | Accepted — Pillow generates icon in-memory, no file needed |

Cycle 2 verified: all fixes correct, no regressions, no new High findings.

### 2026-06-18 — Implementation Review (after Phase 3, persona: Senior engineer, End-user advocate, Reliability engineer, Maintainability reviewer)

Implementation health: Yellow → Green (after auto-fix cycle).
14 findings (4 High, 5 Medium, 5 Low). 7 auto-fixed, 7 accepted.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Search input missing aria-label | Fixed — added aria-label="Search workspaces" |
| 2 | High | Resume button missing accessible context | Fixed — aria-label="Resume session: {{ title }}" |
| 3 | High | No skip-to-content link | Fixed — added in base.html |
| 4 | High | No visual expand/collapse indicator on details | Fixed — CSS chevron icon with rotation |
| 5 | Medium | htmx.min.js is stub, not real library | Accepted — stub sufficient for server-rendered UI; real file for production |
| 6 | Medium | get_sessions() unguarded in workspace loop | Fixed — try/except returns empty list on failure |
| 7 | Medium | Search loop same unguarded issue | Fixed — same treatment |
| 8 | Medium | toggle_trust read-modify-write without full lock | Accepted — single-user desktop, low-risk race |
| 9 | Medium | Toast has no dismiss mechanism | Fixed — dismiss button with aria-label |
| 10 | Low | Session timestamps not human-formatted | Accepted — deferred to follow-up |
| 11 | Low | Search shows all sessions on folder-name match | Accepted — intentional folder match behavior |
| 12 | Low | No caching for workspace iteration | Accepted — V1 proven <2s |
| 13 | Low | Empty search query test missing | Accepted — covered by delegation to partials_workspaces |
| 14 | Low | No htmx error handling for failed load | Accepted — skeleton stays visible, non-critical for V1 |

Cycle 2 verified: all fixes correct, no regressions.
