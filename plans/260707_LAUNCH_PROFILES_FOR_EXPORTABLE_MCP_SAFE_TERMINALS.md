# Launch Profiles for Exportable MCP-Safe Terminals

> **Date**: 2026-07-07
> **Status**: Draft  <!-- Exploring -> Draft -> In Progress -> Complete -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Redesign PowerAtlas terminal-launch configuration around global launch profiles so MCP-safe Windows Terminal launches can work across machines with different profiles, shells, and process names.
> **Estimated effort**: ~2-4 days (5 phases; persisted config shape + launcher and Web UI integration)

---

## Intent

### Problem statement & desired outcomes
PowerAtlas now has an MCP-safe Windows Terminal launch path for `kiro-cli` and `claude-code`, but that path is tuned to one environment: Windows Terminal profile name `PowerShell`, child shell process `pwsh.exe`, helper runner `pwsh`, and fixed helper/attach timeouts. The older terminal configuration is also a single free-form `terminal_command`, which is too narrow for exporting this behavior to machines with different Windows Terminal profiles or shell topologies.

Desired outcome: replace the one-off terminal command model with a broader launch-profile system. A launch profile should describe the terminal command plus optional Windows Terminal/MCP-safe fields, and PowerAtlas should use the active profile for terminal launches. The default experience should still work for a new user, while this local install can be migrated once without a long backwards-compatibility bridge.

The UI should expose launch profiles as normal settings rather than a hidden config tweak. Windows CLI providers should keep using the MCP-safe topology when enabled and supported, but the hard-coded assumptions should become profile fields. If the MCP-safe helper fails and the old direct launch fallback succeeds, the launch result should preserve that success while surfacing a visible warning.

### Success criteria
- Config supports named global launch profiles plus one active profile, replacing the old top-level `terminal_command` as the primary model.
- Existing local config can be migrated once manually or by a narrowly-scoped local migration; the application does not need to preserve long-term legacy `terminal_command` compatibility for future users.
- All terminal-based launches use the active launch profile by default, including provider launches and custom launchers. Kiro IDE and other non-terminal providers remain unchanged.
- The Windows MCP-safe helper becomes profile-driven: Windows Terminal profile name, expected shell process name, helper runner, attach timeout, helper timeout, and enable/disable state are configurable through the active launch profile.
- MCP-safe launch remains Windows-only and provider-gated by default for `kiro-cli` and `claude-code`; unsupported platforms/providers use the normal launch command path.
- Direct Windows Terminal fallback still exists. When helper failure falls back successfully, `LaunchResult` carries explicit warning/fallback metadata instead of hiding the degraded path in a generic error string.
- The settings UI has a dedicated launch-profile management surface reachable from the compact topbar/settings area. There is no dedicated in-app test-launch-profile action; users can validate profiles through normal launcher/provider launches.
- README documents the new launch-profile behavior and the Windows MCP-safe profile fields at the user-facing level.
- Existing tests are updated in place to cover profile config load/save, launch routing, command rendering, fallback warning metadata, and custom-template behavior. Do not create new test files unless later planning identifies a regression that cannot fit existing files.

### Scope boundaries & non-goals
In scope:
- Config schema redesign for launch profiles and the active profile.
- One-time migration of this install's config away from `terminal_command`.
- Launcher API/internal plumbing needed to pass an active profile into provider and custom launches.
- UI changes required to manage launch profiles and active-profile selection.
- README and existing test updates for user-visible behavior.

Out of scope:
- A long-lived compatibility bridge for old top-level `terminal_command` configs.
- Per-custom-launcher or per-provider launch-profile selection; v1 uses one active global launch profile.
- A dedicated test-profile button or endpoint.
- Making MCP-safe injection the default for every terminal provider; v1 remains gated to known supported CLI providers.
- Changing Kiro IDE launch behavior or other non-terminal provider behavior.
- Clipboard, SendKeys, or generic keyboard-injection approaches for terminal input.
- Guaranteeing Windows Terminal process-tree behavior beyond the known empirical topology; profile fields and fallback warnings are the mitigation.

---

## 1) Current State
- `Config` has a top-level `terminal_command: str = ""` at `src/power_atlas/config.py:32`. The loader accepts only dataclass fields at `config.py:51-61`, so replacing this field can drop old values unless backup or one-shot migration happens first.
- `load_config()` ignores unknown keys (`config.py:53-55`) and does shallow type checks (`config.py:56-61`), so nested launch-profile data needs explicit normalization.
- `save_config()` writes `asdict(config)` at `config.py:91`, which supports nested dataclasses if the profile model is typed.
- Web still passes `terminal_command` into the template at `src/power_atlas/web.py:160-169`, exposes it in `/api/settings` at `web.py:521-535`, and allowlists it in `_SETTING_TYPES` at `web.py:574-580`.
- Provider launches pass `config.terminal_command` as `terminal_override` at `web.py:701-707`, `web.py:717-721`, and `web.py:737-743`; custom launchers do the same at `web.py:955-963` and `web.py:978-986`.
- The topbar terminal selector posts `terminal_command` from inline JS in `src/power_atlas/templates/index.html:6-15`, and `refreshSettings()` reads `d.terminal_command` at `index.html:150`.
- The MCP-safe helper is embedded in `src/power_atlas/launcher.py:111-274`. It hard-codes the WT profile at `launcher.py:202`, `pwsh.exe` process matching at `launcher.py:193-214`, `WindowsTerminal.exe` parent matching at `launcher.py:222-224`, and a 4500ms attach deadline at `launcher.py:209`.
- `_launch_mcp_safe_wt()` finds `pwsh` via `shutil.which("pwsh")` at `launcher.py:317`, uses `_MCP_SAFE_WT_TIMEOUT_SECONDS = 8` from `launcher.py:109`, and returns only `bool` at `launcher.py:315-353`, losing helper failure detail.
- Direct Windows Terminal builders also hard-code `PowerShell` at `launcher.py:554-559` and `launcher.py:659-660`.
- `LaunchResult` has only `success`, `session_id`, `workspace`, and `error` at `launcher.py:14-20`, so a successful degraded launch cannot report a warning.
- Tests encode the old model: config round-trips `terminal_command` in `tests/test_config.py:18-24`, Web save-setting tests post `terminal_command` in `tests/test_web.py:395-428`, `/api/settings` tests expect `terminal_command` in `tests/test_web.py:1038-1079`, and launcher tests pass `terminal_override` throughout `tests/test_launcher.py:88-245`.
- README documents `terminal_command` at `README.md:56-60` and the current hard-coded MCP-safe Windows note at `README.md:45-49`.

## 2) Goal
Introduce a typed launch-profile model that becomes the single source for terminal selection, Windows Terminal profile selection, and MCP-safe helper parameters. Provider and custom launches consume the active profile, the Web UI edits profiles directly, and this install's old `terminal_command` value is backed up before the new schema can erase it.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Complexity tier | Major | Standard | The change spans persisted config, launcher APIs, Web endpoints, template/JS, tests, docs, and one-time local migration. |
| Profile shape | `active_launch_profile: str` plus `launch_profiles: list[LaunchProfile]` with immutable `id` and editable `name` | Raw dicts; dict keyed by name; one unnamed profile | A typed model centralizes schema validation while preserving UI order and stable active references. |
| Default profile | `id="default"`, `name="Default"`, `terminal_command=""`, `wt_profile="PowerShell"`, `shell_process_name="pwsh.exe"`, `helper_runner="pwsh"`, `attach_timeout_ms=4500`, `helper_timeout_ms=8000`, `mcp_safe_enabled=true` | Empty profile; no Windows defaults | Defaults preserve current behavior while making the assumptions visible and editable. |
| Legacy `terminal_command` | Back up and migrate before schema-removal saves can run; keep only a narrow one-shot legacy read path in Phase 1 | Keep hidden fallback forever; silently ignore legacy configs | User rejected a long compatibility bridge, but backup must happen before any new-schema save can erase old values. |
| Field validation | Validate every process-launch field at config/API boundaries | Generic nested dict cleanup | These values drive subprocess and PowerShell behavior, so validation cannot be deferred to launch time. |
| MCP-safe shell compatibility | MCP-safe mode remains PowerShell 7+-compatible; `helper_runner` restricted to `pwsh`/`pwsh.exe` only (PS 5.1 excluded due to P/Invoke syntax differences); non-PowerShell WT profiles disable `mcp_safe_enabled` or use direct/custom launch | Add generic shell renderers now; allow Windows PowerShell 5.1 | The validated topology types PowerShell 7+ syntax; PS 5.1's `Add-Type` behavior differs. |
| Launcher API | Replace `terminal_override` plumbing with `launch_profile` plumbing | Keep `terminal_override` and add extra fields | The launch profile should be the new internal contract. |
| Fallback reporting | Add `warning` and `used_fallback` to `LaunchResult`; aggregate warnings in batch endpoints | Overload `error`; treat fallback success as pure success | Users need to see degraded MCP-safe behavior even when all launches succeed. |
| Local Web trust boundary | Add same-origin `Origin`/`Referer` validation to ALL mutation endpoints (not just profile routes); require at least one header present, reject both-absent and `Origin: null` | Rely only on random localhost port; protect only new endpoints | All POST endpoints trigger persistence or subprocess execution; partial protection creates false security. |
| UI management | Dedicated launch-profile settings modal reachable from compact topbar/settings area | Inline topbar select only; separate settings page | Multiple profile fields need a real editing surface while keeping the app single-page. |
| Test-profile action | Do not add one | Add a harmless visible WT test action | User explicitly rejected a dedicated test function; users test through normal launchers/providers. |
| Custom launchers | Use the active global profile when `terminal=true` | Per-launcher profile selector | User chose one active global profile for v1. |
| Non-terminal providers | Leave Kiro IDE/non-terminal behavior unchanged | Route everything through profiles | Launch profiles configure terminals only. |
| Documentation scope | Update `README.md`; leave historical `plans/tests` artifacts unless they become active contracts | Update every old test-plan reference | README is user-facing; old plans are historical context. |

## 4) External Dependencies & Costs

### Required external changes

| Category | Change needed | Owner | Status |
|---|---|---|---|
| Data migration / backfill | Create an immutable timestamped backup of `%LOCALAPPDATA%\power-atlas\config.toml` before any new-schema save can run; verify the backup contains legacy data when present; preserve non-empty legacy `terminal_command` into the default launch profile. | User or implementing agent with explicit approval | Pending |
| Rollout / cutover | Restart PowerAtlas after migration and verify provider/custom launches use the active profile. | User | Pending |
| Cleanup after rollback window | Keep the pre-migration backup until normal, fallback, disabled-MCP, and restore drills pass. | User | Pending |

### Cost impact

None. The change uses existing local files, Windows Terminal, PowerShell, and current Python dependencies. No cloud, CI/CD, IAM, DNS, secrets, paid APIs, or third-party services are introduced.

## 5) Implementation Phases

### Phase 1: Pre-Migrate Local Config and Add Launch-Profile Schema [QA]
**Goal**: Back up the local legacy terminal setting before schema-removal saves can run, then replace the persisted top-level terminal setting with normalized typed launch profiles and an active-profile accessor.

**Why horizontal**: `launcher.py`, `web.py`, and the UI all need the same normalized active profile. Implementing the schema inside the first launcher or UI phase would duplicate defaults and force later rewrites.

**File scope**: `src/power_atlas/config.py`, `tests/test_config.py`, local user config at `%LOCALAPPDATA%\power-atlas\config.toml` (backup/migration only; requires explicit approval before editing outside the repo)

**Detailed changes**:
- First, before code that can save the new schema runs, back up the current local config or record it as absent/default:

```powershell
$cfg = Join-Path $env:LOCALAPPDATA 'power-atlas\config.toml'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$bak = "$cfg.pre-launch-profiles.$stamp.bak"
if (Test-Path $cfg) {
  if (Test-Path $bak) {
    throw "Backup already exists: $bak"
  }
  Copy-Item $cfg $bak
  if ((Get-FileHash $cfg).Hash -ne (Get-FileHash $bak).Hash) {
    throw "Backup verification failed for $bak"
  }
} else {
  Write-Host "No existing config found; migration starts from defaults."
}
```

- Preserve a non-empty legacy `terminal_command` in the default launch profile during manual migration or through Phase 1's one-shot legacy read path. The one-shot path only reads old `terminal_command` when no valid `launch_profiles` exist; `save_config()` never writes the legacy key.
- Add a typed profile model:

```python
@dataclass
class LaunchProfile:
    id: str = "default"
    name: str = "Default"
    terminal_command: str = ""
    wt_profile: str = "PowerShell"
    shell_process_name: str = "pwsh.exe"
    helper_runner: str = "pwsh"
    attach_timeout_ms: int = 4500
    helper_timeout_ms: int = 8000
    mcp_safe_enabled: bool = True
```

- Update `Config` to use `active_launch_profile: str = "default"` and `launch_profiles: list[LaunchProfile] = field(default_factory=default_launch_profiles)`.
- Add field-specific validators with separate persisted-load and API-save semantics: persisted config load normalizes invalid fields to safe defaults and logs/drops unrecoverable profiles; API save rejects invalid submitted fields with an error toast and does not persist partial changes.
  - Profile IDs: `^[A-Za-z0-9_-]{1,64}$`; generated IDs use UUID-style strings; display names trim to 1-80 chars.
  - `terminal_command`: string only, max 512 chars, no control characters; empty means auto-detect; custom `{cwd}`/`{cmd}` templates remain allowed.
  - `wt_profile`: string only, trim/default when empty, max 128 chars, no control characters.
  - `shell_process_name`: basename executable grammar `^[A-Za-z0-9_.-]{1,128}\.exe$` (Windows-only field; ignored on Linux where MCP-safe is not supported); reject quotes, wildcards, path separators, and WQL operators; additionally deny-list known-dangerous process names (`cmd.exe`, `conhost.exe`, `explorer.exe`, `svchost.exe`) that could cause unintended process attachment.
  - `helper_runner`: allow only `pwsh` or `pwsh.exe` (PowerShell 7+); `powershell`/`powershell.exe` (Windows PowerShell 5.1) is excluded because the helper script's `Add-Type` P/Invoke syntax requires PowerShell 7+. No embedded args/templates; launcher resolves through `shutil.which()`.
  - Timeouts on persisted load: clamp `attach_timeout_ms` to 500-30000, clamp `helper_timeout_ms` to 1000-60000, then raise `helper_timeout_ms` to at least `attach_timeout_ms + 1000` when needed.
  - Timeouts on API save: reject values outside those ranges or `helper_timeout_ms < attach_timeout_ms + 1000`; return an error and keep the prior profile unchanged.
- Additionally, add `default_args` validation to the existing `/api/provider/save` endpoint: max 256 chars, no control characters, no shell metacharacters beyond standard CLI flags. This addresses the pre-existing gap where `default_args` flows to `shlex.split` → subprocess without any length or character validation.
- Normalize duplicate profile IDs deterministically: persisted load keeps the first occurrence unchanged and regenerates later duplicate IDs with stable `imported-<n>` IDs for that load/save cycle; if the active ID pointed at a duplicate, it resolves to the first occurrence. API save rejects duplicate IDs. Tests cover duplicate active and inactive profiles.
- Convert `LaunchProfile` instances to dicts only at JSON/TOML boundaries; internal callers use `LaunchProfile` or central helpers.
- Normalize empty `launch_profiles` list to `[LaunchProfile()]` during persisted load, matching the Phase 3 invariant that the last profile cannot be deleted via UI.

```python
def get_active_launch_profile(config: Config) -> LaunchProfile:
    for profile in config.launch_profiles:
        if profile.id == config.active_launch_profile:
            return replace(profile)
    fallback = config.launch_profiles[0] if config.launch_profiles else LaunchProfile()
    return replace(fallback)
```

- Update `tests/test_config.py` in place: old `terminal_command` round-trip assertions become launch-profile assertions; add malformed profile, duplicate ID, empty list, missing active ID, one-shot legacy read, timeout bounds, process-name grammar, helper-runner allowlist, and save round-trip coverage.

**Exit criteria**:
- [ ] Local `%LOCALAPPDATA%\power-atlas\config.toml` is backed up to a timestamped file before any implementation step can run PowerAtlas with the new schema, or explicitly recorded as absent/default.
- [ ] `Config()` has `active_launch_profile == "default"` and one default launch profile matching current Windows behavior defaults.
- [ ] Old top-level `terminal_command` is preserved into the default profile only when no valid `launch_profiles` exist, and `save_config()` never re-emits it.
- [ ] Invalid nested launch-profile values are sanitized/rejected with field-specific rules and do not corrupt unrelated config fields.
- [ ] Duplicate profile IDs keep the first profile, regenerate later duplicates on load, remap duplicate-active references to the first profile, and are rejected on API save.
- [ ] Tests cover persisted-load normalization and API-save rejection for zero, booleans, strings, huge values, and `helper_timeout_ms < attach_timeout_ms + 1000`.
- [ ] `get_active_launch_profile()` returns a copy and falls back deterministically when the configured active ID is missing.
- [ ] `tests/test_config.py` covers default, round-trip, malformed profile, duplicate IDs, legacy read, timeout bounds, process-name validation, helper-runner validation, empty profile list, and active-profile fallback.
- [ ] `python -m pytest tests/test_config.py` passes.

### Phase 2: Make Launcher Runtime Profile-Driven [QA]
**Goal**: Route provider and custom terminal launches through a `LaunchProfile`, parameterize WT/MCP-safe behavior, and preserve fallback diagnostics.

**File scope**: `src/power_atlas/launcher.py`, `tests/test_launcher.py`

**Detailed changes**:
- Extend launch results:

```python
@dataclass
class LaunchResult:
    success: bool
    session_id: str | None
    workspace: str
    error: str = ""
    warning: str = ""
    used_fallback: bool = False
```

- Replace `terminal_override` launch plumbing with `launch_profile: LaunchProfile | None` in ALL launch functions: `launch_session`, `launch_batch`, `launch_custom`, and `launch_custom_batch`. Batch functions propagate the profile through their iteration loops to per-session/per-workspace calls.
- Refactor `detect_terminal(config_override)` to accept `profile.terminal_command` as its input; rename parameter for clarity. Remove the process-lifetime `_terminal_cache` (profile changes invalidate it; the OS probe is cheap). `available_terminals()` is retained only if Phase 3 needs it for a dropdown in profile creation; otherwise mark for removal in Phase 5.
- Use the profile's `wt_profile` for both provider and custom WT direct builders (`_build_command` and `_build_custom_command`); only the default model should contain the string `PowerShell` as a default.
- MCP-safe helper is NEVER used for custom launchers — custom launchers use the profile's `wt_profile` for direct WT commands only. This is because custom commands are opaque strings, not typed PowerShell invocations. The `_should_use_mcp_safe_wt` gate remains provider-keyed.
- Parameterize the helper script with `WtProfile`, validated `ShellProcessName`, and `AttachTimeoutMs`. Avoid WQL injection by not interpolating untrusted text into CIM filters:

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq $ShellProcessName }
```

- Resolve the Phase 1 validated `helper_runner` through `shutil.which()` and pass bounded `helper_timeout_ms / 1000` to `subprocess.run(timeout=...)`.
- Gate MCP-safe with `profile.mcp_safe_enabled`; unsupported platforms/providers and custom templates still use the normal command path.
- When helper fails and direct WT fallback succeeds, return `success=True`, `used_fallback=True`, and a warning. When direct fallback also fails, include both helper and direct-launch errors in `LaunchResult.error`.
- Update `tests/test_launcher.py` in place for profile-driven WT profile, shell process matching, helper-runner resolution, timeout propagation, disabled MCP-safe mode, fallback warning metadata, double-failure error metadata, Linux/custom-template behavior, and Kiro IDE non-terminal behavior.

**Exit criteria**:
- [ ] Provider and custom WT direct commands use the profile's `wt_profile`; hard-coded `PowerShell` remains only as the default profile value or in tests for that default.
- [ ] MCP-safe helper receives profile-driven WT profile, validated shell process name, resolved helper runner, attach timeout, and helper timeout.
- [ ] `mcp_safe_enabled=false` uses the direct launch path without running the helper.
- [ ] Helper failure followed by fallback success returns `success=True`, `used_fallback=True`, and a non-empty `warning`.
- [ ] Helper failure followed by direct fallback failure returns `success=False` and an `error` containing both failure reasons.
- [ ] Custom terminal templates containing `{cwd}` or `{cmd}` still bypass MCP-safe helper routing.
- [ ] Custom launchers in WT use the profile's `wt_profile` for direct WT commands but never invoke the MCP-safe helper.
- [ ] `launch_custom` and `launch_custom_batch` accept `launch_profile` (not `terminal_override`); batch iteration propagates the profile to per-workspace calls.
- [ ] `detect_terminal` accepts the profile's terminal command; `_terminal_cache` is removed.
- [ ] Kiro IDE/non-terminal launches do not attempt terminal detection or read launch-profile fields.
- [ ] Mocked non-Windows and unknown-terminal cases still degrade through existing command-builder behavior.
- [ ] `tests/test_launcher.py` covers the profile-driven routing and fallback diagnostics above.
- [ ] `python -m pytest tests/test_launcher.py` passes.

### Phase 3: Wire Profiles Through Web API and Settings UI [QA]
**Goal**: Replace the topbar terminal selector with active launch-profile controls, route all Web launches through the active profile, and protect profile mutation endpoints.

**File scope**: `src/power_atlas/web.py`, `src/power_atlas/templates/index.html`, optional `src/power_atlas/templates/partials/launch_profile_modal.html`, `src/power_atlas/templates/partials/launcher_modal.html`, `src/power_atlas/static/style.css`, `tests/test_web.py`

**Detailed changes**:
- In `index()`, pass `active_launch_profile`, `launch_profiles`, and `active_launch_profile_detail` instead of `terminal_command`. Remove the `_terminal_context()` helper and its `available_terminals()` call — the profile model replaces terminal auto-detection in the UI. If auto-detect is needed for the profile-creation dropdown, use `detect_terminal("")` directly; `available_terminals()` and `_terminal_cache` are dead code after this phase.
- Change `/api/settings` to return launch-profile fields instead of `terminal_command`:

```python
return {
    "active_launch_profile": config.active_launch_profile,
    "launch_profiles": [asdict(p) for p in config.launch_profiles],
    "peek_hotkey": config.peek_hotkey,
    "port": config.port,
    "provider_settings": config.provider_settings,
    "custom_launchers": config.custom_launchers,
    "autostart": autostart_enabled,
}
```

- Remove `terminal_command` from `_SETTING_TYPES`; add explicit launch-profile endpoints rather than arbitrary nested writes through `/api/save-setting`:

```python
@app.post("/api/launch-profile/activate")
async def activate_launch_profile(request: Request): ...

@app.post("/api/launch-profile/save", response_class=HTMLResponse)
async def save_launch_profile(request: Request): ...

@app.post("/api/launch-profile/delete", response_class=HTMLResponse)
async def delete_launch_profile(request: Request): ...
```

- Add a shared mutation guard for ALL mutation endpoints (not just launch-affecting ones): `/api/launch-profile/*`, `/api/provider/save`, `/api/launcher/create`, `/api/launcher/update`, `/api/launcher/delete`, `/api/save-setting`, `/api/launch`, `/api/new-session`, `/api/launch-batch`, `/api/launcher/run`, `/api/launcher/run-batch`, `/api/set-workspace-icon`, `/api/autostart`. Reject `Origin: null`. Require at least one of `Origin` or `Referer` to be present (browsers always send at least one on POST); reject requests with both absent. When present, require exact scheme, host, and port match.
- Define profile endpoint semantics: create uses a generated unique ID, rename changes only `name`, save uses all-or-nothing semantics (no field is persisted until all pass validation), save rejects duplicate IDs and invalid fields, deleting the active profile reassigns to `default` or the first remaining profile (with a confirmation dialog warning the user their active profile will change), deleting the last profile is rejected (disable the delete button with a tooltip explaining why).
- Route provider and custom launch endpoints through `get_active_launch_profile(config)` and `launch_profile=profile`.
- On first load after migration (detected by absence of a `migration_toast_shown` flag in config), render a one-time informational toast: "Your terminal setting was migrated to a launch profile." Set `migration_toast_shown=true` after display.
- Render warning toasts when a single launch returns `LaunchResult.warning`. Warning-level toasts must be persistent (require manual dismiss) — not auto-dismissed like success toasts — so users don't miss degraded MCP-safe behavior. If helper crash produces an empty warning string, use the template: "MCP-safe mode failed; launched directly. Check profile settings."
- Aggregate batch warnings: format is "N launches used fallback: {first_warning}" when all succeed but some fell back; when failures and warnings both occur, include both counts and the first failure reason plus the first warning.
- Replace the inline topbar terminal select with a compact active-profile display/control (with tooltip/aria-label: "Click to manage launch profiles") plus a settings button. The modal supports active selection, create/rename/delete, and uses progressive disclosure: top section shows profile name and terminal command (with auto-detect toggle); a collapsible "Advanced: MCP-Safe Settings" section shows WT profile, shell process name, helper runner (as a dropdown select with options `pwsh` and `pwsh.exe`, not free-text), attach timeout (displayed in seconds with ms conversion), helper timeout (displayed in seconds with ms conversion), and MCP-safe toggle. It must not include a test-launch button. Render profile names and labels with Jinja escaping or DOM `textContent`, never string-concatenated HTML.
- Use a new `templates/partials/launch_profile_modal.html` only if it keeps `index.html` readable; `pyproject.toml` does not need changes because `templates/**` is already packaged at `pyproject.toml:32-33`.
- Update `refreshSettings()` to track profile data, avoid clobbering an open launcher or profile modal, refresh launchers after profile changes, and call `htmx.process(...)` after manual `innerHTML` swaps.
- Update `tests/test_web.py` in place: `/api/settings` exact key set (update `test_api_settings_returns_expected_keys`), profile endpoint validation, shared mutation guard rejection for ALL mutation endpoints (`Origin: null`, both-absent, mismatched scheme/host/port, cross-origin `Referer`), duplicate/delete-active semantics with confirmation, `/api/save-setting` rejecting `terminal_command` (update `test_accepts_valid_setting` and `test_rejects_wrong_type`), launch profile propagation, warning aggregation format, persistent warning toast rendering, migration toast one-time display, and metacharacter-rich profile-name rendering.

**Exit criteria**:
- [ ] `GET /api/settings` no longer returns `terminal_command`; it returns active/profile data and existing settings keys.
- [ ] `POST /api/save-setting` rejects `terminal_command` as an unknown setting.
- [ ] All mutation endpoints (not just launch-profile routes) use the shared mutation guard; tests reject `Origin: null`, both-headers-absent, mismatched scheme/host/port, and cross-origin `Referer`.
- [ ] Profile activate/save/delete endpoints validate IDs and fields with all-or-nothing save semantics, prevent deleting the last profile (button disabled with tooltip), show confirmation dialog when deleting the active profile, and reassign active profile deterministically.
- [ ] `/api/launch`, `/api/new-session`, `/api/launch-batch`, `/api/launcher/run`, and `/api/launcher/run-batch` pass `get_active_launch_profile(config)` into launcher calls.
- [ ] One-time migration toast renders on first load after migration and does not reappear.
- [ ] Single-launch and batch fallback warnings render as persistent (manual-dismiss) warning toasts with user-friendly fallback message template and escape helper-provided text safely.
- [ ] Batch warning aggregation uses the defined format: count + first representative reason.
- [ ] Metacharacter-rich profile names render via escaping/textContent and cannot inject HTML or script.
- [ ] Topbar UI no longer posts `terminal_command`; profile edits are done through the launch-profile modal.
- [ ] The modal has no dedicated test launch action.
- [ ] Manual JS swaps introduced by this phase call `htmx.process(...)` when needed.
- [ ] Browser runtime verification opens the modal, creates a profile, edits fields, activates it, deletes a non-active profile, refreshes settings, and sees fallback warning toast rendering.
- [ ] `tests/test_web.py` covers settings payload, profile endpoints, same-origin behavior, launch profile propagation, batch warning aggregation, and escaped warning toasts.
- [ ] `python -m pytest tests/test_web.py` passes or only fails on the pre-existing stale-card assertion if not already resolved by another plan; any such failure is reported explicitly.

### Phase 4: README and Migration Rollback/Cleanup
**Goal**: Update user-facing documentation and define the local migration rollback/cleanup path after the new profile model exists.

**File scope**: `README.md`, local user config backup at `%LOCALAPPDATA%\power-atlas\config.toml.pre-launch-profiles.<timestamp>.bak` (outside repo; explicit approval required for edits/deletion)

**Detailed changes**:
- Update README feature bullets and config example. Replace `terminal_command` with `active_launch_profile` and `[[launch_profiles]]`, and document that MCP-safe mode expects a PowerShell-compatible WT profile.
- Document that helper failures can fall back to direct WT launches and show a warning.
- Record rollback criteria: restore if PowerAtlas starts without the migrated profile, launch-profile UI cannot save, or normal provider launches fail due to profile config.
- Provide restore commands:

```powershell
$cfg = Join-Path $env:LOCALAPPDATA 'power-atlas\config.toml'
$bak = Get-ChildItem -Path (Split-Path $cfg) -Filter 'config.toml.pre-launch-profiles.*.bak' |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if ($bak) {
  Copy-Item $bak.FullName $cfg -Force
} else {
  throw "No pre-launch-profiles backup found."
}
```

- After normal, fallback, disabled-MCP, and restore drills pass, optionally delete the backup only with explicit user approval.
- Keep historical `plans/tests` artifacts unchanged unless implementation turns them into active test contracts.

**Exit criteria**:
- [ ] README feature list describes launch profiles instead of a settings page terminal preference.
- [ ] README config sample shows `active_launch_profile` and `[[launch_profiles]]` with Windows MCP-safe fields.
- [ ] README states MCP-safe helper is for PowerShell-compatible Windows Terminal profiles and falls back to direct WT launch on helper failure.
- [ ] Rollback criteria and restore commands are recorded in the plan implementation notes or README if user-facing.
- [ ] The local backup is kept until verification passes, or deletion is explicitly approved and recorded.
- [ ] No new documentation file is created.

### Phase 5: Final Integration Verification and Cleanup
**Goal**: Run focused and runtime verification, remove stale active references, and record implementation divergences.

**File scope**: `src/power_atlas/config.py`, `src/power_atlas/launcher.py`, `src/power_atlas/web.py`, `src/power_atlas/templates/index.html`, optional `src/power_atlas/templates/partials/launch_profile_modal.html`, `src/power_atlas/templates/partials/launcher_modal.html`, `src/power_atlas/static/style.css`, `tests/test_config.py`, `tests/test_launcher.py`, `tests/test_web.py`, `README.md`, `plans/260707_LAUNCH_PROFILES_FOR_EXPORTABLE_MCP_SAFE_TERMINALS.md`

**Detailed changes**:
- Grep active source/tests/docs for stale references:

```powershell
rg -n "terminal_command|terminal_override|PowerShell|MCP-safe|launch_profiles|active_launch_profile" src tests README.md
```

- Remove or justify each active `terminal_command`/`terminal_override` reference. Historical plans under `plans/done/` and `plans/tests/` may remain if clearly historical.
- Run focused lint and tests:

```powershell
.\.venv-PowerAtlas\Scripts\python -m ruff check src\power_atlas\config.py src\power_atlas\launcher.py src\power_atlas\web.py tests\test_config.py tests\test_launcher.py tests\test_web.py
.\.venv-PowerAtlas\Scripts\python -m pytest tests\test_config.py tests\test_launcher.py tests\test_web.py
```

- Run browser runtime verification for Phase 3's UI flows using Playwright/qbrowser. This is mandatory for the modal and warning-toast UI.
- Run full `pytest` if the unrelated `tests/test_web.py::test_partials_workspaces_stale` failure has been fixed by the separate findings plan; otherwise run full pytest and report that known pre-existing failure distinctly.
- Manually verify on Windows after migration: Kiro CLI new session, Kiro CLI resume, Claude Code new session, Claude Code resume, one custom terminal launcher, intentional helper failure fallback, `mcp_safe_enabled=false`, invalid profile recovery, and restoring the known-good profile from backup.

**Exit criteria**:
- [ ] No active source or test path still depends on top-level `terminal_command` or `terminal_override` except explicit migration comments/tests.
- [ ] Focused ruff command passes.
- [ ] Focused pytest command passes.
- [ ] Browser runtime verification passes for profile modal create/edit/delete/activate/refresh and warning toast rendering.
- [ ] Full pytest is run and passes, or the only failure is the known unrelated stale-card assertion and is reported with exact test name.
- [ ] Windows manual provider verification completed for Kiro CLI new, Kiro CLI resume, Claude Code new, Claude Code resume, intentional helper failure fallback, and `mcp_safe_enabled=false`.
- [ ] Windows manual custom-launcher verification completed through the active launch profile.
- [ ] Restore drill from the pre-migration backup is completed or explicitly deferred by the user.
- [ ] `## 9) Implementation Divergences from Plan` is updated if implementation differs from this plan.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Old `terminal_command` is erased before backup | A custom terminal override could be lost silently | Phase 1 backs up local config before new-schema saves and includes one-shot legacy preservation. |
| Profile schema accepts malicious or malformed process-launch fields | Bad config could crash or inject into subprocess/PowerShell behavior | Phase 1 defines load-normalize versus API-reject semantics, timeout bounds, duplicate-ID handling, and negative tests. |
| Launch-affecting mutation endpoints expand local command-execution surface | A cross-origin page could persist executable launch settings | Phase 3 adds a shared exact same-origin guard for ALL mutation endpoints, requires Origin or Referer presence, and tests rejected origins/referrers. |
| MCP-safe command rendering remains PowerShell syntax | Non-PowerShell WT profiles would receive invalid typed commands | README and UI document PowerShell-compatible requirement; users disable MCP-safe or use direct/custom launch for other shells. |
| Helper failure reason is lost on fallback or total failure | User cannot diagnose degraded or failed MCP-safe launches | Phase 2 preserves helper and direct-launch reasons; Phase 3 aggregates warnings in batch toasts. |
| Duplicate profile IDs or active deletion break routing | UI and launcher could select the wrong profile | Phase 1/3 define generated IDs, duplicate rejection, and active reassignment semantics with tests. |
| Browser UI changes pass unit tests but fail at runtime | Modal or toast behavior could be broken for users | Phase 3 and Phase 5 require browser runtime verification for modal flows and warning toast rendering. |
| Full pytest still has unrelated stale-card failure | Could obscure regressions from this plan | Focused tests are mandatory; full-suite failure must be reported with exact unrelated test name if still present. |
| Local config edit is outside the repo | Implementation may need approval or skip migration | Phase 1 creates immutable timestamped backups; Phase 4 defines restore and cleanup as owner-gated work. |

## 7) Verification
- `python -m pytest tests/test_config.py` after Phase 1.
- `python -m pytest tests/test_launcher.py` after Phase 2.
- `python -m pytest tests/test_web.py` after Phase 3, with any pre-existing unrelated failure named explicitly.
- Browser runtime verification for the launch-profile modal: open, create, edit, activate, delete, refresh settings, and fallback warning toast rendering.
- Focused final lint:

```powershell
.\.venv-PowerAtlas\Scripts\python -m ruff check src\power_atlas\config.py src\power_atlas\launcher.py src\power_atlas\web.py tests\test_config.py tests\test_launcher.py tests\test_web.py
```

- Focused final tests:

```powershell
.\.venv-PowerAtlas\Scripts\python -m pytest tests\test_config.py tests\test_launcher.py tests\test_web.py
```

- Full final tests:

```powershell
.\.venv-PowerAtlas\Scripts\python -m pytest
```

- Manual Windows acceptance after migration: Kiro CLI new session, Kiro CLI resume, Claude Code new session, Claude Code resume, custom terminal launcher, intentional helper failure fallback, `mcp_safe_enabled=false`, invalid profile recovery, and timestamped-backup restore drill.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Replace settings/terminal feature bullets, replace `terminal_command` config sample with launch profiles, and explain Windows MCP-safe profile fields/fallback behavior. | 4 |
| `plans/tests/260701_POWERATLAS.md` | Historical test artifact only; review references during implementation but do not update unless this plan's implementation makes it an active test contract. | doc-table-only |
| `plans/tests/260618_KIRO_ORCHESTRATOR.md` | Historical test artifact only; review references during implementation but do not update unless this plan's implementation makes it an active test contract. | doc-table-only |

## Progress Tracker

| # | Phase/Task | Status | Notes |
|---|---|---|---|
| 1 | Pre-migrate local config and add launch-profile schema | Pending | Foundation and backup guard for all later phases. |
| 2 | Make launcher runtime profile-driven | Pending | Depends on Phase 1 active-profile contract. |
| 3 | Wire profiles through Web API and settings UI | Pending | Depends on Phases 1-2. |
| 4 | README and migration rollback/cleanup | Pending | Documents user-visible behavior and backup restore path. |
| 5 | Final integration verification and cleanup | Pending | Runs after all code/docs/migration work. |

## Dependency Graph

```text
Phase 1 (immutable backup + config schema)
  -> Phase 2 (launcher runtime)
    -> Phase 3 (Web API/UI)
      -> Phase 4 (README + rollback/cleanup)
        -> Phase 5 (final verification)
```

## Backwards Compatibility

| Item | Strategy | Safety effect |
|---|---|---|
| Top-level `terminal_command` in old config | Create immutable timestamped backup before code saves; one-shot read into default profile; never re-emit legacy key | Avoids a long compatibility branch while protecting this install. |
| Default terminal behavior | Default launch profile keeps `terminal_command=""` auto-detect | New users retain current auto-detect behavior. |
| Windows Terminal profile | Default profile keeps `wt_profile="PowerShell"` | Existing direct WT and MCP-safe launch behavior remains the default. |
| MCP-safe provider gating | Keep limited to `kiro-cli` and `claude-code` on Windows WT | Avoids expanding unvalidated attach/inject behavior to other providers. |
| Custom terminal templates | `{cwd}`/`{cmd}` templates still bypass MCP-safe routing | Preserves custom terminal escape hatch. |
| Kiro IDE | Non-terminal provider behavior unchanged | Avoids routing a GUI provider through terminal profile settings. |
| `/api/settings` | Payload changes deliberately; tests updated | UI refresh continues but consumes launch-profile keys instead of old terminal key. |

## File Change Summary

### Created
- Optional: `src/power_atlas/templates/partials/launch_profile_modal.html` if keeping profile UI in a partial is cleaner than inline `index.html` markup.

### Modified
- `src/power_atlas/config.py`
- `src/power_atlas/launcher.py`
- `src/power_atlas/web.py`
- `src/power_atlas/templates/index.html`
- `src/power_atlas/templates/partials/launcher_modal.html` only if profile settings reuse shared modal conventions or interaction helpers.
- `src/power_atlas/static/style.css`
- `tests/test_config.py`
- `tests/test_launcher.py`
- `tests/test_web.py`
- `README.md`
- `plans/260707_LAUNCH_PROFILES_FOR_EXPORTABLE_MCP_SAFE_TERMINALS.md`

### Deleted
- None expected.

### Unchanged
- `src/power_atlas/data*.py`, `src/power_atlas/icons.py`, `src/power_atlas/autostart.py`, `src/power_atlas/peek.py`, `src/power_atlas/tray.py`, and non-terminal Kiro IDE launch semantics.

## Review Log

### 2026-07-07 -- Plan Review Cycle 1 (via /qplan)

10 findings (4 High, 6 Medium). 10 auto-resolved.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Migration ran after schema removal could erase legacy `terminal_command`. | Resolved -- Phase 1 now backs up and preserves legacy config before new-schema saves. |
| 2 | High | Profile mutation endpoints lacked a local-Web trust boundary. | Resolved -- Phase 3 adds same-origin validation and tests for launch-profile mutation endpoints. |
| 3 | High | Launch-profile fields lacked field-specific validation before subprocess use. | Resolved -- Phase 1 now specifies validators, bounds, and negative tests for every launch field. |
| 4 | High | Browser runtime verification was missing for the settings modal. | Resolved -- Phase 3 and Phase 5 require browser verification for modal and toast behavior. |
| 5 | Medium | Raw dict profiles leaked schema details across layers. | Resolved -- Phase 1 now introduces a typed `LaunchProfile` model and conversion boundaries. |
| 6 | Medium | Duplicate profile IDs and active deletion semantics were underspecified. | Resolved -- Phases 1 and 3 define duplicate rejection and deterministic active reassignment. |
| 7 | Medium | Batch endpoints could hide successful MCP-safe fallback warnings. | Resolved -- Phase 3 requires warning aggregation for provider and custom batch launches. |
| 8 | Medium | Rollback commands and failure triggers were missing. | Resolved -- Phase 4 defines rollback criteria, restore commands, and backup cleanup rules. |
| 9 | Medium | Total failure after helper failure could lose helper diagnostics. | Resolved -- Phase 2 requires double-failure errors to include helper and direct-launch reasons. |
| 10 | Medium | Manual acceptance omitted helper-failure and invalid-profile drills. | Resolved -- Phase 5 adds disabled-MCP, fallback, invalid-profile, and restore drills. |


### 2026-07-07 -- Plan Review Cycle 2 (via /qplan)

7 findings (1 High, 6 Medium). 7 auto-resolved.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Backup command could overwrite the only legacy backup on rerun. | Resolved -- Phase 1 now requires timestamped immutable backups and backup-content verification. |
| 2 | Medium | Duplicate-ID load behavior still allowed regenerate-or-drop ambiguity. | Resolved -- Phase 1 now keeps first IDs, regenerates later duplicates, and maps active duplicates to the first. |
| 3 | Medium | Validation mixed clamping, sanitizing, and rejecting without boundary-specific rules. | Resolved -- Phase 1 now separates persisted-load normalization from API-save rejection. |
| 4 | Medium | Same-origin validation omitted `Origin: null` and exact scheme/host/port matching. | Resolved -- Phase 3 now requires exact matching and tests null, mismatched, and cross-origin cases. |
| 5 | Medium | Profile names lacked explicit safe-rendering requirements. | Resolved -- Phase 3 now requires Jinja escaping or `textContent` plus metacharacter tests. |
| 6 | Medium | Same-origin protection covered only new profile endpoints. | Resolved -- Phase 3 now applies a shared mutation guard to all launch-affecting persistence routes. |
| 7 | Medium | Restore commands targeted a single mutable backup filename. | Resolved -- Phase 4 now restores from the latest timestamped backup. |

### 2026-07-07 -- Plan Review Cycle 3 (via /qplan, high-effort 4 personas)

45 raw findings from Architect (12), Senior engineer (11), Security auditor (10), End-user advocate (12). After deduplication: 28 unique. 20 auto-resolved (8 High, 12 Medium). 8 remaining Low-severity noted.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `available_terminals()`, `_terminal_context()`, and topbar terminal dropdown fate unspecified after profiles replace them. | Resolved -- Phase 3 now explicitly removes `_terminal_context()` and marks `available_terminals()`/`_terminal_cache` as dead code. |
| 2 | High | Custom launchers: MCP-safe helper applicability undefined; `_build_custom_command` also hard-codes `PowerShell`. | Resolved -- Phase 2 now states custom launchers never use MCP-safe helper; both WT builders use profile `wt_profile`. |
| 3 | High | `launch_custom`/`launch_custom_batch` still accept `terminal_override` but plan only replaces `launch_session`/`launch_batch`. | Resolved -- Phase 2 now replaces `terminal_override` in ALL four launch functions with `launch_profile`. |
| 4 | High | No in-app notification tells migrating users their `terminal_command` was consumed. | Resolved -- Phase 3 now adds a one-time migration toast with `migration_toast_shown` flag. |
| 5 | High | Fallback warning toast auto-dismisses after 4s; user may miss degraded MCP-safe behavior. | Resolved -- Phase 3 now makes warning-level toasts persistent (manual dismiss only). |
| 6 | High | `default_args` flows to `shlex.split` → subprocess with no validation; pre-existing gap not addressed. | Resolved -- Phase 1 now adds `default_args` validation (max 256 chars, no control/shell metacharacters). |
| 7 | High | Mutation guard scope ambiguous; existing launch endpoints that trigger subprocess remain unprotected. | Resolved -- Phase 3 now applies the guard to ALL mutation endpoints (launches, settings, icons, autostart). |
| 8 | High | Origin guard "accept missing" policy allows non-browser HTTP clients; plan rejects `null` but allows both-absent. | Resolved -- Phase 3 now requires at least one of Origin/Referer present; both-absent is rejected. |
| 9 | Medium | `detect_terminal` / `available_terminals` / `_terminal_cache` disposition unspecified. | Resolved -- Phase 2 removes cache; Phase 3 removes `_terminal_context()`; retained only if needed for dropdown. |
| 10 | Medium | `helper_runner` allowlist includes `powershell.exe` (PS 5.1) but helper P/Invoke syntax requires PS 7+. | Resolved -- Restricted to `pwsh`/`pwsh.exe` only; Design Decisions table updated. |
| 11 | Medium | Batch function signatures still accept `str` terminal_override; profile not propagated through iteration. | Resolved -- Phase 2 now updates all batch signatures and documents profile propagation through loops. |
| 12 | Medium | Batch warning aggregation contract undefined (concatenate? count? first-only?). | Resolved -- Phase 3 now defines format: "N launches used fallback: {first_warning}". |
| 13 | Medium | Profile modal shows 8+ fields with no progressive disclosure; cognitive overload for most users. | Resolved -- Phase 3 now specifies collapsible "Advanced: MCP-Safe Settings" section. |
| 14 | Medium | `helper_runner` as free-text input; user has no guidance about valid values. | Resolved -- Phase 3 now specifies a dropdown select with `pwsh`/`pwsh.exe` options. |
| 15 | Medium | All-or-nothing save semantics unspecified; partial valid fields could persist. | Resolved -- Phase 3 now specifies all-or-nothing: no field persisted until all pass validation. |
| 16 | Medium | `shell_process_name` regex permits dangerous names (`cmd.exe`, `conhost.exe`). | Resolved -- Phase 1 now adds a deny-list of known-dangerous process names. |
| 17 | Medium | Delete-active-profile reassignment has no user warning in UI. | Resolved -- Phase 3 now specifies confirmation dialog before deleting active profile. |
| 18 | Medium | Delete-last-profile rejection has no specified UI affordance. | Resolved -- Phase 3 now specifies disabled button with tooltip. |
| 19 | Medium | Helper crash may produce empty warning string; user sees empty toast. | Resolved -- Phase 3 now defines fallback message template for empty warnings. |
| 20 | Medium | `test_accepts_valid_setting` and `test_rejects_wrong_type` reference `terminal_command` but plan doesn't name them. | Resolved -- Phase 3 test section now explicitly names these tests for update. |
| 21 | Low | `LaunchProfile.terminal_command` reuses the same field name as removed legacy key — grep confusion in Phase 5. | Noted -- acknowledged trade-off; Phase 5 grep handles both contexts with comments. |
| 22 | Low | `shell_process_name` regex `.exe$` is Windows-only; Linux profiles never match. | Noted -- Phase 1 now documents the field as Windows-only; ignored on Linux. |
| 23 | Low | Phase 5 manual verification omits "Claude Code new session" as a launch path. | Resolved -- added to all manual verification lists. |
| 24 | Low | Default `mcp_safe_enabled=true` may silently fail for users without pwsh; no first-run detection. | Noted -- fallback exists (direct WT launch); persistent warning toast now ensures visibility. |
| 25 | Low | Timeout fields in raw milliseconds are unfriendly for non-developer users. | Noted -- Phase 3 UI displays as seconds; conversion happens internally. |
| 26 | Low | No keyboard shortcut to reach profile modal; relies on single button. | Noted -- tooltip/aria-label added to topbar profile display for discoverability. |
| 27 | Low | Custom-launcher command validation (`/api/launcher/create`) is out of scope but pre-exists. | Noted -- documented as deferred; TODO comment suggested for source. |
| 28 | Low | API breaking change (`terminal_command` removed from `/api/settings`) not documented as breaking. | Noted -- README update in Phase 4 covers the change; no external consumers beyond the self-contained UI. |
## 9) Implementation Divergences from Plan
<Reserved -- filled during implementation>


