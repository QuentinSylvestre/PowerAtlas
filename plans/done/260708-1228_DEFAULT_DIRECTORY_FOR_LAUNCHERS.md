# Default Directory for Launchers

> **Date**: 2026-07-08
> **Status**: Complete
> **Last Updated**: 2026-07-08 12:28
> **Scope**: Add global and per-provider default directory settings so providers can launch without workspace selection

---

## Intent

### Problem statement & desired outcomes

Currently, terminal-based providers (kiro-cli, claude-code) cannot be launched from their tile without first selecting a workspace — the UI shows "Select workspaces first". Non-terminal providers (kiro-ide) launch without a workspace but pass no directory argument. There is no way to configure a default working directory for any provider.

**Desired outcomes:**
- Users can click any provider tile and have it launch into a configured default directory when no workspaces are selected
- Per-provider directory overrides exist so different providers can target different directories
- The global default is editable from the Launch Profile modal; per-provider overrides are editable from the existing provider settings modal

### Success criteria

1. A `default_directory` field exists at the top-level Config and persists in `config.toml`
2. Per-provider `default_directory` field exists in `provider_settings` and persists in `config.toml`
3. The provider settings modal (gear icon on provider tile) shows an editable "Working directory" field (currently read-only) populated with the per-provider default_directory
4. The launch profile modal shows the global `default_directory` field
5. Clicking a provider tile without workspace selection launches into the configured directory (per-provider > global > error fallback chain)
6. Non-terminal providers (kiro-ide) receive the default directory as workspace argument when configured
7. Existing tests updated and new tests pass

### Scope boundaries & non-goals

**In scope:**
- Global `default_directory` on Config dataclass
- Per-provider `default_directory` in provider_settings dict
- UI: editable working directory in provider settings modal
- UI: global default directory field in launch profile modal
- Fallback chain: selected workspaces > per-provider default_directory > global default_directory > error
- Path validation at launch time (not save time)

**Non-goals:**
- Directory picker/browser widget (plain text input is sufficient)
- Per-workspace or per-session default directories
- Validation of directory existence at save time (launch-time validation matches existing behavior)
- Changes to session resume behavior (resume always uses session's original workspace)

---

## Discovery

### Existing patterns & constraints

- `provider_settings` is `dict[str, dict]` with no schema enforcement — inner dict currently stores `{default_args, color, enabled}` (config.py:52, web.py:592-596)
- Custom launchers already have a `cwd` field that persists and is editable in the modal (web.py:1060, launcher_modal.html)
- The launcher modal has an existing "Working directory" input (`launcherCwd`) that is forced to empty+readOnly for providers (index.html:162)
- `launch_session()` accepts `cwd` as first parameter; `"."` is the sentinel for "no workspace" (launcher.py:125)
- JS `_providerTerminal` dict gates the "no selection" behavior — terminal providers show error, non-terminal send `workspace:'.'` (index.html:170)
- `_SETTING_TYPES` whitelist at web.py:732 controls which settings are saveable via the generic save endpoint
- Launch profile modal currently edits `LaunchProfile` fields (id, name, terminal_command, wt_profile) — the global default_directory is a Config-level field displayed in this modal but not stored on the profile
- JS-side display maps must mirror Python-side dicts (project memory constraint)

### Risks & mitigations

1. **Stale directory in config** — User saves a path that later gets deleted. Mitigation: launch-time validation produces clear "Folder not found" error (same as existing workspace/launcher behavior at launcher.py:125).
2. **Global field in profile modal but not on profile** — Could confuse users expecting it to travel with profile export. Mitigation: acceptable for a personal desktop tool; label clearly in UI.
3. **JS `_providerSettings` cache must include new field** — The client-side cache updates after save (index.html:174 `_providerSettings[key]=payload`). Mitigation: include `default_directory` in the save payload sent from JS.

### Resolved decisions

- Change-1: Clicking a provider tile without selection launches into default directory (per-provider > global > error) — A: yes — Decision: Implement fallback chain in `runLauncherById` JS function and backend launch endpoints
- Change-2: Empty per-provider default_directory means "inherit from global" — A: yes — Decision: Empty string = fall through to global default_directory
- Change-3: Default directory applies to non-terminal providers too (kiro-ide gets it as workspace arg) — A: yes — Decision: All providers use the same fallback chain regardless of terminal/non-terminal
- Change-4: Global default_directory editable in the Launch Profile modal — A: option A — Decision: Add a text input for global default_directory in the launch profile modal
- Change-5: Global default_directory is a single top-level Config value, not per-profile — A: ok — Decision: Add `default_directory: str = ""` to Config dataclass, display in profile modal but save/load independently from LaunchProfile

### Open items

None.

### Assumptions (unconfirmed)

- UX: When a default directory is configured, the "Select workspaces first" error disappears for tile clicks — provider just launches. No confirmation needed.
- Edge case: If both global and per-provider are set to nonexistent paths, user gets "Folder not found" at launch time (existing error path).
- Path validation: raw filesystem path stored, validated only at launch time (not save time), matching existing custom launcher behavior.

### Recommended approach

1. Add `default_directory: str = ""` to `Config` dataclass
2. Add `default_directory` key handling to provider settings GET/POST endpoints
3. Un-read-only the `launcherCwd` field for providers in `openProviderLauncherModal`; include in save payload
4. Add `default_directory` field to launch profile modal UI (reads/writes from Config, not LaunchProfile)
5. Modify `runLauncherById` JS to check per-provider then global default_directory before showing error
6. Modify backend launch endpoints to apply fallback chain when workspace is `"."` or empty
7. Update tests for new config field, provider settings, and launch behavior

---

## Context

The launch subsystem already supports per-custom-launcher `cwd` fields — the infrastructure for storing and using a directory exists. The gap is that built-in provider launchers (kiro-cli, claude-code, kiro-ide) cannot have a configured directory. The change adds a two-tier fallback (per-provider > global) following the same pattern as the existing `default_args` layered configuration.

## Files to modify

| File | Change |
|---|---|
| `src/power_atlas/config.py` | Add `default_directory: str = ""` field to `Config` dataclass |
| `src/power_atlas/web.py` | Update provider settings GET/POST, `/api/settings`, `_SETTING_TYPES`, launch profile save endpoint, launch endpoints fallback logic |
| `src/power_atlas/templates/index.html` | Modify `openProviderLauncherModal`, `saveLauncher`, `runLauncherById` JS; add global default_directory to profile modal JS |
| `src/power_atlas/templates/partials/launch_profile_modal.html` | Add "Default directory" input field |
| `tests/test_config.py` | Add round-trip tests for `default_directory` |
| `tests/test_web.py` | Update provider settings tests, add launch fallback tests |
| `README.md` | Add `default_directory` to config example |

## External Dependencies

None — code-only change.

## Rollout / Migration / Cleanup

None — additive field with empty-string default. Existing configs work unchanged.

## Step-by-step

### 1. Add `default_directory` to Config dataclass [QA]

**config.py:42-52** — Add field after `peek_hotkey`:

```python
@dataclass
class Config:
    port: int = 0
    peek_hotkey: str = "ctrl+shift+z"
    default_directory: str = ""  # Global fallback for provider launches without workspace selection
    active_launch_profile: str = "default"
    launch_profiles: list[LaunchProfile] = field(default_factory=lambda: [LaunchProfile()])
    pinned_folders: list[str] = field(default_factory=list)
    pinned_sessions: list[str] = field(default_factory=list)
    workspace_icons: dict[str, str] = field(default_factory=dict)
    custom_launchers: list[dict] = field(default_factory=list)
    provider_settings: dict[str, dict] = field(default_factory=dict)
```

Add sanitization in `load_config()` after existing field sanitization (~line 135):

```python
# Sanitize default_directory: must be string, strip control chars
if not isinstance(config.default_directory, str):
    config.default_directory = ""
config.default_directory = _strip_control_chars(config.default_directory).strip()
```

### 2. Update provider settings endpoints [QA]

**web.py:565-570** — GET endpoint, update default dict:

```python
@app.get("/api/provider/{key}")
async def get_provider_settings(key: str):
    if key not in data.PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    config = load_config()
    settings = config.provider_settings.get(key, {"default_args": "", "color": "", "enabled": True, "default_directory": ""})
    # Ensure default_directory key exists for legacy entries
    settings.setdefault("default_directory", "")
    return {"provider": key, **settings}
```

**web.py:574-598** — POST endpoint, accept and validate `default_directory`:

```python
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
```

**web.py:170** — Index route template context, add `default_directory`:

```python
return templates.TemplateResponse(request, "index.html", {
    "port": config.port,
    "active_launch_profile": profile,
    "launch_profiles": [asdict(p) for p in config.launch_profiles],
    "autostart": autostart.is_enabled(),
    "launchers": config.custom_launchers,
    "peek_hotkey": config.peek_hotkey,
    "default_directory": config.default_directory,  # NEW — used by _globalDefaultDirectory JS var
    "provider_settings": config.provider_settings,
    "autostart_label": "Start at login" if sys.platform != "win32" else "Start with Windows",
})
```

**web.py:537-553** — GET `/api/settings`, include `default_directory`:

```python
return {
    "active_launch_profile": config.active_launch_profile,
    "launch_profiles": [asdict(p) for p in config.launch_profiles],
    "peek_hotkey": config.peek_hotkey,
    "port": config.port,
    "default_directory": config.default_directory,  # NEW
    "provider_settings": config.provider_settings,
    "custom_launchers": config.custom_launchers,
    "autostart": autostart_enabled,
}
```

**web.py:732-737** — Add to `_SETTING_TYPES` whitelist:

```python
_SETTING_TYPES: dict[str, type] = {
    "port": int,
    "peek_hotkey": str,
    "default_directory": str,  # NEW
    "pinned_folders": list,
    "pinned_sessions": list,
}
```

Add string-specific validation in `save_setting` (after the type check, before setattr):

```python
# String-specific validation (applies to peek_hotkey, default_directory)
if expected_type is str:
    if len(value) > 512:
        return {"ok": False, "error": f"{key} too long (max 512 chars)"}
    if any(ord(ch) < 0x20 for ch in value):
        return {"ok": False, "error": f"{key} contains invalid control characters"}
```

### 3. Update provider settings modal JS [QA]

**index.html:162** — `openProviderLauncherModal`: make cwd editable and populate from settings:

```javascript
function openProviderLauncherModal(id){
  var key=id.slice('provider--'.length);
  fetch('/api/provider/'+encodeURIComponent(key)).then(function(r){return r.json()}).then(function(d){
    var m=document.getElementById('launcherModal');
    document.getElementById('launcherId').value=id;
    document.getElementById('launcherName').value=d.provider||key;
    document.getElementById('launcherName').readOnly=true;
    document.getElementById('launcherCommand').value=_providerBinaryDisplay[key]||key;
    document.getElementById('launcherCommand').readOnly=true;
    document.getElementById('launcherArgs').value=d.default_args||'';
    document.getElementById('launcherCwd').value=d.default_directory||'';  // CHANGED: populate from settings
    document.getElementById('launcherCwd').readOnly=false;                  // CHANGED: make editable
    // ... rest unchanged
  })
}
```

**index.html:174** — `saveLauncher` provider branch, include `default_directory`:

```javascript
if(id.startsWith('provider--')){
  var key=id.slice('provider--'.length);
  var payload={
    provider:key,
    default_args:document.getElementById('launcherArgs').value,
    color:document.getElementById('launcherColor').value,
    enabled:document.getElementById('launcherEnabled').checked,
    default_directory:document.getElementById('launcherCwd').value  // NEW
  };
  fetch('/api/provider/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(function(r){return r.text()}).then(function(t){
    showToast(t);
    document.getElementById('launcherModal').close();
    _providerSettings[key]=payload;  // Cache includes default_directory
    // ... refresh tiles and cards
  })
}
```

### 4. Update `runLauncherById` fallback logic [QA]

The fallback chain is implemented **purely on the JS side** — the frontend resolves the directory before sending it to `/api/launch-batch`. The backend launch endpoints (`/api/launch`, `/api/launch-batch`, `/api/new-session`) remain unchanged because they already accept any `workspace` string; the caller is responsible for resolving which directory to send.

**index.html:170** — Replace the terminal-provider error with fallback chain:

```javascript
function runLauncherById(id){
  if(id.startsWith('provider--')){
    var key=id.slice('provider--'.length);
    var selected=getSelectedWorkspaceCwds();
    if(selected.length>0){
      var sessions=selected.map(function(w){return{workspace:w,provider:key}});
      fetch('/api/launch-batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sessions:sessions})}).then(function(r){return r.text()}).then(showToast)
    }else{
      // Fallback chain: per-provider default_directory > global default_directory > error
      var provDir=(_providerSettings[key]||{}).default_directory||'';
      var globalDir=window._globalDefaultDirectory||'';
      var fallbackDir=provDir||globalDir;
      if(fallbackDir){
        fetch('/api/launch-batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sessions:[{workspace:fallbackDir,provider:key}]})}).then(function(r){return r.text()}).then(showToast)
      }else if(!_providerTerminal[key]){
        fetch('/api/launch-batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sessions:[{workspace:'.',provider:key}]})}).then(function(r){return r.text()}).then(showToast)
      }else{
        showToast('<div class="toast toast-error">Select workspaces first, or click \u2699\ufe0f to set a default directory<button class="toast-dismiss" onclick="this.parentElement.remove()">\u00d7</button></div>')
      }
    }
    return
  }
  // ... custom launcher logic unchanged
}
```

**index.html** — Initialize `_globalDefaultDirectory` from template context (near `_providerSettings`):

```javascript
var _globalDefaultDirectory={{default_directory|tojson}};
```

**index.html** — Update `refreshSettings` to refresh the global default:

```javascript
// Inside refreshSettings() callback, add:
if(d.default_directory!==undefined) window._globalDefaultDirectory=d.default_directory;
```

### 5. Add global default_directory to launch profile modal [QA]

**templates/partials/launch_profile_modal.html** — Add input after WT Profile:

```html
<label class="form-label">Default directory <span class="form-hint">(Fallback when no workspace is selected. Per-provider overrides this.)</span>
  <input type="text" id="profileDefaultDir" maxlength="512">
</label>
```

**index.html** — `editProfile` function: populate from global config:

```javascript
document.getElementById('profileDefaultDir').value=window._globalDefaultDirectory||'';
```

**index.html** — `saveProfile` function: save `default_directory` via separate endpoint (it's a Config field, not a LaunchProfile field):

```javascript
// After the profile save fetch succeeds, also save the global default_directory:
var newDefaultDir=document.getElementById('profileDefaultDir').value;
if(newDefaultDir!==window._globalDefaultDirectory){
  fetch('/api/save-setting',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:'default_directory',value:newDefaultDir})}).then(function(r){return r.json()}).then(function(d){
    if(d.ok) window._globalDefaultDirectory=newDefaultDir;
    else showToast('<div class="toast toast-error">Failed to save default directory<button class="toast-dismiss" onclick="this.parentElement.remove()">\u00d7</button></div>');
  }).catch(function(){
    showToast('<div class="toast toast-error">Failed to save default directory<button class="toast-dismiss" onclick="this.parentElement.remove()">\u00d7</button></div>');
  });
}
```

**index.html** — Inside `refreshSettings` callback, add after `_providerSettings` update:

```javascript
if(d.default_directory!==undefined) window._globalDefaultDirectory=d.default_directory;
```

### 6. Update tests

**tests/test_config.py** — Add round-trip test:

```python
def test_default_directory_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    c = config.Config(default_directory="/home/user/projects")
    config.save_config(c)
    loaded = config.load_config()
    assert loaded.default_directory == "/home/user/projects"


def test_default_directory_default_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    c = config.load_config()
    assert c.default_directory == ""
```

**tests/test_web.py** — Update provider settings tests:

```python
def test_save_provider_settings_with_default_directory(client):
    resp = client.post("/api/provider/save", json={
        "provider": "kiro-cli",
        "default_args": "-a",
        "color": "#ff0000",
        "enabled": True,
        "default_directory": "/home/user/work",
    })
    assert resp.status_code == 200
    config = load_config()
    assert config.provider_settings["kiro-cli"]["default_directory"] == "/home/user/work"


def test_get_provider_settings_includes_default_directory(client):
    resp = client.get("/api/provider/kiro-cli")
    data = resp.json()
    assert "default_directory" in data
    assert data["default_directory"] == ""


def test_settings_includes_default_directory(client):
    resp = client.get("/api/settings")
    data = resp.json()
    assert "default_directory" in data
```

### 7. Update README.md config example

Add `default_directory` to the Configuration section:

```toml
default_directory = ""  # Global fallback directory for provider launches without workspace selection

[provider_settings.kiro-cli]
default_args = "-a"
color = ""
enabled = true
default_directory = ""  # Per-provider override (empty = use global)
```

## Verification

1. `pytest tests/test_config.py tests/test_web.py -v` — all pass including new tests
2. Manual: start app, open provider settings modal → Working directory field is editable
3. Manual: set a per-provider default_directory, click tile without selection → launches into that directory
4. Manual: clear per-provider, set global in profile modal → provider falls back to global
5. Manual: clear both → terminal providers show updated error message, non-terminal launches with `'.'`
6. Confirm `config.toml` contains both `default_directory` (top-level) and nested in `[provider_settings.*]`

## Documentation updates

- README.md: Add `default_directory` to config example (Step 7 above)

---

## Review Log

### 2026-07-08 — Plan Review (via /qplan, High effort, 4 personas)

8 findings (2 High, 4 Medium, 2 Low). All auto-resolved.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `index.html` references `{{default_directory|tojson}}` but index route context omits it | Resolved — added index route context snippet in Step 2 |
| 2 | High | JS cache `_providerSettings[key]=payload` would lack `default_directory` after save | Resolved — explicit save callback with full payload shown in Step 3 |
| 3 | Medium | `save-setting` endpoint has no string-length/control-char validation | Resolved — added string validation snippet in Step 2 |
| 4 | Medium | `saveProfile` second fetch has no error handling for failed `default_directory` save | Resolved — added `.catch()` with toast in Step 5 |
| 5 | Medium | `refreshSettings` callback never updates `_globalDefaultDirectory` | Resolved — explicit snippet added in Step 4 |
| 6 | Medium | Error message "Select workspaces first" not actionable for new feature | Resolved — message now references gear icon for configuration |
| 7 | Low | Backend fallback mentioned in plan title but snippets show JS-only implementation | Resolved — explicit note clarifying fallback is JS-side only |
| 8 | Low | Profile modal discoverability of "default directory" may confuse users | Noted — hint text already included in the HTML label; acceptable for v1 |

Personas: Senior engineer, Architect, Reliability engineer, End-user advocate.



---

## Implementation Notes

Implementation (2026-07-08)

All 7 plan steps implemented in one pass:
1. Config.py: Added `default_directory: str = ""` to Config dataclass with sanitization (strip control chars + trim whitespace) in `load_config()`.
2. Web.py: Updated GET `/api/provider/{key}` to return `default_directory` (with `setdefault` for legacy entries), POST `/api/provider/save` to validate and store it (max 512 chars, no control chars), GET `/api/settings` to include it, `_SETTING_TYPES` whitelist expanded, and `save_setting` gained string-specific validation for all str-typed settings.
3. Index.html: Added `_globalDefaultDirectory` JS variable from template context, made `launcherCwd` editable for providers in `openProviderLauncherModal`, included `default_directory` with `.trim()` in `saveLauncher` payload, implemented fallback chain in `runLauncherById` (per-provider > global > non-terminal '.' > error), updated `refreshSettings` to sync `_globalDefaultDirectory`, and updated `editProfile`/`saveProfile` for global default_directory field in profile modal.
4. Launch profile modal: Added "Default directory" input with maxlength=512 and hint text.
5. Tests: 4 new config tests (round-trip, default empty, control chars sanitized, whitespace stripped) + 9 new web tests (provider save/get with default_directory, settings includes it, save-setting works, validation rejects too-long and control chars). 1 existing test updated (expected keys set).
6. README: Config example updated with `default_directory` at global and per-provider levels.

Auto-fixes applied from review:
- Fixed race condition in `saveProfile` (sequenced saves with `.then()` chain)
- Added client-side `.trim()` on provider `default_directory` before sending/caching
- Added `maxlength="512"` to `launcherCwd` input in launcher modal
- Added missing test for control chars in provider directory save

## Implementation Divergences from Plan

None — implementation follows the plan exactly.

## Review Log

### 2026-07-08 -- Implementation Review (after Phase 1, personas: Senior engineer, End-user advocate, Reliability engineer, Security auditor)

Implementation health: Green.
10 findings (0 High, 2 Medium, 6 Low, 2 Info). High-effort review (4 personas).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | `saveProfile` fires default_directory save concurrently causing race with settings refresh | Fixed — sequenced saves with `.then()` chain before fetching settings |
| 2 | Medium | JS cache stores raw form values; untrimmed paths used verbatim by fallback chain | Fixed — added `.trim()` on `launcherCwd` value before sending |
| 3 | Low | No test for control characters in per-provider `default_directory` save endpoint | Fixed — added `test_save_provider_directory_control_chars` |
| 4 | Low | `launcherCwd` input has no `maxlength` attribute (backend enforces 512 but no client feedback) | Fixed — added `maxlength="512"` to the input |
| 5 | Low | No hint text on `launcherCwd` for provider use explaining the field's purpose | Noted — label text "Working directory" is sufficient for power-user audience |
| 6 | Low | Error message "click gear" ambiguous about which gear icon to click | Noted — acceptable for v1; both gears lead to relevant settings |
| 7 | Low | `profileDefaultDir` shows same global value regardless of which profile is edited | Noted — by-design per plan; hint text clarifies |
| 8 | Low | No observability for which directory was chosen in fallback chain | Noted — low priority for personal desktop tool |
| 9 | Low | `refreshSettings` uses `!==undefined` check; `!=null` would be more robust | Noted — no bug due to `||''` in consumer; cosmetic |
| 10 | Info | XSS/CSRF/path-traversal protections adequate for local desktop app | No action needed |

Cycle 2 skipped — remaining findings all Low + auto-fixes purely mechanical.

QA verification: PASS (6 API surfaces verified, 10 HTML output checks passed).
