# Workspace Quick Actions — Folder Open & Terminal

> **Date**: 2026-07-09
> **Status**: In Progress  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Add folder-open and quick-terminal actions to workspace cards, add a terminal launcher tile, remove emoji icon picker

---

## Intent

### Problem statement & desired outcomes

Workspace cards currently lack quick access to two common actions: opening the workspace folder in the system file explorer, and opening a plain terminal at that directory. Users must manually navigate to folders or rely on provider-specific launch buttons. Additionally, the emoji icon picker on workspace cards is unused and occupies a prime interaction spot (the folder icon).

The desired outcome is a streamlined workspace card that lets users (1) click the folder icon to open Explorer/file manager at that path, (2) hover to reveal a quick-terminal button that opens a bare shell session at the workspace, and (3) launch terminals from the Launchers panel using a built-in terminal tile that respects workspace selection.

### Success criteria

1. Clicking the 📁 card-icon on a workspace card opens the OS file manager at that workspace's directory (Windows: Explorer, Linux: xdg-open)
2. The emoji icon picker feature is fully removed (JS, API endpoint, config field `workspace_icons`, template onclick)
3. A terminal button in `card-actions` (visible on hover) opens a plain terminal at the workspace's directory using the configured launch profile — no provider command
4. A built-in "Terminal" launcher tile appears in the Launchers panel; clicking it opens a terminal at selected workspace(s) if any are selected, otherwise at `default_directory`
5. Cross-platform: both Windows and Linux paths work for folder-open and terminal launch

### Scope boundaries & non-goals

**In scope:**
- New `/api/open-folder` endpoint (cross-platform)
- Repurpose card-icon click from icon picker to folder-open
- Remove `showIconPicker()`, `/api/set-workspace-icon`, `workspace_icons` config handling, emoji picker partial
- New "plain terminal" launch path in launcher.py (no provider command)
- Terminal button in workspace card `card-actions` div
- Built-in terminal launcher tile in Launchers panel (uses selected workspaces or default_directory)
- CSS/JS for the new buttons

**Non-goals:**
- Changes to provider-specific launch buttons
- Changes to the Sessions panel
- Pinned session deduplication (confirmed not a bug)
- Custom terminal selection per-workspace (uses global launch profile)

---

## Discovery

### Existing patterns & constraints

- `workspace_card.html:9`: card-icon `<span>` currently has `onclick=showIconPicker(...)` — will be repurposed to call new folder-open function
- `workspace_card.html:15-21`: `card-actions` div contains per-provider new-session buttons + pin button — new terminal button goes here
- `tray.py:60,72`: existing cross-platform open pattern — `os.startfile()` (Windows) and `subprocess.Popen(["xdg-open", path])` (Linux)
- `launcher.py:42-53`: `detect_terminal()` returns terminal path; reusable for plain terminal
- `launcher.py:227-260`: `_build_command()` always expects provider args — cannot handle empty args; needs a new "no-command" variant
- Card-actions appear on hover (`opacity: 0` → visible on `.card-header:hover`) — terminal button fits this pattern
- Custom htmx-mini requires `htmx.process(el)` after innerHTML swaps (project memory)
- Custom launchers already have a `use_selected_workspaces` field — pattern available for the terminal launcher tile

### Risks & mitigations

- `os.startfile(directory)` behavior on Windows: should open Explorer but is technically undocumented for directories — mitigate by falling back to `subprocess.Popen(["explorer", path])` if needed
- Linux file manager depends on `xdg-open` being configured — mitigate by returning error message if it's not found
- Removing `workspace_icons` config field: existing config files with this field won't error (TOML parsing ignores unknown keys in current implementation) — but the icons will silently disappear

### Resolved decisions

- Q1: Folder open via card-icon click — A: Remove emoji picker, repurpose the 📁 icon click for folder-open — Decision: Card-icon click opens file explorer at workspace path; emoji picker removed entirely
- Q2: Terminal button placement — A: In card-actions on hover — Decision: Terminal button in card-actions div, visible on hover alongside provider buttons
- Q3: Terminal launcher tile scope — A: Add both card button and launcher tile — Decision: Built-in terminal launcher tile in Launchers panel, launches at selected workspace(s) or default_directory
- Q4: Pinned session duplication — A: Confirmed not a bug via browser verification — Decision: Dropped from scope

### Open items

- None

### Assumptions (unconfirmed)

- `os.startfile(directory_path)` opens Explorer on Windows (standard behavior, untested on user's exact config)
- `xdg-open directory_path` opens the default file manager on Linux (depends on desktop environment)
- Terminal launcher tile icon: generic terminal symbol (>_ or similar) — no user confirmation on exact icon choice

### Recommended approach

1. **Remove emoji icon picker**: delete `showIconPicker()` JS, `/api/set-workspace-icon` endpoint, `workspace_icons` from config dataclass and load/save, emoji_picker partial template, and the onclick on card-icon
2. **Add folder-open**: new `/api/open-folder` endpoint using `os.startfile` (Win) / `xdg-open` (Linux); repurpose card-icon onclick to call it
3. **Add plain terminal launch path**: new function in `launcher.py` (e.g., `launch_terminal()`) that opens a terminal at cwd with no command — handles Windows Terminal, pwsh, and Linux terminals
4. **Add terminal card button**: button in `card-actions` calling new JS function → `/api/launch-terminal` endpoint
5. **Add terminal launcher tile**: built-in launcher entry rendered in the launchers panel, using selected workspaces or default_directory


---

## Context

Workspace cards have a 📁 icon that currently opens an emoji picker (unused feature). The card-actions div shows per-provider "new session" buttons on hover. There is no way to open a file explorer or plain terminal at a workspace's directory from the UI. The launcher panel shows provider tiles and custom launchers but has no built-in "open terminal" tile.

## Files to modify

| File | Change |
|---|---|
| `src/power_atlas/web.py` | Remove `/api/set-workspace-icon` endpoint, `workspace_icons` refs in `partials_workspaces()` and search; add `/api/open-folder` and `/api/launch-terminal` endpoints; add terminal tile to `partials_launchers()`; add `import os, subprocess` |
| `src/power_atlas/config.py` | Remove `workspace_icons` field from Config dataclass and sanitization |
| `src/power_atlas/launcher.py` | Add `launch_terminal(cwd, launch_profile)` and `_build_terminal_only_command()` functions |
| `src/power_atlas/templates/partials/workspace_card.html` | Replace card-icon onclick with folder-open; add terminal button in card-actions |
| `src/power_atlas/templates/partials/emoji_picker.html` | Delete file |
| `src/power_atlas/templates/index.html` | Remove emoji picker include + `resetOverlays` emoji ref; add `openFolder()`, `openTerminal()` JS functions; add terminal tile + editLauncher handling in `runLauncherById()` |
| `src/power_atlas/static/style.css` | Add `.card-terminal-btn` styling (match `card-new-btn` size/hover) |
| `tests/test_web.py` | Update tests that reference workspace_icons or set-workspace-icon; add tests for new endpoints |
| `tests/test_launcher.py` | Add tests for `launch_terminal()` and `_build_terminal_only_command()` |
| `tests/test_config.py` | Remove workspace_icons-related tests |

## External Dependencies

None — code-only change using OS-native subprocess calls.

## Rollout / Migration / Cleanup

None. The `workspace_icons` key in existing `config.toml` files will be silently ignored by TOML parsing (unknown keys are skipped). No data migration needed.

## Step-by-step

### 1. Remove emoji icon picker [QA]

**Goal**: Clean removal of the entire icon picker feature.

**Delete** `src/power_atlas/templates/partials/emoji_picker.html`.

**In `src/power_atlas/templates/index.html`**:
- Remove `{% include "partials/emoji_picker.html" %}` (line 157)
- In `resetOverlays()` (line 134): remove the `var picker=document.getElementById('emoji-picker');if(picker)picker.style.display='none';` fragment

**In `src/power_atlas/config.py`**:
- Remove `workspace_icons: dict[str, str] = field(default_factory=dict)` (line 60)
- Remove the sanitization line (line 202): `config.workspace_icons = {k: v ...}`

**In `src/power_atlas/web.py`**:
- Remove the entire `/api/set-workspace-icon` endpoint (search for `@app.post("/api/set-workspace-icon")`)
- In `partials_workspaces()`: remove the `norm_icons = {_normalize_path(k): v for k, v in config.workspace_icons.items()}` line and all `icon=norm_icons.get(...)` args in template renders
- In the `search()` function: remove similar `config_icons` / `icon=` references
- Remove `icon` from all `workspace_card.html` render calls

**In `workspace_card.html`** (line 9):
- Change: `<span class="card-icon" onclick="event.stopPropagation();showIconPicker(this,'{{ cwd }}')" title="Click to change icon" role="button" aria-label="Workspace icon">{{ icon or '📁' }}</span>`
- To: `<span class="card-icon" onclick="event.stopPropagation();openFolder('{{ cwd }}')" title="Open folder" role="button" aria-label="Open in file explorer">📁</span>`

**In tests**: remove any assertions on `workspace_icons` or `set-workspace-icon` endpoint.

#### Implementation (2026-07-09, code: 2961793)

Removed the entire emoji icon picker feature: deleted emoji_picker.html partial, removed the include from index.html, removed resetOverlays() emoji cleanup fragment, removed workspace_icons field from Config dataclass and its sanitization, removed the /api/set-workspace-icon endpoint, removed all norm_icons/config_icons/icon= references from partials_workspaces() and search(), repurposed the card-icon onclick from showIconPicker to openFolder, and removed related test functions from test_web.py and test_config.py. All 388 tests pass.

Per-phase review deferred to Step 9: mechanical deletion with no new executable code introduced (openFolder function is Phase 2 scope). QA: SKIP — purely removal, no runtime surface to verify until openFolder is implemented.

### 2. Add folder-open endpoint [QA]

**Goal**: OS-native file explorer opening via new API.

**In `src/power_atlas/web.py`**, add `import os` and `import subprocess` at the top-level imports (alongside existing `sys` import), then add:

```python
@app.post("/api/open-folder")
async def api_open_folder(request: Request):
    body = await request.json()
    folder = body.get("folder", "")
    if not folder or not Path(folder).is_dir():
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": f"Folder not found: {folder}", "level": "error",
        })
    try:
        if sys.platform == "win32":
            os.startfile(folder)
        else:
            subprocess.Popen(["xdg-open", folder])
    except OSError as e:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": f"Could not open folder: {e}", "level": "error",
        })
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": "Folder opened", "level": "success",
    })
```

**In `src/power_atlas/templates/index.html`**, add JS function:

```javascript
function openFolder(cwd) {
  fetch('/api/open-folder', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({folder: cwd})}).then(function(r){return r.text()}).then(showToast);
}
```

#### Implementation (2026-07-09, code: 1b33c4f)

Added /api/open-folder endpoint to web.py with os.startfile (Windows) and xdg-open (Linux) support, including Path.is_dir() validation and OSError handling returning toast responses. Added openFolder() JS function in index.html that POSTs to the endpoint with the workspace cwd. Added os and subprocess imports to web.py.

### 3. Add plain terminal launch [QA]

**Goal**: Open a terminal at a directory with no provider command.

**In `src/power_atlas/launcher.py`**, add `launch_terminal()`:

```python
def launch_terminal(cwd: str, launch_profile: LaunchProfile | None = None) -> LaunchResult:
    """Open a plain terminal at the given cwd. No provider command."""
    profile = launch_profile or LaunchProfile()
    if cwd and cwd != "." and not Path(cwd).exists():
        return LaunchResult(False, None, cwd, error=f"Folder not found: {cwd}")

    terminal = detect_terminal(profile.terminal_command)
    if not terminal:
        if sys.platform == "win32":
            msg = "No terminal found. Configure one in Settings."
        else:
            msg = "No terminal found. Install kitty, alacritty, gnome-terminal, konsole, or xterm."
        return LaunchResult(False, None, cwd, error=msg)

    title = f"Terminal - {Path(cwd).name}"
    cmd = _build_terminal_only_command(terminal, cwd, title=title, wt_profile=profile.wt_profile)
    if cmd is None:
        return LaunchResult(False, None, cwd, error="Could not build terminal command")

    try:
        kwargs: dict = {"creationflags": subprocess.CREATE_NEW_CONSOLE} if sys.platform == "win32" else {"start_new_session": True}
        subprocess.Popen(cmd, **kwargs)
        return LaunchResult(True, None, cwd)
    except OSError as e:
        return LaunchResult(False, None, cwd, error=str(e))
```

Note: `CREATE_NEW_CONSOLE` is correct here (matches existing `launch_session()` for terminal-based providers) — the terminal process needs its own console window.

**Add `_build_terminal_only_command()`** (no provider args):

```python
def _build_terminal_only_command(terminal: str, cwd: str, title: str = "", wt_profile: str = "PowerShell") -> list[str] | None:
    """Build command to open a terminal at cwd with no inner command."""
    stem = Path(terminal).stem.lower()

    # User template with placeholders — substitute {cwd}/{title}/{wt_profile}, leave {cmd}/{pscmd} empty
    if "{cwd}" in terminal or "{title}" in terminal or "{wt_profile}" in terminal:
        parts = re.split(r"(\{cwd\}|\{cmd\}|\{pscmd\}|\{title\}|\{wt_profile\})", terminal)
        result: list[str] = []
        for part in parts:
            if part == "{cwd}":
                result.append(cwd)
            elif part in ("{cmd}", "{pscmd}"):
                continue  # skip — no command
            elif part == "{title}":
                result.append(_sanitize_title(title) if title else "")
            elif part == "{wt_profile}":
                result.append(wt_profile)
            else:
                result.extend(p for p in part.split() if p)
        return result

    # Windows Terminal: just open a tab at cwd
    if stem == "wt":
        cmd = [terminal]
        if title:
            cmd += ["--title", _sanitize_title(title)]
        cmd += ["-p", wt_profile, "-d", cwd]
        return cmd

    # PowerShell: open with Set-Location only
    if stem == "pwsh":
        safe_cwd = cwd.replace("'", "''")
        safe_title = _sanitize_title(title).replace("'", "''")
        script = f"$Host.UI.RawUI.WindowTitle = '{safe_title}'; Set-Location -LiteralPath '{safe_cwd}'"
        return [terminal, "-NoExit", "-Command", script]

    # cmd.exe fallback
    if stem == "cmd":
        inner = f'title {_sanitize_title(title)}&& cd /d "{cwd}"'
        return [terminal, "/k", inner]

    # Linux terminals — omit exec_sep since there is no command to execute
    if stem in _LINUX_TERMINALS:
        title_flag, cwd_flag, exec_sep = _LINUX_TERMINALS[stem]
        cmd = [terminal]
        if title and title_flag:
            if title_flag.endswith("="):
                cmd.append(f"{title_flag}{_sanitize_title(title)}")
            else:
                cmd += [title_flag, _sanitize_title(title)]
        if cwd_flag:
            if cwd_flag.endswith("="):
                cmd.append(f"{cwd_flag}{cwd}")
            else:
                cmd += [cwd_flag, cwd]
        # Do NOT append exec_sep or any command — let the terminal open its default shell
        return cmd

    return None
```

**In `src/power_atlas/web.py`**, add endpoint:

```python
@app.post("/api/launch-terminal", response_class=HTMLResponse)
async def api_launch_terminal(request: Request):
    body = await request.json()
    cwd = body.get("workspace", "")
    config = load_config()
    profile = get_active_launch_profile(config)
    if not cwd:
        cwd = config.default_directory
    if not cwd:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "No workspace selected and no default directory configured", "level": "error",
        })
    result = launcher.launch_terminal(cwd, launch_profile=profile)
    if not result.success:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": result.error, "level": "error",
        })
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": f"Terminal opened: {Path(cwd).name}", "level": "success",
    })
```

**In `workspace_card.html`**, add terminal button in card-actions (before provider buttons):

```html
<button class="card-terminal-btn" onclick="event.stopPropagation();openTerminal(this)" aria-label="Open terminal" title="Open terminal">
  >_
</button>
```

**In `index.html`**, add JS:

```javascript
function openTerminal(btn) {
  var card = btn.closest('.workspace-card');
  fetch('/api/launch-terminal', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({workspace: card.dataset.cwd})}).then(function(r){return r.text()}).then(showToast);
}
```

#### Implementation (2026-07-09, code: 8dc3508)

Added plain terminal launch capability: `_build_terminal_only_command()` builds terminal-specific commands (wt, pwsh, cmd, Linux terminals, user templates) that open a shell at the workspace directory without running any provider; `launch_terminal()` validates the path with is_dir, detects the terminal, and spawns the process; `/api/launch-terminal` endpoint reads the workspace from the request body (falling back to config.default_directory); the workspace card template gains a `>_` button before the provider launch buttons; `openTerminal()` JS function POSTs to the endpoint with error handling matching `openFolder()`; and `.card-terminal-btn` CSS shares selector with `.card-new-btn`. Review fixes (f33fbcb): xterm uses `${SHELL:-/bin/sh}` fallback, validation uses `is_dir()` not `exists()`, variable renamed to `stem`, CSS deduplicated.

### 4. Add built-in terminal launcher tile [QA]

**Goal**: Terminal tile in Launchers panel, selection-aware.

**In `src/power_atlas/web.py`**, in `partials_launchers()`, add a terminal tile after provider tiles and before custom launchers:

```python
    # Built-in terminal tile (after providers, before custom launchers)
    terminal_tile = {
        "id": "builtin--terminal",
        "name": "Terminal",
        "command": "terminal",
        "custom_args": "",
        "color": "#6b7280",  # neutral gray
        "terminal": True,
        "use_selected_workspaces": True,
        "is_provider": True,  # shows lock icon + prevents editing
    }
    html += templates.get_template("partials/launcher_tile.html").render(request=request, launcher=terminal_tile)
```

**In `index.html`**, extend `runLauncherById()` to handle `builtin--terminal`:

```javascript
// At the top of runLauncherById, before the provider-- check:
if (id === 'builtin--terminal') {
  var selected = getSelectedWorkspaceCwds();
  if (selected.length > 0) {
    selected.forEach(function(w) {
      fetch('/api/launch-terminal', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({workspace: w})}).then(function(r){return r.text()}).then(showToast);
    });
  } else {
    fetch('/api/launch-terminal', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({workspace: ''})}).then(function(r){return r.text()}).then(showToast);
  }
  return;
}
```

Also in `editLauncher()`, add early exit for the terminal tile (no editable settings):

```javascript
// At the top, after the provider-- check:
if (id === 'builtin--terminal') return;
```

**In `src/power_atlas/icons.py`**, the `/api/launcher-icon/builtin--terminal` route needs to return a terminal icon SVG. Add a case in the icon endpoint for the `builtin--terminal` id:

```python
# In the launcher-icon endpoint, handle the builtin--terminal case
if launcher_id == "builtin--terminal":
    svg = default_icon_svg(True, "#6b7280")
    return Response(content=svg, media_type="image/svg+xml")
```

#### Implementation (2026-07-09, code: 9d275a1)

Added built-in terminal launcher tile in partials_launchers() (web.py) with `builtin--terminal` id placed after provider tiles and before custom launchers. Extended runLauncherById() with selection-aware terminal launch (iterates selected workspaces or falls back to default_directory). Added editLauncher() early-exit for the terminal tile. Added builtin--terminal icon handling in the launcher_icon endpoint. All with .catch() error handling matching Phase 2/3 patterns.

### 5. Update tests

**Goal**: Ensure existing tests pass and new features have coverage.

- Remove tests referencing `set-workspace-icon`, `workspace_icons` in `test_web.py` and `test_config.py`
- Add test for `/api/open-folder` (mock `os.startfile` / `subprocess.Popen`)
- Add test for `/api/launch-terminal` (mock `launcher.launch_terminal`)
- Add test for `launch_terminal()` in `test_launcher.py`
- Verify `partials_launchers` response includes the terminal tile

#### Implementation (2026-07-09, code: 2ca1814)

Added 18 tests total: 8 in test_web.py (open-folder valid/invalid/OSError/empty, launch-terminal success/no-dir/failure, launchers terminal tile) and 10 in test_launcher.py (launch_terminal valid/invalid/OSError/no-terminal/empty-cwd, _build_terminal_only_command for wt/pwsh/kitty/xterm/cmd-metachar). Review fixes (662aa34) added error-path coverage identified by 4-persona review. All 406 tests pass.

## Verification

```bash
pytest tests/ -x -q
```

Manual checks:
- Click 📁 icon on a workspace card → Explorer opens at that path
- Hover workspace card → see terminal `>_` button → click → terminal opens at cwd
- Click Terminal tile in Launchers panel with no selection → opens at default_directory
- Select a workspace card → click Terminal tile → terminal opens at that workspace
- Verify emoji picker is fully gone (no UI remnant, no API response)

## Documentation updates

| Document | Update needed |
|---|---|
| `README.md` | Add folder-open and terminal quick-action features to the Features list |

## Review Log

### 2026-07-09 — Plan Review (Senior engineer, high effort)

13 findings (2 High, 6 Medium, 5 Low). 8 auto-resolved.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `web.py` missing `import os` and `import subprocess` for `/api/open-folder` | Resolved — added explicit import note in Step 2 |
| 2 | High | (duplicate of #1 — subprocess) | Resolved — merged with #1 |
| 3 | Medium | `editLauncher()` silently fails for `builtin--terminal` | Resolved — added early-exit in Step 4 |
| 4 | Medium | `updateLauncherBadges()` won't find terminal tile | Noted — tile has `is_provider: True` which gives it `provider-tile` class; existing badge logic covers it |
| 5 | Medium | Linux terminals with `exec_sep` may hang with no command | Resolved — added explicit "omit exec_sep" comment in Step 3 |
| 6 | Medium | Line numbers in plan may be inaccurate for web.py | Resolved — replaced with pattern-based search instructions |
| 7 | Medium | Same as #6 for search() references | Resolved — merged with #6 |
| 8 | Medium | `CREATE_NEW_CONSOLE` + wt may create spurious console | Noted — matches existing launch_session() pattern; acceptable |
| 9 | Low | `icons.py` change unnecessary since fallback already works | Noted — kept for explicit color assignment |
| 10 | Low | JS toast pattern is correct | No action needed |
| 11 | Low | Missing CSS for `.card-terminal-btn` | Resolved — added `style.css` to files-to-modify |
| 12 | Low | pwsh single-quote escaping for paths | Resolved — added `replace("'", "''")`  |
| 13 | Low | Gear button inert for terminal tile (undocumented) | Noted — gear hidden by early-exit in editLauncher |

### 2026-07-09 -- Implementation Review (after Phase 2, personas: Senior engineer, Security auditor, Reliability engineer, End-user advocate)

Implementation health: Green.
9 findings (0 High, 5 Medium, 4 Low). High-effort review (4 personas).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | Inline `'{{ cwd }}'` in onclick breaks if path contains quotes on Linux | Fixed — switched to `dataset.cwd` DOM read pattern (d35bf71) |
| 2 | Medium | No `.catch()` on fetch — network failure silently drops user feedback | Fixed — added .catch with error toast (d35bf71) |
| 3 | Medium | Missing `response_class=HTMLResponse` on endpoint decorator | Fixed — added to decorator (d35bf71) |
| 4 | Medium | `subprocess.Popen` on Linux missing DEVNULL + start_new_session | Fixed — added both kwargs (d35bf71) |
| 5 | Medium | `Path(folder).is_dir()` can raise OSError/ValueError on malformed paths | Fixed — wrapped in try/except (d35bf71) |
| 6 | Low | Success toast shows "Folder opened" without identifying which folder | Fixed — now shows folder basename (d35bf71) |
| 7 | Low | TOCTOU race between is_dir check and os.startfile | Accepted — OS-level error handling covers it |
| 8 | Low | Tests deferred to Phase 5 | Expected — plan groups tests in Phase 5 |
| 9 | Low | UNC paths could trigger NTLM hash disclosure via Explorer | Accepted — localhost-only tool, cwd from session discovery |

### 2026-07-09 -- Implementation Review (after Phase 3, personas: Senior engineer, Security auditor, Reliability engineer, Maintainability reviewer)

Implementation health: Green.
8 findings (0 High, 4 Medium, 4 Low). High-effort review (4 personas).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | Linux logic inlines code already in `_linux_base_cmd()` — duplication | Accepted — refactoring shared helper exceeds phase scope |
| 2 | Medium | CSS byte-identical to `.card-new-btn` | Fixed — combined selectors (f33fbcb) |
| 3 | Medium | xterm `exec $SHELL` fails if env unset — terminal closes | Fixed — uses `${SHELL:-/bin/sh}` fallback (f33fbcb) |
| 4 | Medium | Tests deferred to Phase 5 | Expected — plan groups tests separately |
| 5 | Low | Uses `exists()` not `is_dir()` — file path passes validation | Fixed — switched to `is_dir()` (f33fbcb) |
| 6 | Low | Variable `t` inconsistent with `stem` in module | Fixed — renamed to `stem` (f33fbcb) |
| 7 | Low | Template branch is dead code (detect_terminal returns resolved path) | Accepted — defensive future-proofing |
| 8 | Low | xterm shell wrapper diverges from plan (more correct than plan) | Accepted — plan's approach would open at HOME |

### 2026-07-09 -- Implementation Review (after Phase 4, personas: Senior engineer, Security auditor, End-user advocate, Maintainability reviewer)

Implementation health: Green.
8 findings (0 High, 2 Medium, 6 Low). High-effort review (4 personas).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | `#6b7280` hardcoded in tile dict and icon endpoint — no constant | Accepted — only 2 references, near each other; not worth a constant |
| 2 | Medium | Test for terminal tile ordering not added | Expected — Phase 5 covers all test additions |
| 3 | Low | `is_provider: True` semantically misleading for non-provider tile | Accepted — functional meaning is "shows lock, prevents editing" |
| 4 | Low | Gear button renders but is inert (editLauncher no-ops) | Accepted — plan review finding #13; no user confusion |
| 5 | Low | Tooltip shows "Command: terminal" — slightly misleading | Accepted — consistent with provider tiles showing binary name |
| 6 | Low | Multi-workspace selection spawns N parallel toasts | Accepted — matches existing batch launch behavior for providers |
| 7 | Low | No test for builtin--terminal icon endpoint | Expected — Phase 5 scope |
| 8 | Low | `builtin--` prefix convention undocumented | Accepted — single built-in; document if pattern grows |

### 2026-07-09 -- Implementation Review (after Phase 5, personas: Senior engineer, Security auditor, Reliability engineer, Maintainability reviewer)

Implementation health: Green.
8 findings (0 High, 5 Medium, 3 Low). High-effort review (4 personas).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | `test_open_folder_valid_directory` never asserts mock was actually called | Fixed — added `assert_called_once_with(folder)` (662aa34) |
| 2 | Medium | No test for `launch_terminal` OSError from Popen | Fixed — added `test_popen_oserror` (662aa34) |
| 3 | Medium | No web test for `/api/open-folder` OSError path | Fixed — added `test_open_folder_oserror` (662aa34) |
| 4 | Medium | No web test for `/api/launch-terminal` failure result | Fixed — added `test_launch_terminal_failure_result` (662aa34) |
| 5 | Medium | No test for cmd metachar rejection returning None | Fixed — added `test_cmd_metachar_returns_none` (662aa34) |
| 6 | Low | No test for empty folder path in open-folder | Fixed — added `test_open_folder_empty_path` (662aa34) |
| 7 | Low | No test for no-terminal-detected error path | Fixed — added `test_no_terminal_detected` (662aa34) |
| 8 | Low | No test for empty cwd in launch_terminal | Fixed — added `test_empty_cwd` (662aa34) |