# Custom Launchers, Icons, Session Tail & Tab Title

> **Date**: 2026-06-19
> **Status**: In Progress
> **Scope**: Four V2 roadmap features — custom launchers, custom icons, session output tail tooltip, terminal tab title on launch
> **Estimated effort**: 2-3 days

---

## Intent

### Problem statement & desired outcomes

The orchestrator currently only launches kiro-cli sessions and has no visual customization. Users want to: (1) run arbitrary CLI tools from the dashboard, (2) visually distinguish workspaces and launchers with icons, (3) preview session output without opening a terminal, and (4) have meaningful tab titles when sessions launch. These four features collectively elevate the orchestrator from a kiro-cli-specific launcher to a general-purpose workspace command center.

### Success criteria

1. **Custom launchers**: can create, edit, delete, and run a custom launcher from the UI; launcher definition persists in config across restarts; custom_args field is editable inline on the tile before each run; gearcog opens a modal for advanced fields (name, fixed command, cwd, env vars, icon)
2. **Custom icons**: can assign an emoji icon to a workspace via a clickable slot on the card header; icon displays persistently; can assign icon to a custom launcher via the modal; categorized emoji picker (5-6 tabs, ~80 emojis) with text input for custom emoji or file path
3. **Session output tail**: hovering a session row shows a styled tooltip with the last ~5 assistant message lines from the session's .jsonl file; fetched on-demand via htmx; disappears on mouseleave
4. **Tab title on launch**: launching a kiro-cli session sets terminal tab title to `kiro-cli - <folder-name>` for the three known terminals (wt, pwsh, cmd); custom launchers get `<command> - <cwd>`; unsupported/custom terminal templates silently skip title injection

### Scope boundaries & non-goals

**In scope**: Custom launcher CRUD + execution in visible terminal, workspace icon assignment + persistence, categorized emoji picker, session tail tooltip (on-demand fetch, assistant messages only), tab title for wt/pwsh/cmd.

**Non-goals**: Hidden/background launcher mode, per-session icon override (sessions inherit workspace icon), live-streaming/WebSocket tail, tab title for custom terminal templates, single-tab enforcement (dropped — browser security prevents focusing existing tabs from external process), SVG/PNG bundled icon assets (emoji only for V1), env var UI validation, WSL support.

---

## Discovery

### Existing patterns & constraints

- Config dataclass (config.py:13-19) uses simple types with `isinstance` validation. `list[dict]` for launchers passes as `list` — no element-type validation. Permissive loading is the project philosophy.
- `_SETTING_TYPES` allowlist (web.py:300-316) must be extended for new config fields.
- All launches go through `_build_command()` (launcher.py:71-114) with three terminal branches (wt, pwsh, cmd) plus a custom template branch.
- Templates use emoji already (📌 pin buttons). No image/icon infrastructure exists.
- `_extract_content(line, "AssistantMessage")` (data.py:204) is the parsing primitive for session content.
- Session rows use htmx lazy-loading pattern (`hx-trigger`, `hx-get`) — same pattern fits tooltip fetching.
- SessionCache returns copies (project MEMORY.md pattern). New cache structures must follow.
- Existing modal/dialog pattern: none — this will be the first `<dialog>` element in the UI.
- Dead partial `action_bar.html` exists with incompatible selection model — not relevant.

### Risks & mitigations

- **Config type validation for `list[dict]`**: `load_config()` won't validate inner dict structure. Mitigation: permissive load, validate at save time in UI.
- **Shell safety for custom launcher commands**: existing `_CMD_METACHAR_RE` only covers cmd.exe. Mitigation: custom launchers run through the same `_build_command` terminal-detection logic; custom commands are passed as-is (no shell interpretation via `shell=True`).
- **File I/O for session tail on hover**: reading .jsonl tail on every hover could cause disk pressure. Mitigation: read only last 100 lines via `deque`, extract last 5 assistant messages, no caching (single fetch per hover).
- **Windows file locking**: kiro-cli may be writing to .jsonl concurrently. Mitigation: `errors="replace"` (already used in data.py:258) + non-exclusive open.
- **Tab title test breakage**: adding `--title` to wt commands changes `_build_command` output. Mitigation: update existing tests.
- **Topbar terminal dropdown vs custom terminal**: changing topbar dropdown overwrites custom terminal_command (mutation-finder finding). Not addressed in this scope — pre-existing issue.

### Resolved decisions

- Q1: All items in one plan, phased by dependency — A: ok — Decision: Single plan with all 4 features (single-tab enforcement dropped later at Q18)
- Q2: Custom launchers as dedicated section above pinned workspaces, global (not workspace-scoped) — A: ok — Decision: Dedicated "Custom Launchers" section at top of dashboard
- Q3: Launcher data model: name, fixed command, custom_args (editable), cwd, env vars, icon — A: add working directory, env vars, icon; command split into fixed + custom with gearcog for advanced — Decision: Two-part command model (fixed + custom_args); modal for rarely-changed fields
- Q4: Tile shows editable custom_args input; clicking Run launches with fixed+custom; custom args passed as-is (no splitting) — A: ok — Decision: Inline editable args, no parsing/splitting
- Q5: Custom icons: both built-in emoji set + custom file path; workspaces get icons, sessions inherit — A: both — Decision: Emoji + file path; workspace-level assignment; sessions inherit
- Q6: Emoji characters (zero packaging cost) for built-in set — A: ok — Decision: Emoji-based icons, no bundled assets
- Q7: Session tail as hover-tooltip, on-demand htmx fetch, all sessions (not just active), last ~5 assistant lines — A: ok — Decision: htmx hover tooltip, fetch once per hover
- Q8: Tab title format: `kiro-cli - <folder-name>` for sessions, `<command> - <cwd>` for custom launchers — A: kiro-cli sessions = `kiro-cli - <folder-name>`, custom launchers = `command + cwd` — Decision: As stated by user
- Q9: Single-tab enforcement via BroadcastChannel — A: ok initially — Decision: Superseded by Q18
- Q10: Permissive config loading for launcher dicts; validation at save time in UI — A: ok — Decision: Permissive load, UI-side validation
- Q11: Modal dialog for launcher create/edit (HTML `<dialog>` element) — A: ok — Decision: `<dialog>` modal, first in the project
- Q12: Emoji picker = categorized palette (5-6 tabs, ~15 each) + text input for custom — A: ok — Decision: Categorized emoji grid + text fallback
- Q13: Workspace icon via clickable slot left of folder name in card header, default 📁 — A: ok — Decision: Icon slot replaces/augments folder name area
- Q14: Workspace icons stored in config.toml as `workspace_icons: dict[str, str]` field — A: ok — Decision: New Config field, same file
- Q15-Q18: Single-tab enforcement — A: drop the feature — Decision: Dropped. Browser security prevents focusing existing tabs from external process; pywebview changes UX surface unacceptably. Supersedes Q9.
- Q17: Tab title only for known terminals (wt/pwsh/cmd); `{title}` placeholder not needed for now — A: ok to support only known terminals, show warning for unsupported — Decision: Known terminals only, no custom template support
- Q19: Custom launchers open in visible terminal (same pattern as kiro-cli) — A: ok, hidden mode later — Decision: Visible terminal only
- Q20: Environment variables are additive (merged on top of `os.environ`) — A: ok — Decision: Additive env vars

### Open items

- None. All design decisions resolved.

### Recommended approach

Implement in 4 phases ordered by dependency and complexity:

1. **Tab title on launch** — smallest change; modify `_build_command` to inject title args for wt/pwsh/cmd. Update tests.
2. **Custom icons infrastructure** — add `workspace_icons: dict[str, str]` to Config, emoji picker component (reusable), icon slot on workspace card headers, API endpoints for icon assignment.
3. **Custom launchers** — add `custom_launchers: list[dict]` to Config, launcher execution logic (reuse terminal detection + tab title), launcher tiles section in UI, modal dialog for CRUD, inline custom_args editing.
4. **Session output tail** — add `/partials/session-tail?sid=<id>` endpoint, tail extraction function (last 5 assistant messages), htmx hover-triggered tooltip on session rows, tooltip CSS.

---

## 1) Current State

**Launcher** (launcher.py): `_build_command()` has three terminal branches — wt, pwsh, cmd — plus a custom template branch. None inject tab titles. `launch_session()` hardcodes `kiro_args = ["kiro-cli", "chat"]`. Existing `-p PowerShell` flag on wt branch.

**Config** (config.py): `Config` dataclass has 5 fields (bool, bool, str, list[str], list[str]). `load_config()` type-checks with `isinstance(v, expected_type)` — works for `dict` and `list` natively via TOML parsing.

**Web** (web.py): `_SETTING_TYPES` allowlist gates `save_setting()`. No modal dialogs, no tooltip infrastructure. Workspace cards have no icon element.

**Data** (data.py): `_extract_content(line, "AssistantMessage")` parses v2 session content. `_extract_prompts()` reads first 50 + tail 100 lines via deque. v2-only: `SESSION_DIR = ~/.kiro/sessions/cli/`.

**Templates**: No `<dialog>` elements. No tooltip CSS. Emoji used for pins (📌) only.

**Tests**: test_launcher.py asserts exact command lists from `_build_command` — will need updating.

## 2) Goal

Add tab titles to terminal launches, a custom launcher system with full CRUD and inline args editing, workspace icon assignment with a categorized emoji picker, and session output tail tooltips on hover — transforming the orchestrator from a kiro-cli-specific launcher into a general-purpose workspace command center.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Launcher data model | `list[dict]` in config.toml `[[custom_launchers]]` | Separate JSON file; SQLite | TOML array-of-tables is human-editable, no new deps, matches existing config pattern |
| Command model | Two-part: fixed command + editable custom_args | Single command string; Template with placeholders | Separates rarely-changed executable from frequently-changed arguments; no parsing/splitting needed |
| Icon source | Emoji characters (categorized palette) + file path fallback | Bundled SVGs; Icon fonts | Zero packaging cost; OS renders emoji; file path covers power users |
| Icon scope | Workspace-level (sessions inherit) | Per-session icons | Workspace is the natural grouping; per-session adds config bloat |
| Icon storage | `workspace_icons: dict[str, str]` field on Config | Separate file; Embedded in workspace metadata | Single config file, simple schema |
| Session tail trigger | htmx hover (on-demand fetch) | Polling; WebSocket; Preloaded | Minimal server load; no streaming infrastructure needed |
| Tab title approach | Known terminals only (wt/pwsh/cmd) | Also support custom templates via `{title}` placeholder | Simplest; custom templates have naive split() that would break title injection |
| Launcher UI | Dedicated section + modal `<dialog>` for CRUD | Inline editing; Settings page | Modal handles 5+ fields cleanly; section gives visual separation |
| Env vars model | Additive (merged onto `os.environ`) | Exclusive (clean slate) | Tools expect PATH, TEMP, etc. to be present |
| Config validation | Permissive load, validate at save time in UI | Strict schema validation | Matches existing config philosophy (wrong types get defaults) |

## 4) External Dependencies & Costs

### Required external changes

None. All changes are local code and config.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Tab title on launch [QA] [P:2,4]

**Goal**: Inject terminal tab titles into the three known terminal branches of `_build_command()`.

**File scope**: `src/kiro_orchestrator/launcher.py`, `tests/test_launcher.py`

**Changes**:

1. Add `title` parameter to `_build_command()`:

```python
def _build_command(terminal: str, cwd: str, kiro_args: list[str], title: str = "") -> list[str] | None:
```

2. Inject title per terminal branch:

```python
def _sanitize_title(title: str) -> str:
    """Strip characters unsafe for shell title injection."""
    return title.replace('"', '').replace("'", '').replace('&', '-').replace('|', '-')

# wt branch
if t == "wt":
    cmd = [terminal, "--title", _sanitize_title(title)] if title else [terminal]
    cmd += ["-p", "PowerShell", "-d", cwd, "--", *kiro_args]
    return cmd

# pwsh branch
if t == "pwsh":
    escaped_cwd = cwd.replace("'", "''")
    escaped_title = title.replace("'", "''") if title else ""
    title_prefix = f"$Host.UI.RawUI.WindowTitle = '{escaped_title}'; " if title else ""
    script = f"{title_prefix}Set-Location -LiteralPath '{escaped_cwd}'; & {' '.join(kiro_args)}"
    return [terminal, "-NoExit", "-Command", script]

# cmd branch
if _CMD_METACHAR_RE.search(cwd):
    return None
kiro_cmd = " ".join(kiro_args)
safe_title = _sanitize_title(title) if title else ""
title_cmd = f"title {safe_title}&& " if safe_title else ""
return [terminal, "/k", f'{title_cmd}cd /d "{cwd}" && {kiro_cmd}']
```

3. Update `launch_session()` to derive and pass title:

```python
title = f"kiro-cli - {Path(cwd).name}"
cmd = _build_command(terminal, cwd, kiro_args, title=title)
```

4. Update tests: adjust expected command lists in `TestBuildCommand` and `TestLaunchSession`.

**Exit criteria**:
- [x] `_build_command` accepts `title` param and injects it for wt, pwsh, cmd
- [x] Custom template branch ignores title (no injection)
- [x] `launch_session` passes `"kiro-cli - <folder>"` as title
- [x] All existing tests updated and passing
- [x] New test verifies title appears in each terminal's command output

**Implementation (2026-06-19, code: 27dac16)**
Added `title: str = ""` parameter to `_build_command()` with `_sanitize_title()` helper that strips shell-unsafe chars (`"`, `'`, `&`, `|`). Title is injected per terminal: wt uses `--title`, pwsh prepends `$Host.UI.RawUI.WindowTitle = '...'`, cmd prepends `title X&&`. Custom templates ignore it. `launch_session()` derives title as `kiro-cli - <folder>`. Six new tests in `TestTabTitle` class verify all branches.

### Phase 2: Custom icons [QA] [P:1,4]

**Goal**: Workspace icon assignment via categorized emoji picker, persisted in config.

**File scope**: `src/kiro_orchestrator/config.py`, `src/kiro_orchestrator/web.py`, `src/kiro_orchestrator/templates/partials/workspace_card.html`, `src/kiro_orchestrator/templates/partials/emoji_picker.html` (new), `src/kiro_orchestrator/static/style.css`, `tests/test_config.py`, `tests/test_web.py`

**Changes**:

1. Add to Config dataclass:
```python
workspace_icons: dict[str, str] = field(default_factory=dict)
```
Note: No special-case loading needed — the existing generic `isinstance(v, expected_type)` handles `dict` correctly since TOML tables decode as `dict`.

2. Do NOT add `workspace_icons` to `_SETTING_TYPES` — use the dedicated endpoint only (prevents accidental full-overwrite via the generic `save_setting` API).

3. Add API endpoints in web.py:
```python
@app.post("/api/set-workspace-icon")
async def set_workspace_icon(request: Request):
    body = await request.json()
    config = load_config()
    workspace = _normalize_path(body["workspace"])
    icon = body.get("icon", "")
    if icon:
        config.workspace_icons[workspace] = icon
    else:
        config.workspace_icons.pop(workspace, None)  # Remove = reset to default
    save_config(config)
    return {"ok": True}
```

4. Create `templates/partials/emoji_picker.html` — categorized grid component with accessibility:
```html
<div class="emoji-picker" id="emojiPicker" role="dialog" aria-modal="true"
     aria-label="Choose an icon" tabindex="-1">
  <div class="emoji-tabs" role="tablist">
    <button class="emoji-tab active" data-cat="tools" role="tab" aria-selected="true">Tools</button>
    <button class="emoji-tab" data-cat="objects" role="tab">Objects</button>
    <button class="emoji-tab" data-cat="nature" role="tab">Nature</button>
    <button class="emoji-tab" data-cat="symbols" role="tab">Symbols</button>
    <button class="emoji-tab" data-cat="flags" role="tab">Flags</button>
  </div>
  <div class="emoji-grid" id="emojiGrid" role="grid" aria-label="Emoji options"></div>
  <input type="text" class="emoji-custom" placeholder="Custom emoji or file path"
         aria-label="Custom icon input">
  <div class="emoji-picker-footer">
    <button class="emoji-reset-btn" onclick="resetIcon()">Reset to default</button>
    <button class="emoji-close-btn" onclick="closeIconPicker()" aria-label="Close">×</button>
  </div>
</div>
```

5. Add icon slot to `workspace_card.html` before folder name:
```html
<span class="card-icon" onclick="event.stopPropagation();showIconPicker(this, '{{ cwd }}')"
      title="Click to change icon" role="button" aria-label="Workspace icon: {{ icon or 'folder' }}">{{ icon or '📁' }}</span>
```

6. Pass `icon` to workspace card render calls in web.py. Pre-compute normalized icon lookup once per request:
```python
# At top of partials_workspaces, after loading config:
norm_icons = {_normalize_path(k): v for k, v in config.workspace_icons.items()}
# Then per card:
icon = norm_icons.get(_normalize_path(cwd), "")
```

7. Add CSS for `.emoji-picker`, `.emoji-tabs`, `.emoji-grid`, `.emoji-tab`, `.card-icon`, `.emoji-picker-footer`.

8. JS behavior (to be implemented in index.html inline script):
   - `showIconPicker(el, cwd)`: positions picker near clicked icon, focuses picker container, adds click-outside listener
   - Tab switching: arrow keys between tabs, Enter activates
   - Grid navigation: arrow keys across emoji items, Enter/Space selects
   - Escape: closes picker, returns focus to trigger element
   - Selection: calls `/api/set-workspace-icon`, updates the card-icon text, closes picker
   - `resetIcon()`: calls `/api/set-workspace-icon` with empty icon value

**Exit criteria**:
- [ ] `workspace_icons` persists in config.toml and round-trips through load/save
- [ ] Clicking icon slot on workspace card opens emoji picker
- [ ] Selecting emoji saves via `/api/set-workspace-icon` and updates card display
- [ ] Categorized picker shows 5 tabs with ~15 emojis each
- [ ] Text input accepts custom emoji or file path
- [ ] "Reset to default" removes the icon assignment
- [ ] Picker is keyboard-navigable (arrow keys, Enter, Escape)
- [ ] Picker has proper ARIA roles and labels
- [ ] Icon lookup uses normalized paths on both read and write
- [ ] Tests cover config round-trip with dict field and API endpoint

### Phase 3: Custom launchers [QA]

**Goal**: Full CRUD for custom launchers with inline args editing and execution.

**File scope**: `src/kiro_orchestrator/config.py`, `src/kiro_orchestrator/launcher.py`, `src/kiro_orchestrator/web.py`, `src/kiro_orchestrator/templates/partials/launcher_tile.html` (new), `src/kiro_orchestrator/templates/partials/launcher_modal.html` (new), `src/kiro_orchestrator/templates/index.html`, `src/kiro_orchestrator/static/style.css`, `tests/test_launcher.py`, `tests/test_web.py`, `tests/test_config.py`

**Changes**:

1. Add to Config:
```python
custom_launchers: list[dict] = field(default_factory=list)
```
Note: No special-case loading needed — the existing generic `isinstance(v, list)` handles it. Do NOT add to `_SETTING_TYPES` — use dedicated CRUD endpoints only.

2. Each launcher dict has a stable UUID `id` field (generated at creation):
```python
# Structure:
{"id": "uuid4-string", "name": "...", "command": "...", "custom_args": "...", "cwd": "...", "env": {}, "icon": "🚀"}
```

3. Add launcher execution function in launcher.py. Key design: `command` is treated as a single executable path (not split), and `custom_args` is passed as a raw string appended to the terminal command invocation — avoiding the naive `split()` problem:
```python
def launch_custom(
    name: str,
    command: str,
    custom_args: str = "",
    cwd: str = "",
    env: dict[str, str] | None = None,
    terminal_override: str = "",
) -> LaunchResult:
    """Launch a custom command in a terminal. Returns result, never raises."""
    terminal = detect_terminal(terminal_override)
    if not terminal:
        return LaunchResult(False, None, cwd or ".", error="No terminal found.")
    
    work_dir = cwd or "."
    if not Path(work_dir).exists():
        return LaunchResult(False, None, work_dir, error=f"Folder not found: {work_dir}")
    
    # Build the full command string (not split — preserves paths with spaces)
    full_cmd_str = f"{command} {custom_args}".strip() if custom_args else command
    title = _sanitize_title(f"{Path(command).stem} - {Path(work_dir).name}")
    
    # Custom launcher uses a dedicated command-building path (not _build_command)
    # because _build_command assumes kiro-cli arg list semantics
    cmd = _build_custom_command(terminal, work_dir, full_cmd_str, title)
    if cmd is None:
        return LaunchResult(False, None, work_dir, error="Path contains unsafe characters for this terminal")
    
    proc_env = None
    if env:
        proc_env = {**os.environ, **env}
    
    try:
        kwargs: dict = {"creationflags": subprocess.CREATE_NEW_CONSOLE} if sys.platform == "win32" else {"start_new_session": True}
        if proc_env:
            kwargs["env"] = proc_env
        subprocess.Popen(cmd, **kwargs)
        return LaunchResult(True, None, work_dir)
    except OSError as e:
        return LaunchResult(False, None, work_dir, error=str(e))


def _build_custom_command(terminal: str, cwd: str, cmd_str: str, title: str) -> list[str] | None:
    """Build terminal command for a custom launcher. cmd_str is the full command as a string."""
    t = Path(terminal).stem.lower()
    if t == "wt":
        parts = [terminal, "--title", title, "-p", "PowerShell", "-d", cwd, "--", "cmd", "/c", cmd_str]
        return parts
    if t == "pwsh":
        escaped_cwd = cwd.replace("'", "''")
        escaped_title = title.replace("'", "''")
        script = f"$Host.UI.RawUI.WindowTitle = '{escaped_title}'; Set-Location -LiteralPath '{escaped_cwd}'; & cmd /c '{cmd_str}'"
        return [terminal, "-NoExit", "-Command", script]
    # cmd fallback
    if _CMD_METACHAR_RE.search(cwd):
        return None
    safe_title = _sanitize_title(title)
    return [terminal, "/k", f'title {safe_title}&& cd /d "{cwd}" && {cmd_str}']
```

4. Add CRUD API endpoints in web.py (all return toast partials for user feedback):
```python
@app.post("/api/launcher/create", response_class=HTMLResponse)
@app.post("/api/launcher/update", response_class=HTMLResponse)
@app.post("/api/launcher/delete", response_class=HTMLResponse)
@app.post("/api/launcher/run", response_class=HTMLResponse)  # Returns toast on success/failure
```
The `/api/launcher/run` endpoint passes the launcher dict from the request body directly — no config re-read needed per run.

5. Create `launcher_tile.html`:
```html
<div class="launcher-tile" data-id="{{ launcher.id }}">
  <span class="launcher-icon">{{ launcher.icon or '🚀' }}</span>
  <span class="launcher-name">{{ launcher.name }}</span>
  <input type="text" class="launcher-args" value="{{ launcher.custom_args or '' }}"
         placeholder="args..." title="Editable arguments" aria-label="Custom arguments for {{ launcher.name }}">
  <button class="launcher-run-btn" onclick="runLauncher(this)" aria-label="Run {{ launcher.name }}">▶</button>
  <button class="launcher-gear-btn" onclick="editLauncher('{{ launcher.id }}')" aria-label="Edit {{ launcher.name }}">⚙️</button>
</div>
```

6. Create `launcher_modal.html` — `<dialog>` with full accessibility:
```html
<dialog id="launcherModal" aria-labelledby="launcherModalTitle">
  <h3 id="launcherModalTitle">Edit Launcher</h3>
  <form method="dialog">
    <!-- name, command, custom_args, cwd, icon picker (reused from Phase 2) -->
    <!-- env vars: textarea with placeholder="API_KEY=xxx\nDEBUG=1" -->
    <!-- Parse: split on first = per line, skip empty lines, trim whitespace -->
    <div class="modal-actions">
      <button type="button" class="btn-delete" onclick="confirmDeleteLauncher()">Delete</button>
      <button type="button" class="btn-cancel" onclick="this.closest('dialog').close()">Cancel</button>
      <button type="submit" class="btn-save">Save</button>
    </div>
  </form>
</dialog>
<!-- Delete confirmation inline: -->
<div class="delete-confirm hidden">Delete this launcher? <button>Confirm</button> <button>Cancel</button></div>
```
Focus management: on open, focus first input; on close (Escape or Cancel), return focus to trigger (gearcog button).

7. Add "Custom Launchers" section to `index.html` above the cards area, with "+" button and empty state:
```html
<section id="launcherSection" class="launcher-section">
  <div class="section-label">Custom Launchers
    <button class="launcher-add-btn" onclick="createLauncher()" aria-label="Add launcher">+</button>
  </div>
  <div class="launcher-grid" id="launcherGrid">
    <!-- Tiles rendered here, or empty state: -->
    <!-- <div class="empty-hint">No custom launchers. Click + to create one.</div> -->
  </div>
</section>
```

8. CSS for `.launcher-tile`, `.launcher-modal`, `.launcher-args`, `.launcher-section`, `.launcher-grid`, `.empty-hint`, `.delete-confirm`.

9. Env vars textarea parsing spec: one var per line, `KEY=VALUE` format (split on first `=` only per line; skip empty lines; trim whitespace from key and value).

**Exit criteria**:
- [ ] Can create a launcher via "+" button and modal (UUID assigned)
- [ ] Launcher tile shows name, icon, editable args input, run button, gearcog
- [ ] Running a launcher opens a terminal with the full command + custom args (no naive split)
- [ ] Tab title set to `<command-stem> - <cwd-name>`
- [ ] Gearcog opens modal for editing all fields (name, command, cwd, env, icon)
- [ ] Delete requires confirmation before removing
- [ ] Launchers referenced by UUID (not index) — reordering/deletion is safe
- [ ] Launchers persist in config.toml as `[[custom_launchers]]` with `id` field
- [ ] Env vars are additive (merged onto os.environ)
- [ ] Run button shows toast feedback on success/failure
- [ ] Empty state shown when no launchers configured
- [ ] Modal has focus management (trap, Escape, return focus)
- [ ] Tests cover `launch_custom()`, `_build_custom_command()`, CRUD endpoints, config round-trip
- [ ] Test for malformed launcher dicts in config (empty dict, wrong types) — no crash
- [ ] Update README.md with Custom Launchers in Features list and config example

### Phase 4: Session output tail tooltip [QA] [P:1,2]

**Goal**: Styled tooltip showing last ~5 assistant messages on session row hover.

**File scope**: `src/kiro_orchestrator/data.py`, `src/kiro_orchestrator/web.py`, `src/kiro_orchestrator/templates/partials/session_row.html`, `src/kiro_orchestrator/templates/partials/session_tail.html` (new), `src/kiro_orchestrator/static/style.css`, `tests/test_data.py`, `tests/test_web.py`

**Known limitation**: Session tail reads v2 `.jsonl` files only (`SESSION_DIR = ~/.kiro/sessions/cli/`). Sessions from kiro-cli v3 (which use `~/.kiro/sessions/<hash>/sess_<uuid>/messages.jsonl` with a different envelope format) will return empty tooltips. This is acceptable for V1 since the project's data layer (`data.py`) exclusively uses v2 paths throughout.

**Changes**:

1. Add tail extraction function in data.py with seek-from-end optimization and TTL cache:
```python
_tail_cache: dict[str, tuple[float, float, list[str]]] = {}  # sid -> (time, mtime, lines)
_TAIL_CACHE_TTL = 5  # seconds

def get_session_tail(session_id: str, max_lines: int = 5) -> list[str]:
    """Extract last N assistant message texts from a session's .jsonl. Cached 5s."""
    jsonl_path = SESSION_DIR / f"{session_id}.jsonl"
    if not jsonl_path.exists():
        return []
    
    try:
        st = jsonl_path.stat()
    except OSError:
        return []
    
    # TTL cache check
    cached = _tail_cache.get(session_id)
    if cached and (time.time() - cached[0] < _TAIL_CACHE_TTL) and cached[1] == st.st_mtime:
        return list(cached[2])
    
    try:
        # Seek from end for efficiency (avoid reading entire multi-MB file)
        with open(jsonl_path, "rb") as fh:
            fh.seek(0, 2)  # end
            size = fh.tell()
            read_size = min(size, 65536)  # last 64KB
            fh.seek(size - read_size)
            tail_bytes = fh.read()
        
        lines = tail_bytes.decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    
    messages = []
    for line in reversed(lines):
        # Skip tool-use lines before parsing (perf: short-circuit)
        if '"toolUse"' in line:
            continue
        text = _extract_content(line, "AssistantMessage")
        if text:
            truncated = text[:150] + "…" if len(text) > 150 else text
            messages.append(truncated)
            if len(messages) >= max_lines:
                break
    messages.reverse()
    
    _tail_cache[session_id] = (time.time(), st.st_mtime, messages)
    return list(messages)
```

2. Add endpoint in web.py:
```python
@app.get("/partials/session-tail", response_class=HTMLResponse)
async def partials_session_tail(request: Request, sid: str = ""):
    import asyncio
    messages = await asyncio.to_thread(data.get_session_tail, sid)
    if not messages:
        return HTMLResponse('<div class="tail-empty">No recent output</div>')
    return templates.TemplateResponse(request, "partials/session_tail.html", {
        "messages": messages,
    })
```

3. Create `templates/partials/session_tail.html`:
```html
<div class="session-tail-tooltip">
  {% for msg in messages %}
  <div class="tail-line">{{ msg }}</div>
  {% endfor %}
</div>
```

4. Add htmx hover trigger to session_row.html (no `once` — re-fetches on each hover for fresh data, debounced 300ms to avoid rapid-fire on mouse scans):
```html
<div class="session-tooltip-anchor"
     hx-get="/partials/session-tail?sid={{ session.session_id }}"
     hx-trigger="mouseenter delay:300ms"
     hx-target="find .session-tooltip-slot"
     hx-swap="innerHTML">
  <div class="session-tooltip-slot">
    <div class="tail-loading">Loading...</div>
  </div>
</div>
```
The loading placeholder appears during the 300ms debounce + fetch time. Tooltip hides on mouseleave via CSS (`:hover` on the anchor controls tooltip visibility).

5. CSS for `.session-tail-tooltip`:
   - `position: fixed` (escapes card-body overflow context)
   - Dark background, monospace font, max-width 400px, z-index above cards and action bar
   - `.tail-loading`: subtle opacity animation
   - Visibility controlled by `.session-tooltip-anchor:hover .session-tooltip-slot`
   - Positioning computed via JS (small helper that sets `top`/`left` based on anchor rect)

**Exit criteria**:
- [ ] Hovering a session row (300ms debounce) triggers fetch of `/partials/session-tail?sid=<id>`
- [ ] Tooltip shows last 5 assistant message snippets (max 150 chars + "…" when truncated)
- [ ] Tool-use messages filtered out (only prose content shown)
- [ ] Tooltip disappears on mouseleave (CSS :hover)
- [ ] Tooltip positioned via `position: fixed` (not clipped by card overflow)
- [ ] Loading state visible during fetch
- [ ] Re-fetch on subsequent hovers (not cached client-side; server caches 5s by mtime)
- [ ] Uses seek-from-end (last 64KB) — O(1) for large files
- [ ] Graceful fallback for sessions with no .jsonl or no assistant messages
- [ ] Tests cover `get_session_tail()` extraction, TTL cache behavior, and the API endpoint

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Config type validation for `list[dict]` | Malformed launcher entries render as broken tiles | Permissive load; UI validates on save; broken entries show with "incomplete" badge |
| Custom launcher shell safety | Commands go through terminal-specific escaping | Dedicated `_build_custom_command()` handles each terminal; no `shell=True`; `_sanitize_title()` strips metacharacters |
| .jsonl concurrent read while kiro-cli writes | OSError or partial line read | `errors="replace"` on decode; seek-from-end reads last 64KB only; try/except per file |
| Tab title breaks existing tests | CI fails until tests updated | Phase 1 updates all affected tests as part of the same change |
| Emoji rendering inconsistency | Some emojis may show as squares | Use well-supported emoji (Unicode 12+); text input fallback |
| Session tail is v2-only | v3 sessions return empty tooltips | Documented limitation; data.py uses v2 paths exclusively |
| Custom launcher paths with spaces | Broken execution | `_build_custom_command` treats command as single string (no split) |
| Launcher index-based identity | Wrong launcher mutated on concurrent edits | UUID-based identity; all references use `launcher.id` |
| Tooltip disk I/O on hover | Latency spike on large files | Seek-from-end (O(1)); 5s TTL cache by mtime; 300ms debounce |

## 7) Verification

**Automated**:
```bash
pytest tests/ -v
```

**Manual checks**:
- Launch a kiro-cli session → verify terminal tab shows "kiro-cli - <folder>"
- Assign emoji to a workspace → refresh → verify it persists
- Create custom launcher → edit args → run → verify terminal opens with correct command + title
- Hover a session row → verify tooltip appears with assistant message content

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Add Custom Launchers and Icons to Features list; update config example with new fields | 3 |

## 9) Implementation Divergences from Plan

<Reserved — filled during implementation>

## Review Log

### 2026-06-19 — Implementation Review (after Phase 1, persona: Senior engineer)

Implementation health: Yellow.
4 findings (0 High, 1 Medium, 3 Low).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | `-p PowerShell` flag not introduced by this phase — pre-existing in working tree | Not applicable — pre-existing change, plan acknowledges it |
| 2 | Low | `_sanitize_title` stripped spaces making title unreadable | Fixed — removed space from unsafe chars regex |
| 3 | Low | Custom template test assertion was a tautology (logical OR always true) | Fixed — tightened to direct absence check |
| 4 | Low | cmd `title X&&` pattern is unconventional but standard | Informational — no action needed |

### 2026-06-19 — Plan Review (4 personas, High effort)

15 findings (3 High, 9 Medium, 3 Low). 12 auto-resolved, 3 noted.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | wt branch drops `-p PowerShell` flag when adding title | Resolved — preserved `-p PowerShell` in wt command; added `_sanitize_title()` |
| 2 | High | `launch_custom` uses naive `split()` breaking paths with spaces | Resolved — introduced `_build_custom_command()` that treats command as single string |
| 3 | High | Emoji picker and launcher modal have no accessibility (ARIA, keyboard) | Resolved — added ARIA roles, keyboard nav spec, focus management to both |
| 4 | Medium | Special-case config loading for dict/list is dead code | Resolved — removed; generic `isinstance` handles both types |
| 5 | Medium | cmd title quoting vulnerable to metacharacters in folder names | Resolved — added `_sanitize_title()` stripping unsafe chars; cmd uses unquoted form |
| 6 | Medium | Session tail is v2-only; v3 sessions return empty | Resolved — documented as known limitation in Phase 4 |
| 7 | Medium | Launcher referenced by array index; deletion shifts all indices | Resolved — UUID-based identity added to launcher dict schema |
| 8 | Medium | No icon reset/remove UX | Resolved — "Reset to default" button added to picker; API accepts empty icon |
| 9 | Medium | Tooltip `hx-trigger="mouseenter once"` shows stale data | Resolved — removed `once`; re-fetches per hover with 300ms debounce + server TTL cache |
| 10 | Medium | Full-file sequential read for tail O(n) in file size | Resolved — seek-from-end (last 64KB) + 5s TTL cache by mtime |
| 11 | Medium | `workspace_icons`/`custom_launchers` in `_SETTING_TYPES` allows full-overwrite via generic endpoint | Resolved — removed from `_SETTING_TYPES`; dedicated endpoints only |
| 12 | Medium | Workspace icon lookup not using normalized paths | Resolved — specified `_normalize_path` on both read and write |
| 13 | Low | Line number references drift from actual code | Resolved — removed all line numbers; reference by function/class name only |
| 14 | Low | Emoji picker JS behavior unspecified | Resolved — added JS behavior spec (tab switching, selection, positioning, dismiss) |
| 15 | Low | Env vars textarea parsing rules ambiguous | Resolved — specified: split first `=` per line, skip empty, trim whitespace |

Remaining (Low, informational, not blocking):
- Search doesn't cover custom launcher names (V1 known limitation)
- No JS test framework for client-side interactions (covered by [QA] browser verification)

Phase 2 before 3 because the emoji picker built for icons is reused in the launcher modal.
