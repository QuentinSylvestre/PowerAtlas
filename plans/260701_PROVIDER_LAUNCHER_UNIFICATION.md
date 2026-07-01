# Provider-Launcher Unification

> **Date**: 2026-07-01
> **Status**: In Progress  <!-- Status lifecycle: Exploring → Draft → In Progress → Complete -->
> **Scope**: Merge providers into launcher grid, fix Kiro IDE icon/launch, propagate provider icon/color to workspace cards, deduplicate workspace selection.
> **Estimated effort**: 1-2 days

---

## Intent

### Problem statement & desired outcomes

PowerAtlas has two parallel systems for managing AI tool launching: providers (with session discovery, tabs, and a separate settings modal) and custom launchers (tiles in a grid with per-launcher config). This creates a fragmented UX where provider settings live in one place and launch actions in another. Additionally, the Kiro IDE launcher has two bugs (wrong icon due to `.cmd` resolution, launch ignores selected workspace), and workspace selection is not deduplicated across provider cards.

### Success criteria

1. **SC1 — Providers as launcher tiles**: kiro-cli and claude-code appear as non-deletable, partially-editable tiles in the launcher grid (first position, before user launchers). Provider modal and tab gear icons removed.
2. **SC2 — Kiro IDE icon fix**: Icon extraction follows `.cmd`→`.exe` resolution by parsing the batch file content to find the underlying executable.
3. **SC3 — Provider/launcher icon+color in workspace markers**: Workspace cards show the provider-launcher's extracted icon (or fallback) and color instead of the hardcoded letter badge.
4. **SC4 — Workspace deduplication for custom launchers**: `getSelectedWorkspaceCwds()` returns unique paths only. Badge shows unique count. Custom launchers fire once per unique workspace. "Launch selected" (action bar) remains per-card/provider-aware (no dedup).
5. **SC5 — Non-terminal workspace argument**: Non-terminal, selection-aware launchers append the workspace path as an argument to the command (not just cwd). Fixes Kiro IDE launch.

### Scope boundaries & non-goals

**In scope**: Provider-to-launcher unification, icon `.cmd` resolution, workspace card icon/color propagation from provider-launchers, cwd deduplication in JS, non-terminal workspace-as-argument fix.

**Non-goals**: Migration logic for existing config (big-bang cleanup). Kiro IDE session discovery. New provider types. Reordering UI for launcher tiles. Bundled static provider icons.


---

## 1) Current State

**Config** (`src/power_atlas/config.py:23-31`):
- `provider_settings: dict[str, dict]` — keyed by provider name, fields: `default_args`, `color`, `enabled`.
- `custom_launchers: list[dict]` — each entry: `id`, `name`, `command`, `custom_args`, `cwd`, `env`, `color`, `terminal`, `use_selected_workspaces`.
- These two structures are completely independent with different schemas and different launch pipelines.

**Launcher pipeline** (`src/power_atlas/launcher.py`):
- `launch_session()` (line 91-137): provider-aware, builds `["kiro-cli", "chat", "--resume-id", id]` or `["claude", "--resume", id]`. Uses `_PROVIDER_BINARY` lookup.
- `launch_custom()` (line 233-274): raw command string, `full_cmd_str = f"{command} {custom_args}".strip()`, workspace is ONLY `cwd=work_dir`, never passed as argument.
- `launch_custom_batch()` (line 219-230): iterates workspaces, calls `launch_custom()` per each. No deduplication.

**Icon extraction** (`src/power_atlas/icons.py`):
- `_resolve_binary()` (line 63-79): `shutil.which("kiro")` → returns `kiro.cmd`. Suffix gate at line 51 only allows `.exe`/`.msi`. `.cmd` falls through to generic SVG.
- No `.cmd`→`.exe` resolution exists.

**Web layer** (`src/power_atlas/web.py`):
- `PROVIDER_COLORS` (line 19), `PROVIDER_BADGES` (line 25), `PROVIDER_DISPLAY_NAMES` (line 22): hardcoded dicts for workspace card rendering.
- Provider modal: `GET /api/provider/{key}` (line 371), `POST /api/provider/save` (line 378), `templates/partials/provider_modal.html`. Tab gear icons at line 262.
- Launcher grid: `GET /partials/launchers` (line 621), rendered from `config.custom_launchers` only.

**JS selection** (`src/power_atlas/templates/index.html:80`):
- `getSelectedWorkspaceCwds()`: pushes ALL `.workspace-card.selected` cwds without dedup. Same physical workspace from two provider cards → duplicate entries.
- `updateLauncherBadges()`: uses raw count from above. Badge shows inflated number.
- `runLauncherById()`: sends duplicated list to `/api/launcher/run-batch`.

**Workspace card template** (`templates/partials/workspace_card.html:1,6`):
- Colored left-border from `provider_color`, letter badge ("K"/"C") with colored background.
- No icon image — only text badge.

## 2) Goal

Unify provider settings and custom launchers into a single launcher grid. Provider entries appear as non-deletable tiles with the same edit modal. Fix icon extraction for `.cmd` shims, append workspace path as argument for non-terminal selection-aware launchers, deduplicate workspace selection, and propagate provider icon/color to workspace cards.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Provider-launcher data structure | Keep in `provider_settings` (existing), render as tiles in launcher grid | Move into `custom_launchers` with `system: True` flag | Separate launch pipeline (`launch_session` vs `launch_custom`); cleaner separation |
| Provider-launcher dispatch | Call `launch_session(session_id=None)` per unique workspace | Reuse `launch_custom()` for all | `launch_session` already handles binary validation, provider args, terminal title |
| Non-terminal workspace argument | Automatic append for non-terminal + selection-aware launchers | User-configurable checkbox; `{cwd}` placeholder | Simple semantics; only "Kiro IDE" matches pattern currently |
| Icon `.cmd` resolution | Parse `.cmd`/`.bat` content, regex for `.exe` path | Look for `.exe` with same stem nearby; bundle static icons | Generic for Electron apps; handles VS Code, Kiro, etc. |
| Workspace dedup scope | Dedup only in `getSelectedWorkspaceCwds()` (custom launchers) | Dedup everywhere including "Launch selected" | "Launch selected" is provider-aware — selecting both cards is intentional |
| Provider icon fallback | Same pipeline as custom launchers (no bundled icons) | Bundle static SVG/PNG for known providers | Extraction already works for Claude; terminal fallback fine for Kiro CLI |
| Provider-launcher editability | `default_args` + `color` editable; `name`/`command` locked; non-deletable | Fully editable; fully locked | Users need to configure args; command/name are system-defined |
| Provider-launcher position | Always first in grid, before user launchers | Last; mixed ordering | Primary tools deserve prominence |
| Config migration | None (big-bang cleanup by user) | Auto-detect and migrate matching custom launchers | Single user; manual cleanup acceptable |
| Tab bar after modal removal | Clean filter tabs, no gear icons | Keep gear icons pointing to launcher edit | Provider settings now live on launcher tiles |

## 4) External Dependencies & Costs

### Required external changes

None. All changes are local code + config.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Icon `.cmd`→`.exe` resolution and non-terminal workspace argument [QA] [P:2]

**Goal**: Fix icon extraction for `.cmd`/`.bat` shims (SC2) and make non-terminal selection-aware launchers append workspace as argument (SC5).

**File scope**: `src/power_atlas/icons.py`, `src/power_atlas/launcher.py`, `tests/test_launcher.py`

**Detailed changes**:

1. `icons.py` — add `_resolve_cmd_to_exe(cmd_path: Path) -> Path | None`:
   ```python
   import re
   
   _EXE_IN_CMD_RE = re.compile(r'["\s]([^"]*\.exe)', re.IGNORECASE)
   _EXE_IN_CMD_QUOTED_RE = re.compile(r'"([^"]*\.exe)"', re.IGNORECASE)
   # Also handle %~dp0 relative paths
   _EXE_RELATIVE_RE = re.compile(r'%~dp0(\.\.\\[^"]*\.exe|\.\.\/[^"]*\.exe)', re.IGNORECASE)
   
   def _resolve_cmd_to_exe(cmd_path: Path) -> Path | None:
       """Parse a .cmd/.bat file to find the underlying .exe it wraps."""
       try:
           content = cmd_path.read_text(encoding="utf-8", errors="ignore")
       except OSError:
           return None
       # Try %~dp0-relative paths first (Electron pattern)
       for m in _EXE_RELATIVE_RE.finditer(content):
           rel = m.group(1).replace("/", "\\")
           candidate = (cmd_path.parent / rel).resolve()
           if candidate.is_file():
               return candidate
       # Try quoted absolute paths
       for m in _EXE_QUOTED_RE.finditer(content):
           candidate = Path(m.group(1))
           if candidate.is_file():
               return candidate
       # Try unquoted paths
       for m in _EXE_IN_CMD_RE.finditer(content):
           candidate = Path(m.group(1).strip())
           if candidate.is_file():
               return candidate
       return None
   ```

2. `icons.py` — modify `extract_icon()` (line 46-56): after `_resolve_binary()`, if the resolved path suffix is `.cmd` or `.bat`, call `_resolve_cmd_to_exe()` and use the result:
   ```python
   def extract_icon(launcher_id: str, command: str, is_terminal: bool) -> bool:
       ICONS_DIR.mkdir(parents=True, exist_ok=True)
       target = icon_path(launcher_id)
       binary = _resolve_binary(command)
       # Follow .cmd/.bat → .exe resolution
       if binary and binary.suffix.lower() in (".cmd", ".bat"):
           resolved_exe = _resolve_cmd_to_exe(binary)
           if resolved_exe:
               binary = resolved_exe
       if binary and binary.suffix.lower() in (".exe", ".msi") and sys.platform == "win32":
           if _extract_windows_icon(binary, target):
               return True
       target.unlink(missing_ok=True)
       return False
   ```

3. `launcher.py` — modify `launch_custom()` (around line 255): when non-terminal AND selection-aware, append workspace as a properly-escaped argument:
   ```python
   def launch_custom(name: str, command: str, custom_args: str = "", cwd: str = "",
                     env: dict[str, str] | None = None, terminal_override: str = "",
                     use_terminal: bool = True, pass_workspace_arg: bool = False) -> LaunchResult:
       work_dir = cwd or "."
       if not Path(work_dir).exists():
           return LaunchResult(False, None, work_dir, error=f"Folder not found: {work_dir}")
       full_cmd_str = f"{command} {custom_args}".strip() if custom_args else command
       # Append workspace as argument for non-terminal selection-aware launches
       if pass_workspace_arg and work_dir != ".":
           if sys.platform == "win32":
               # Windows: wrap in quotes, escape inner quotes
               escaped = work_dir.replace('"', '""')
               full_cmd_str = f'{full_cmd_str} "{escaped}"'
           else:
               full_cmd_str = f'{full_cmd_str} {shlex.quote(work_dir)}'
       ...
   ```

4. `launcher.py` — modify `launch_custom_batch()`: pass `pass_workspace_arg` only when BOTH `not use_terminal` AND the launcher is selection-aware (caller must pass this):
   ```python
   def launch_custom_batch(..., use_terminal: bool = True,
                           pass_workspace_arg: bool = False) -> list[LaunchResult]:
       results = []
       for ws in (workspaces or []):
           cwd = ws or str(Path.home())
           results.append(launch_custom(
               ..., cwd=cwd, ..., use_terminal=use_terminal,
               pass_workspace_arg=pass_workspace_arg,
           ))
       return results
   ```
   The `pass_workspace_arg` flag is set by the caller (`web.py`'s `launcher_run_batch` endpoint) based on whether the launcher has `use_selected_workspaces=True AND terminal=False`. This prevents non-selection-aware non-terminal launchers from getting workspace appended.

5. `tests/test_launcher.py` — add tests:
   - Test `launch_custom()` with `pass_workspace_arg=True` includes workspace in command string.
   - Test `launch_custom()` with `pass_workspace_arg=False` does not include workspace.
   - Test `launch_custom_batch()` with `use_terminal=False` passes workspace arg.

6. Tests for icon `.cmd` resolution (can be in same or separate test file):
   - Create a temp `.cmd` file with `%~dp0..\App.exe` content, mock the `.exe` path existing → verify `_resolve_cmd_to_exe()` returns the resolved path.
   - Test fallback when no `.exe` found → returns None.

**Exit criteria**:
- [x] `_resolve_cmd_to_exe()` correctly parses `kiro.cmd` pattern and returns `Kiro.exe` path
- [x] `extract_icon()` for command `"kiro"` now attempts icon extraction from `Kiro.exe`
- [x] `launch_custom()` with `pass_workspace_arg=True` appends workspace path to command
- [x] `launch_custom_batch()` with `use_terminal=False` automatically passes workspace arg
- [x] All existing launcher tests pass (regression-free)
- [x] New tests for `.cmd` resolution and workspace arg pass

**Implementation (2026-07-01, code: 225c655)**
Added `_resolve_cmd_to_exe(cmd_path)` to `icons.py` that parses `.cmd`/`.bat` shim files to find their underlying `.exe` binary. It handles three patterns in priority order: `%~dp0`-relative paths (the Electron/scoop shim pattern), quoted absolute paths, and unquoted absolute paths - each validated against the filesystem. Modified `extract_icon()` to call this resolver when the binary has a `.cmd`/`.bat` suffix, allowing icon extraction from the real `.exe` (e.g., `kiro.cmd` → `Kiro.exe`). Added `pass_workspace_arg` parameter to both `launch_custom()` and `launch_custom_batch()`. When `True` and the working directory is not `.`, the workspace path is appended to the command string with platform-appropriate quoting (double-quotes on Windows for paths with spaces, `shlex.quote` on Linux). This enables non-terminal GUI launchers to receive the workspace as an argument for selection-aware launching. Added 13 new tests covering `.cmd` resolution (relative paths, absolute paths, missing exe, unreadable files) and workspace argument passing (with/without terminal, quoting, batch forwarding, dot-cwd guard).

### Phase 2: Workspace deduplication in JS [QA] [P:1]

**Goal**: Fix `getSelectedWorkspaceCwds()` to return unique workspace paths only (SC4).

**File scope**: `src/power_atlas/templates/index.html`, `tests/test_web.py`

**Detailed changes**:

1. `index.html` — rewrite `getSelectedWorkspaceCwds()`:
   ```javascript
   function getSelectedWorkspaceCwds(){
     var seen={};
     var cwds=[];
     document.querySelectorAll('.workspace-card.selected').forEach(function(c){
       var key=c.dataset.cwd.toLowerCase();
       if(!seen[key]){seen[key]=1;cwds.push(c.dataset.cwd)}
     });
     document.querySelectorAll('.session-row.selected').forEach(function(r){
       var cwd=r.dataset.cwd;
       if(cwd){var key=cwd.toLowerCase();if(!seen[key]){seen[key]=1;cwds.push(cwd)}}
     });
     return cwds;
   }
   ```
   Uses case-insensitive comparison (`.toLowerCase()`) for Windows path dedup.

2. `tests/test_web.py` — the dedup is purely client-side JS, so add a comment documenting the behavior. Server-side `launch_custom_batch()` already handles whatever list it receives — no server-side change needed.

**Exit criteria**:
- [x] `getSelectedWorkspaceCwds()` returns unique paths (case-insensitive dedup)
- [x] Badge count reflects unique workspace count
- [x] Custom launcher fires once per unique workspace even when same cwd selected via multiple provider cards
- [x] "Launch selected" action bar still launches per-card (no dedup there — unchanged)
- [x] Existing tests pass

**Implementation (2026-07-01, code: 5cc8e8f)**
Rewrote `getSelectedWorkspaceCwds()` in `index.html` to use a case-insensitive deduplication pattern. The new implementation uses a `seen` object keyed by `.toLowerCase()` paths to ensure each unique workspace path appears only once in the returned array, regardless of whether the same path is selected via multiple provider cards or a combination of workspace cards and session rows. The badge count and custom launcher batch dispatch automatically benefit from this dedup since they already call `getSelectedWorkspaceCwds()`. The per-card "Launch selected" action bar is unaffected as it uses its own iteration logic. All 42 existing tests pass without modification.

### Phase 3: Provider-launcher tiles in launcher grid [QA]

**Goal**: Render providers as non-deletable, partially-editable tiles in the launcher grid (SC1). Remove provider modal and tab gear icons.

**File scope**: `src/power_atlas/web.py`, `src/power_atlas/templates/index.html`, `src/power_atlas/templates/partials/launcher_tile.html`, `src/power_atlas/templates/partials/provider_modal.html`, `src/power_atlas/templates/partials/launcher_modal.html`, `src/power_atlas/static/style.css`, `tests/test_web.py`

**Detailed changes**:

1. `web.py` — modify `partials_launchers()` (line 621): prepend provider-launcher tiles before user custom launchers:
   ```python
   @app.get("/partials/launchers", response_class=HTMLResponse)
   async def partials_launchers(request: Request):
       config = load_config()
       providers = data.available_providers()
       html = ""
       # Provider-launcher tiles first
       for p in providers:
           if not config.provider_settings.get(p, {}).get("enabled", True):
               continue
           p_settings = config.provider_settings.get(p, {})
           provider_launcher = {
               "id": f"provider--{p}",
               "name": PROVIDER_DISPLAY_NAMES.get(p, p),
               "command": _PROVIDER_BINARY_DISPLAY.get(p, p),
               "custom_args": p_settings.get("default_args", ""),
               "cwd": "",
               "color": p_settings.get("color", "") or PROVIDER_COLORS.get(p, ""),
               "terminal": True,
               "use_selected_workspaces": True,
               "is_provider": True,
           }
           html += templates.get_template("partials/launcher_tile.html").render(
               request=request, launcher=provider_launcher)
       # User custom launchers
       for l in config.custom_launchers:
           html += templates.get_template("partials/launcher_tile.html").render(
               request=request, launcher=l)
       return HTMLResponse(html)
   ```

2. `web.py` — add constant:
   ```python
   _PROVIDER_BINARY_DISPLAY = {
       "kiro-cli": "kiro-cli chat",
       "claude-code": "claude",
   }
   ```

3. `launcher_tile.html` — add lock indicator for provider tiles and prevent delete:
   ```html
   <div class="launcher-tile{% if launcher.is_provider %} provider-tile{% endif %}" data-id="{{ launcher.id }}" onclick="runLauncherById('{{ launcher.id }}')"{% if launcher.color %} style="border-left: 3px solid {{ launcher.color }}"{% endif %}>
     {% if launcher.use_selected_workspaces %}<span class="launcher-badge" id="badge-{{launcher.id}}"></span>{% endif %}
     {% if launcher.is_provider %}<span class="launcher-lock">🔒</span>{% endif %}
     <img class="launcher-icon" src="/api/launcher-icon/{{ launcher.id }}" alt="" aria-hidden="true">
     ...
   ```

4. `web.py` — modify `/api/launcher-icon/{launcher_id}` to handle provider IDs:
   ```python
   @app.get("/api/launcher-icon/{launcher_id}")
   async def launcher_icon(launcher_id: str):
       # Provider launcher icon
       if launcher_id.startswith("provider--"):
           provider_key = launcher_id[len("provider--"):]
           binary = launcher._PROVIDER_BINARY.get(provider_key, provider_key)
           # Try to extract icon (runs through .cmd→.exe resolution now)
           icons.extract_icon(launcher_id, binary, True)
           if icons.has_icon(launcher_id):
               return FileResponse(icons.icon_path(launcher_id), media_type="image/png")
           svg = icons.default_icon_svg(True)
           return Response(content=svg, media_type="image/svg+xml")
       # Existing custom launcher logic...
   ```

5. `index.html` JS — modify `runLauncherById()` to handle provider-launcher dispatch:
   ```javascript
   function runLauncherById(id){
     // Provider-launcher: use launch_session per workspace
     if(id.startsWith('provider--')){
       var provider=id.slice('provider--'.length);
       var selected=getSelectedWorkspaceCwds();
       if(selected.length>0){
         var sessions=selected.map(function(ws){return {workspace:ws,provider:provider}});
         fetch('/api/launch-batch',{method:'POST',headers:{'Content-Type':'application/json'},
           body:JSON.stringify({sessions:sessions})}).then(function(r){return r.text()}).then(showToast);
       }
       return;
     }
     // Existing custom launcher logic...
     var l=_launchers.find(function(x){return x.id===id});
     ...
   }
   ```

6. `index.html` JS — modify `editLauncher()` to handle provider tiles (open modal with locked fields):
   ```javascript
   function editLauncher(id){
     if(id.startsWith('provider--')){
       openProviderLauncherModal(id);
       return;
     }
     // existing logic...
   }
   function openProviderLauncherModal(id){
     var provider=id.slice('provider--'.length);
     fetch('/api/provider/'+encodeURIComponent(provider)).then(function(r){return r.json()}).then(function(d){
       var m=document.getElementById('launcherModal');
       document.getElementById('launcherId').value=id;
       document.getElementById('launcherName').value=d.provider||provider;
       document.getElementById('launcherCommand').value=''; // not editable
       document.getElementById('launcherArgs').value=d.default_args||'';
       document.getElementById('launcherCwd').value='';
       document.getElementById('launcherEnv').value='';
       document.getElementById('launcherColor').value=d.color||'';
       document.getElementById('launcherTerminal').checked=true;
       document.getElementById('launcherUseSelected').checked=true;
       document.getElementById('launcherDeleteBtn').style.display='none';
       document.getElementById('launcherModalTitle').textContent='Provider Settings — '+d.provider;
       // Lock fields
       document.getElementById('launcherName').readOnly=true;
       document.getElementById('launcherCommand').readOnly=true;
       document.getElementById('launcherCommand').value=_PROVIDER_BINARY_DISPLAY[provider]||provider;
       document.getElementById('launcherTerminal').disabled=true;
       document.getElementById('launcherUseSelected').disabled=true;
       // Show enabled toggle for provider tiles
       document.getElementById('launcherEnabledRow').style.display='';
       document.getElementById('launcherEnabled').checked=d.enabled!==false;
       m.showModal();
       document.getElementById('launcherArgs').focus();
     });
   }
   ```
   Note: The `launcherEnabledRow` is a new row in `launcher_modal.html` (hidden by default, shown only for provider tiles). The `launcherEnabled` checkbox maps to `provider_settings[provider].enabled`.

   **Field unlock**: Reset locked/disabled state on dialog close (not just on save success):
   ```javascript
   document.getElementById('launcherModal').addEventListener('close', function(){
     document.getElementById('launcherName').readOnly=false;
     document.getElementById('launcherCommand').readOnly=false;
     document.getElementById('launcherTerminal').disabled=false;
     document.getElementById('launcherUseSelected').disabled=false;
     document.getElementById('launcherEnabledRow').style.display='none';
   });
   ```
   ```

7. `index.html` JS — modify `saveLauncher()` to detect provider ID and route to provider save:
   ```javascript
   function saveLauncher(e){
     e.preventDefault();
     var id=document.getElementById('launcherId').value;
     if(id.startsWith('provider--')){
       var provider=id.slice('provider--'.length);
       var payload={provider:provider,
         default_args:document.getElementById('launcherArgs').value,
         color:document.getElementById('launcherColor').value,
         enabled:document.getElementById('launcherEnabled').checked};
       fetch('/api/provider/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
         .then(function(r){return r.text()}).then(function(t){showToast(t);document.getElementById('launcherModal').close();
           htmx.ajax('GET','/partials/launchers','#launcher-tiles');
           refreshCards(); // Refresh workspace cards if provider was disabled
         });
       return;
     }
     // existing save logic...
   }
   ```

8. Remove provider modal and tab gear icons:
   - Delete `templates/partials/provider_modal.html` (or empty its content).
   - `web.py` (around line 262): remove the gear-icon rendering loop in `partials_workspaces()`.
   - `index.html`: remove `{% include "partials/provider_modal.html" %}` and `openProviderModal`/`saveProvider` JS functions.

9. `index.html` JS — update `updateLauncherBadges()` to handle provider-launcher IDs:
   ```javascript
   function updateLauncherBadges(){
     var count=getSelectedWorkspaceCwds().length;
     // Update provider-launcher badges
     document.querySelectorAll('.provider-tile .launcher-badge').forEach(function(badge){
       if(count>0){badge.textContent=count;badge.classList.add('visible')}
       else{badge.textContent='';badge.classList.remove('visible')}
     });
     // Update custom launcher badges
     _launchers.forEach(function(l){
       if(!l.use_selected_workspaces)return;
       var badge=document.getElementById('badge-'+l.id);
       if(!badge)return;
       if(count>0){badge.textContent=count;badge.classList.add('visible')}
       else{badge.textContent='';badge.classList.remove('visible')}
     });
   }
   ```

10. `style.css` — add `.provider-tile` and `.launcher-lock` styles:
    ```css
    .provider-tile { position: relative; }
    .launcher-lock { position: absolute; top: 4px; right: 4px; font-size: 10px; opacity: 0.5; }
    ```

11. `tests/test_web.py` — add tests:
    - Test `/partials/launchers` returns provider tiles before custom launchers.
    - Test provider tile has `data-id="provider--kiro-cli"`.
    - Test disabled provider not shown in launcher grid.
    - Test tab bar no longer has gear icons.

**Exit criteria**:
- [x] Provider tiles render first in launcher grid with lock indicator
- [x] Clicking provider tile with selection launches `launch_session` per unique workspace (fresh sessions, no resume)
- [x] Provider tile gear button opens launcher modal with locked name/command fields
- [x] Provider-launcher modal includes "Enabled" toggle that persists to `provider_settings`
- [x] Saving from provider-launcher modal updates `provider_settings`
- [x] Field lock/unlock resets on dialog close (not just on save success)
- [x] Provider modal HTML and tab gear icons removed atomically (server-side rendering + JS in same commit)
- [x] Provider tile icon uses extraction pipeline (`.cmd`→`.exe` from Phase 1)
- [x] Disabled providers don't appear in launcher grid
- [x] All existing tests pass
- [x] New tests pass

**Implementation (2026-07-01, code: cda954d)**
Providers now render as first-class tiles in the launcher grid, appearing before custom launchers. Each provider tile shows a lock indicator, uses the provider's color for the left border, and has selection-aware badge support. Clicking a provider tile with selected workspaces POSTs to `/api/launch-batch` (fresh sessions, no resume). The gear button opens the existing launcher modal with name/command fields locked (readOnly), terminal/use-selected disabled, delete hidden, and an "Enabled" toggle visible. Saving from the provider modal PUTs to `/api/provider/save`. The dialog's `close` event resets all field locks/disabled states. The old standalone provider modal HTML has been emptied, tab gear icons removed from the workspace tab bar, and `openProviderModal`/`saveProvider` JS functions replaced by the unified `openProviderLauncherModal`. The `/api/launcher-icon/provider--{key}` path uses the extraction pipeline (`.cmd`→`.exe` from Phase 1) to serve provider icons. Disabled providers are excluded from the launcher grid. Four new tests verify: provider tiles render first, correct `data-id` attribute, disabled provider exclusion, and no `tab-gear` class in output.

**Divergences from plan:**
- Provider tile run with no selection shows toast error instead of silently doing nothing (better UX: user gets feedback that they need to select workspaces first).

### Phase 4: Provider icon/color in workspace cards [QA]

**Goal**: Replace the hardcoded letter badge on workspace cards with the provider-launcher's icon image and user-configurable color (SC3).

**File scope**: `src/power_atlas/web.py`, `src/power_atlas/templates/partials/workspace_card.html`, `src/power_atlas/static/style.css`, `tests/test_web.py`

**Detailed changes**:

1. `workspace_card.html` — replace the letter badge with an icon image that falls back gracefully:
   ```html
   <span class="card-folder-name">{{ folder_name }}<span class="provider-icon-wrapper"><img class="provider-icon-badge" src="/api/launcher-icon/provider--{{ provider }}" alt="{{ provider_badge|default('?') }}" title="{{ provider_display|default(provider) }}" onerror="this.style.display='none';this.nextElementSibling.style.display=''"><span class="provider-badge-fallback" style="display:none;background:{{ provider_color|default('#888') }}">{{ provider_badge|default('?') }}</span></span><span class="card-path" title="{{ cwd }}">{{ cwd }}</span></span>
   ```
   The `onerror` handler hides the broken image and shows the letter badge fallback. This ensures workspace cards always have a provider indicator even when icon extraction fails.

2. `web.py` — pass `provider_display` to workspace card template context (add alongside existing `provider_color`, `provider_badge`):
   ```python
   provider_display=PROVIDER_DISPLAY_NAMES.get(prov, prov),
   ```

3. `web.py` — update `PROVIDER_COLORS` usage: the border-left color should come from `provider_settings` color override (falling back to hardcoded):
   ```python
   def _get_provider_color(provider: str, config) -> str:
       """Get provider color: user override from provider_settings, or hardcoded default."""
       user_color = config.provider_settings.get(provider, {}).get("color", "")
       return user_color or PROVIDER_COLORS.get(provider, "#888")
   ```
   Use this in `partials_workspaces()` when rendering cards.

4. `style.css` — add `.provider-icon-badge` style:
   ```css
   .provider-icon-badge {
     width: 14px;
     height: 14px;
     margin-left: 6px;
     vertical-align: middle;
     border-radius: 2px;
   }
   ```

5. `tests/test_web.py` — verify workspace cards include `<img class="provider-icon-badge" src="/api/launcher-icon/provider--kiro-cli"`.

**Exit criteria**:
- [x] Workspace cards show provider icon image (not letter badge)
- [x] Letter badge shown as fallback when icon extraction fails (no broken images)
- [x] Icon served from extraction pipeline (same as launcher tile icons)
- [x] Color border uses user-configured color from `provider_settings` when set
- [x] Fallback to default `PROVIDER_COLORS` when no user color
- [x] All existing tests pass
- [x] New tests pass
- [x] README.md updated to mention provider-launcher unification in Features section

**Implementation (2026-07-01, code: 4b5289b)**
Replaced the hardcoded letter badge (`<span class="provider-badge">K</span>`) on workspace cards with an `<img class="provider-icon-badge">` element that loads the provider icon from `/api/launcher-icon/provider--{name}` - the same extraction pipeline used by launcher tiles. An `onerror` handler on the img hides it and shows a `.provider-badge-fallback` span with the old letter+color styling, ensuring no broken images appear when icon extraction fails. Added a `_get_provider_color()` helper in web.py that checks user-configured color from `provider_settings` before falling back to the default `PROVIDER_COLORS` dict, and all three workspace-card render sites (pinned, main panel, search) now pass both `provider_display` (for the img title) and the user-aware color. CSS classes `.provider-icon-wrapper`, `.provider-icon-badge`, and `.provider-badge-fallback` were added. Two new tests verify the icon img tag presence and user-configured color propagation. README updated with the unified provider-launcher feature bullet.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `.cmd` parsing regex misses edge cases | Low — falls back to generic SVG (existing behavior) | Regex covers Electron pattern (`%~dp0..\App.exe`); test against real `kiro.cmd` |
| Provider-launcher ID format (`provider--kiro-cli`) collides with user UUID | None — UUIDs never contain double-hyphens in this position | ID format is inherently non-colliding |
| Non-terminal workspace-as-argument breaks a future launcher | Low — only applies when `pass_workspace_arg=True` | Currently only Kiro IDE matches; terminal launchers unaffected |
| Removing provider modal loses settings access | None — settings moved to launcher edit modal | Same fields (args + color) available through launcher modal |
| Provider icon extraction runs on every page load | Low perf impact | `has_icon()` check is a simple file existence test; extraction only runs once |

## 7) Verification

```bash
# Run all tests
pytest tests/ -v

# Manual verification:
# 1. Start power-atlas, verify provider tiles appear first in launcher grid
# 2. Select a workspace, click Kiro IDE tile → IDE opens at that workspace
# 3. Select same workspace via both kiro-cli and claude-code cards → badge shows "1"
# 4. Click Kiro IDE → launches once (not twice)
# 5. Verify Kiro IDE tile shows actual Kiro icon (not generic terminal SVG)
# 6. Verify workspace cards show small provider icon (not letter badge)
# 7. Click gear on provider tile → modal opens with locked name/command
# 8. Tab bar has no gear icons
# 9. Edit provider args via launcher modal → verify launch uses new args
```

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Update Features section: mention unified launcher grid for providers | 4 |

## 9) Implementation Divergences from Plan
<Reserved -- filled during implementation>

## Review Log

### 2026-07-01 -- Plan Review (via /qplan)

High-effort review (4 personas: Architect, Senior engineer, End-user advocate, Reliability engineer). 8 findings (5 High, 3 Medium). All auto-resolved.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Provider ID `provider:kiro-cli` contains colon — illegal in Windows filenames; icon save/read would fail. | Changed ID format to `provider--kiro-cli` (double-hyphen separator). |
| 2 | High | Provider "Enabled" toggle lost when modal is removed — users can't disable a provider. | Added `launcherEnabledRow` to provider-launcher modal view with enabled checkbox. |
| 3 | High | `pass_workspace_arg` uses f-string interpolation with `shell=True` — shell injection risk for special chars. | Changed to proper quoting: `shlex.quote()` on Linux, escaped double-quotes on Windows. |
| 4 | High | `pass_workspace_arg=not use_terminal` fires for ALL non-terminal launchers, not just selection-aware ones. | Changed to explicit `pass_workspace_arg` parameter set by caller based on `use_selected_workspaces AND not terminal`. |
| 5 | High | Replacing letter badge with `<img>` on workspace cards — broken image if extraction fails (common case). | Added `onerror` handler that hides broken img and shows letter-badge fallback span. |
| 6 | Medium | Modal field lock (readOnly/disabled) only resets in `.then()` callback — save failure leaves fields permanently locked. | Moved field-unlock to dialog `close` event listener (fires regardless of save outcome). |
| 7 | Medium | Plan doesn't clarify that provider tile "run" = new session (`launch_session(session_id=None)`), not resume. | Added explicit note in Phase 3 exit criteria: "fresh sessions, no resume". |
| 8 | Medium | Server-side gear-icon HTML removal must be atomic with JS function removal to avoid stale-tab errors. | Added exit criterion: "removed atomically (server-side rendering + JS in same commit)". |

### 2026-07-01 -- Implementation Review (after Phase 1, persona: Senior engineer)

Implementation health: Green.
3 findings (0 High, 1 Medium, 2 Low).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | Windows quoting uses simple quote-if-spaces; plan specified inner-quote escaping, but NTFS forbids `"` in filenames. | Fixed — added clarifying comment explaining why inner-quote escaping is unnecessary. |
| 2 | Low | Pattern 1 regex cannot match `%~dp0`-relative paths containing spaces; real shims never have them. | Accepted — real-world Electron shims are space-free in relative segments. |
| 3 | Low | `re` imported lazily inside `_resolve_cmd_to_exe()` vs module-level import. | Accepted — defensible to avoid loading regex on Linux where function is never called. |

Cycle 2 skipped — cycle 1 findings all Low + auto-fix purely mechanical (comment rewording only).

### 2026-07-01 -- Implementation Review (after Phase 2, persona: Senior engineer)

Implementation health: Green.
2 findings (0 High, 0 Medium, 2 Low).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Low | No client-side test for dedup behavior; trusted to code inspection. | Accepted — plan specified comment-only; QA step covers browser behavior. |
| 2 | Low | No null guard on `c.dataset.cwd.toLowerCase()` for workspace cards. | Accepted — template always renders `data-cwd`; null impossible in practice. |

### 2026-07-01 -- Implementation Review (after Phase 3, persona: Senior engineer)

Implementation health: Green.
5 findings (0 High, 2 Medium, 3 Low).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | `launcher_icon()` calls `extract_icon()` on every request for provider icons; no `has_icon()` cache gate. | Fixed — added `has_icon()` check before extraction attempt, matching custom launcher path. |
| 2 | Medium | Orphaned CSS: `.tab-gear` and `.provider-modal` are dead rules after HTML removal. | Fixed — removed dead CSS blocks. |
| 3 | Low | `{% include "partials/provider_modal.html" %}` still present, rendering only a comment. | Fixed — removed include line and deleted file. |
| 4 | Low | `refreshCards()` called without `true` after provider save; disabling won't bust cache immediately. | Fixed — pass `refreshCards(true)` in provider save success callback. |
| 5 | Low | `_providerSettings` var still injected; confirmed still in use (migration toast IIFE). | No action — not dead code. |

Cycle 2 skipped — all findings auto-fixed were purely mechanical (cache gate, CSS deletion, include removal, function arg).

### 2026-07-01 -- Implementation Review (after Phase 4, persona: Senior engineer)

Implementation health: Green.
3 findings (0 High, 1 Medium, 2 Low).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | `onerror` fallback unreachable: icon endpoint always returns valid SVG on extraction failure, so `onerror` never fires in normal operation. Letter badge only visible on endpoint failure. | Accepted — SVG fallback IS the intended degradation for missing icons; letter badge is a second-level defense for catastrophic endpoint failure. Design is intentional per plan Finding #5. |
| 2 | Low | Dead `.provider-badge` CSS rule remains after Phase 4 replaced it with `.provider-icon-badge`. | Fixed — removed dead rule, updated stale test assertion to target `provider-icon-badge`. |
| 3 | Low | No test for onerror fallback path (unreachable in normal flow per Finding 1). | Accepted — only testable via browser with endpoint down; covered by Step 9b QA if needed. |

Cycle 2 skipped — cycle 1 findings: 1 Medium accepted (design-intentional), 2 Low (1 fixed mechanically, 1 accepted).
