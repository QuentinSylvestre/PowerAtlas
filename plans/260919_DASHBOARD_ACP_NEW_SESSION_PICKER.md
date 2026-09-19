# Dashboard ACP New Session Picker

> **Date**: 2026-09-19
> **Status**: Draft
> **Scope**: Port the /acp new-session picker modal to the main dashboard so users can create kiro-cli v3 ACP sessions inline without leaving the dashboard.
> **Estimated effort**: 1-2 days

---

## Intent

### Problem statement & desired outcomes

The main dashboard lets users resume kiro-cli v3 sessions in the transcript panel and chat live, but has no way to *create* a new ACP session from the dashboard. The only "new session" affordance on the dashboard is the sparkle dropdown, which launches terminal sessions (`POST /api/new-session`). The `/acp` page has a polished workspace-picker modal that creates ACP sessions over the WebSocket and opens them in the transcript pane — this feature does not exist on the dashboard at all.

The desired outcome is feature parity: a user on the dashboard can trigger the same picker flow (workspace selection, task mode, optional close-of-current-session), create an ACP session, and have it immediately appear in the transcript panel ready to use — without navigating to `/acp`.

### Success criteria

- SC-1: A "kiro-cli v3 ACP session" item appears in the sparkle dropdown on every workspace group header, alongside the existing provider items.
- SC-2: Clicking that item opens a picker modal matching `/acp`'s look and feel: workspace search/filter, session count per workspace, "The agent's own folder" neutral option, task-mode picker, "close current session" checkbox (shown when a session is attached in the transcript panel), and at-capacity state with guidance.
- SC-3: On workspace selection, the dashboard sends `{type:'new', payload:{cwd, mode}}` over `_dashWs`, receives the `{type:'session', payload:{created:true}}` response, auto-opens the new session in the transcript panel, and performs a targeted single-workspace rail refresh to show the new session.
- SC-4: All existing dashboard features (terminal launches via sparkle, resume, transcript, live attach, close) are unaffected.
- SC-5: The task-mode picker resets to `kiro_default` on each picker open, matching `/acp`.
- SC-6: The "close current session" checkbox is shown when `_dashAttachedSid` is non-null; disabled when `_dashTurnActive` is true with the same label text as `/acp`'s `pickerRenderKeepRow()`.
- SC-7: `acp_page.test.mjs` is extended to cover the new picker JS behavior (open/close, at-capacity state, creation flow frame handling).

### Scope boundaries & non-goals

**In scope:**
- New "kiro-cli v3 ACP session" sparkle dropdown item in the dashboard workspace group header.
- Picker modal HTML added to `index.html` (mirroring `/acp`'s `#acpPicker` structure).
- Task-mode picker CSS (`.acp-taskmode-*`) migrated from `acp.html` inline `<style>` to `style.css` so both pages share it.
- JS for the dashboard picker: `dashPickerOpen`, `dashPickerLoad`, `dashPickerCreate`, `dashPickerRender`, `dashPickerRunPending`, `dashPickerRenderKeepRow`.
- New `dashHandle` branch for `{type:'session', payload:{created:true}}` to capture the new session sid and open the transcript panel.
- Targeted single-workspace rail refresh after creation.
- `acp_page.test.mjs` extensions for picker behavior.
- `AGENTS.md` update removing `.acp-taskmode-*` from the inline-style-block exception list.

**Out of scope:**
- Any changes to `/acp` itself.
- Backend changes (no new Python routes needed).
- Image paste / Steer / Queue mode in the new-session creation flow.
- Global topbar "New session" button.

---

## 1) Current State

**`src/power_atlas/static/style.css` lines 1995+**: `.acp-picker`, `.acp-picker[hidden]`, `.acp-picker-panel`, `.acp-picker-head`, `.acp-picker-list`, `.acp-picker-option`, `.acp-picker-search`, `.acp-picker-note` are already in `style.css` — shared between pages today.

**`src/power_atlas/templates/acp.html` inline `<style>` (~288 lines)**: `.acp-taskmode-toggle`, `.acp-taskmode-menu`, `.acp-taskmode-option`, `.acp-taskmode-*` CSS lives only here today. `acp.html` inherits `base.html` which links `style.css`, so moving these rules to `style.css` makes them available to `index.html` without any HTML change to `acp.html`.

**`src/power_atlas/templates/index.html` line 671**: `var ACP_TOKEN = {{ acp_token | tojson }};` — token is already injected.

**`src/power_atlas/templates/index.html` lines 695-714**: `dashConnect(onReady)` opens `/ws/acp?t={ACP_TOKEN}`, attaches `dashHandle` as `onmessage`. Reused as-is by the picker's creation flow.

**`src/power_atlas/templates/index.html` line 846-930**: `dashHandle(frame)` — the `type === 'session'` branch (lines 851-868) guards `if (sid !== _viewingSid) return` before doing anything with the session frame. A newly created session has a sid unknown to `_viewingSid`, so this guard silently drops the creation response today. Fix: detect `payload.created === true` before the `_viewingSid` guard.

**`src/power_atlas/templates/index.html` lines 2000-2035 (approx.)**: sparkle dropdown builder in `dashRailGroupNode()`. The `_availableProviders` loop builds per-provider items, each calling `newSessionInWorkspace(btn, p.name)` → `POST /api/new-session` (terminal launch). The new ACP item is a fixed entry added before this loop, guarded by `ACP_TOKEN !== null`, calling `dashPickerOpen(group.cwd)`.

**`/api/acp/workspaces`**: returns `{workspaces:[{cwd, name, sessions}], missing, capacity:{held, max}}`. Used by the picker to load workspace rows.

**`/api/dashboard/sessions?cwd=<cwd>`**: accepts a `cwd` param for targeted single-workspace refresh. `dashRailMergeGroup()` at line ~1340 can absorb the result.

**`tests/acp_page.test.mjs`**: covers `acp.html` behavior and the remote-access panel in `index.html`. Uses a Jinja renderer + DOM stand-in. The same harness applies to new picker JS in `index.html`.

**`AGENTS.md` line 7** (§ "ACP UI iteration does not require a restart"): explicitly lists `.acp-taskmode-*` as an inline-`<style>` exception. Phase 1 makes this stale — must be updated when the CSS is moved.

## 2) Goal

Add a "kiro-cli v3 ACP session" entry to the dashboard's sparkle dropdown that opens a polished workspace-picker modal, creates the session over the existing WebSocket, and auto-opens it in the transcript panel — matching `/acp`'s new-session experience without any backend changes.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Entry point for "New ACP session" | Fixed item in sparkle dropdown per-workspace, before the `_availableProviders` provider loop | Global topbar button; replacing sparkle with ACP-only | User confirmed per-workspace sparkle item alongside existing terminal items; preserves all existing features |
| Socket reuse | Reuse `_dashWs` + `dashConnect()` | Separate socket for creation only | Consistent with existing subscribe/load pattern; one socket per page load |
| Close-before-create | Port `/acp`'s `pendingCreate` variable + `dashPickerRunPending()` exactly | Disallow close-before-create on dashboard | User confirmed exact parity; `session_closed` frame already handled in `dashHandle` |
| `dashHandle` creation fix | Detect `payload.created === true` before the `sid !== _viewingSid` guard; set `_viewingSid`, clear transcript, adopt rail, fall through | Add a separate `type === 'created'` frame (would require backend change) | No backend change needed; the `session` frame already carries `payload.created` |
| Rail refresh after creation | Targeted: `dashRailFetch({cwd: newCwd, ...})` + `dashRailMergeGroup()` | Full reload via `loadGroupPage(1)` | Avoids resetting scroll/filter state; consistent with `/acp`'s `railAdoptCreated` pattern |
| Task-mode picker reuse | Use migrated `.acp-taskmode-*` CSS classes from `style.css` | Duplicate CSS in `index.html`; use different class names | CSS migration is the cleanest path; no duplication; shared visual language |
| CSS migration for taskmode | Move `.acp-taskmode-*` from `acp.html` inline `<style>` to `style.css` | Keep inline and duplicate for dashboard | AGENTS.md already documents taskmode CSS as inline-only — migration removes a known exception |

## 4) External Dependencies & Costs

### Required external changes

None. This is a pure frontend JS/HTML/CSS change. No Python routes, no infrastructure, no IAM, no data migration.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: CSS migration — `.acp-taskmode-*` to `style.css` [QA]

**Goal**: Move all `.acp-taskmode-*` CSS rules from `acp.html`'s inline `<style>` block into `style.css`, and update `AGENTS.md` to remove the now-stale exception reference.

**Why this phase is first**: Phases 2 and 3 depend on `.acp-taskmode-*` classes being available from `style.css`. Migrating CSS first makes the dependency clean and verifiable before any HTML/JS is written.

**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/static/style.css`, `AGENTS.md`

**Changes:**

1. In `acp.html`, locate the inline `<style>` block. Identify all rules whose selector starts with `.acp-taskmode` (including `.acp-taskmode-toggle`, `.acp-taskmode-menu`, `.acp-taskmode-option`, `.acp-taskmode-picker`, and any media-query variants). **Also identify `.acp-picker-taskmode` and `.acp-picker-taskmode-label`** — these are layout/label rules for the taskmode row container that also live inline and are required by Phase 2's HTML. Remove all of them from the inline block.

2. Append those rules to `style.css` in the existing `.acp-*` component section (near the `.acp-picker` rules at line 1995+), with a comment: `/* task-mode picker (shared: acp.html + index.html) */`.

3. In `AGENTS.md` § "ACP UI iteration does not require a restart" (the paragraph referencing `.acp-taskmode-*` as an inline-`<style>` exception): remove `.acp-taskmode-*` from the exception list (`.acp-mode-*` stays inline). Update the sentence to reflect that `.acp-taskmode-*` now lives in `style.css`.

**Exit criteria:**
- [x] All `.acp-taskmode-*` rules removed from `acp.html`'s inline `<style>` block
- [x] Same rules present in `style.css` under a `/* task-mode picker */` comment
- [x] `AGENTS.md` exception list updated — `.acp-taskmode-*` no longer listed as inline; `.acp-mode-*` still listed
- [x] `node tests/acp_page.test.mjs` exits 0 (no regression in acp.html visual behavior)

**Covers**: SC-4 (no regression)

---

### Phase 2: Picker modal HTML in `index.html` [QA]

**Goal**: Add the new-session picker modal HTML structure to `index.html`, mirroring `/acp`'s `#acpPicker` element with dashboard-namespaced IDs.

**File scope**: `src/power_atlas/templates/index.html`

**Where to insert**: Immediately before the closing `</body>` tag, after any existing overlay elements (e.g., the remote-access panel). Must be `hidden` by default.

**HTML to add** (mirrors `/acp`'s `#acpPicker` exactly, with `dash`-prefixed IDs):

```html
<!-- Dashboard ACP new-session picker — mirrors /acp #acpPicker -->
<div class="acp-picker" id="dashPicker" hidden role="dialog" aria-modal="true"
     aria-labelledby="dashPickerTitle">
  <div class="acp-picker-panel">
    <div class="acp-picker-head">
      <span class="acp-picker-title" id="dashPickerTitle">New session — where?</span>
      <button class="acp-btn" id="dashPickerCancel" type="button">Cancel</button>
    </div>
    <div class="acp-picker-note" id="dashPickerNote" role="status" aria-live="polite"></div>
    <div class="acp-picker-taskmode" id="dashPickerTaskModeRow">
      <span class="acp-picker-taskmode-label">Task mode</span>
      <div class="acp-taskmode-picker">
        <button class="acp-btn acp-taskmode-toggle" id="dashPickerTaskModeToggle"
                data-task-mode="kiro_default" type="button"
                aria-haspopup="menu" aria-expanded="false" aria-label="Task mode: Default">
          <span id="dashPickerTaskModeToggleText">Default</span>
          <svg class="acp-taskmode-chevron" viewBox="0 0 10 6" aria-hidden="true"><path d="M1 1l4 4 4-4" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/></svg>
        </button>
        <div class="acp-taskmode-menu" id="dashPickerTaskModeMenu" hidden role="menu">
          <!-- populated by dashPickerInitTaskMode() -->
        </div>
      </div>
    </div>
    <label class="acp-picker-keep" id="dashPickerKeepRow" hidden>
      <input type="checkbox" id="dashPickerCloseCurrent">
      <span id="dashPickerKeepText"></span>
    </label>
    <button class="acp-btn acp-picker-option" id="dashPickerNeutral" type="button">
      <span class="acp-picker-option-name">The agent's own folder</span>
      <span class="acp-picker-option-hint acp-picker-neutral-hint">General local work, not tied to a specific project.</span>
    </button>
    <input class="acp-rail-search acp-picker-search" id="dashPickerSearch"
           type="text" placeholder="Filter workspaces" autocomplete="off">
    <div class="acp-picker-list" id="dashPickerList"></div>
  </div>
</div>
```

**Note on SVG chevron**: Copy the exact `<svg>` used in `/acp`'s `#acpPicker` task-mode toggle (inspect `acp.html` for the exact markup used by the `.acp-taskmode-chevron` element). The snippet above is illustrative; use the verbatim SVG from `acp.html`.

**Exit criteria:**
- [x] `#dashPicker` element present in `index.html`, `hidden` by default
- [x] All required child elements present: `#dashPickerTitle`, `#dashPickerCancel`, `#dashPickerNote`, `#dashPickerTaskModeRow`, `#dashPickerTaskModeToggle`, `#dashPickerTaskModeMenu`, `#dashPickerKeepRow`, `#dashPickerCloseCurrent`, `#dashPickerNeutral`, `#dashPickerSearch`, `#dashPickerList` (11 elements)
- [x] `node tests/acp_page.test.mjs` exits 0

**Covers**: SC-2 (picker modal structure)

---

### Phase 3: Picker JS in `index.html` [QA]

**Goal**: Implement the dashboard picker's JavaScript — open/close, workspace loading, task-mode picker, close-current-session checkbox, and session creation via WebSocket.

**File scope**: `src/power_atlas/templates/index.html`

**Where to insert**: In the `<script>` section of `index.html`, after the existing `dashConnect`/`dashHandle`/`dashSendPrompt` functions (Phase 3 of the live-attach wiring, currently ending around line ~840). Add a new "Phase 4: ACP new-session picker" comment block.

**State variables to add** (alongside the existing `_dashWs`, `_viewingSid`, etc.):
```javascript
// ---- Phase 4: ACP new-session picker ------------------------------------
var _dashPickerWorkspaces = [];
var _dashPickerMissing = 0;
var _dashPickerCapacity = {held: 0, max: 8};
var _dashPickerFilter = '';
var _dashPickedTaskMode = 'kiro_default';
var _dashPendingCreate = null; // {cwd, mode} when close-before-create is in flight
```

**Task mode labels** (match `/acp` exactly):
```javascript
var _DASH_TASK_MODES = [
  {id: 'kiro_default', label: 'Default'},
  {id: 'spec',         label: 'Spec'},
  {id: 'quick-spec',   label: 'Quick spec'},
  {id: 'bug-fix',      label: 'Bug fix'},
  {id: 'plan',         label: 'Plan'},
  {id: 'semantic_reviewer', label: 'Semantic reviewer'},
];
```

**`dashPickerInitTaskMode()`**: Populate `#dashPickerTaskModeMenu` with one `<button class="acp-taskmode-option" role="menuitem">` per entry in `_DASH_TASK_MODES`. On click: update `_dashPickedTaskMode`, update `#dashPickerTaskModeToggle`'s `data-task-mode` and `#dashPickerTaskModeToggleText`'s text, hide the menu, set `aria-expanded="false"`.

`#dashPickerTaskModeToggle` click: toggle `#dashPickerTaskModeMenu` hidden/shown, update `aria-expanded`.

**`dashPickerOpen(cwd)`**:
1. Return immediately if `!ACP_TOKEN`.
2. `_dashPickerFilter = cwd || ''`.
3. `document.getElementById('dashPickerSearch').value = _dashPickerFilter`.
4. Reset task mode: `_dashPickedTaskMode = 'kiro_default'`; update toggle text and `data-task-mode`.
5. `document.getElementById('dashPickerCloseCurrent').checked = false`.
6. `dashPickerRenderKeepRow()`.
7. `document.getElementById('dashPickerNote').textContent = 'loading workspaces\u2026'`.
8. `document.getElementById('dashPicker').hidden = false`.
9. `document.getElementById('dashPickerSearch').focus()`.
10. `dashPickerRender()` (with whatever is already in `_dashPickerWorkspaces`).
11. `dashPickerLoad()`.

**`dashPickerClose()`**:
1. `document.getElementById('dashPicker').hidden = true`.
2. `_dashPendingCreate = null` — clear any pending close-then-create to prevent stale creation on a future `session_closed` frame.
3. Close the task-mode submenu if open: `document.getElementById('dashPickerTaskModeMenu').hidden = true; document.getElementById('dashPickerTaskModeToggle').setAttribute('aria-expanded', 'false')`.

**`dashPickerLoad()`**:
```javascript
function dashPickerLoad() {
  fetch('/api/acp/workspaces', {cache: 'no-store'})
    .then(function(r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function(data) {
      _dashPickerWorkspaces = data.workspaces || [];
      _dashPickerMissing = data.missing || 0;
      _dashPickerCapacity = data.capacity || {held: 0, max: 8};
      dashPickerRender();
    })
    .catch(function(err) {
      document.getElementById('dashPickerNote').textContent = 'Could not load workspaces (' + err.message + ')';
    });
}
```

**`dashPickerRender()`**: Rebuild `#dashPickerList` from `_dashPickerWorkspaces` filtered by `_dashPickerFilter`. For each workspace, create a `<button class="acp-btn acp-picker-option acp-picker-ws" type="button">` containing:
- `<span class="acp-picker-option-name">` — workspace name (`ws.name`)
- `<span class="acp-picker-option-count">` — e.g. "3 sessions"
- `<span class="acp-picker-option-hint">` — `ws.cwd`

Disable workspace buttons when `_dashPickerCapacity.held >= _dashPickerCapacity.max`. On click: `dashPickerCreate(ws.cwd)`.

Also update `#dashPickerNote`: `"{N} workspaces · {held}/{max} sessions open"` + optional `"· {missing} hidden (folder no longer on this machine)"`.

If at capacity, add: `"· limit reached — close one to make room."` and disable `#dashPickerNeutral`.

Filter: `(ws.name + ' ' + ws.cwd).toLowerCase().indexOf(_dashPickerFilter.toLowerCase()) !== -1`.

If no workspaces after filter: show `<div class="acp-picker-empty">No workspaces match.</div>`.

**`dashPickerRenderKeepRow()`**: Mirror `/acp`'s `pickerRenderKeepRow()` exactly:
- Show `#dashPickerKeepRow` only when `_dashAttachedSid` is non-null.
- If `_dashTurnActive`: disable checkbox, set text to `"The session you have open is still answering, so it cannot be closed yet. It stays open and keeps its slot."`.
- Else: enable checkbox. Use `_dashPickerCapacity.max` for the slot count, defaulting to 8 if not yet loaded: set text to `"Close the session I have open first — otherwise it stays open, holding one of the " + ((_dashPickerCapacity && _dashPickerCapacity.max) || 8) + " slots until it is closed or reclaimed."`.
- This function is also called after `dashPickerLoad()` resolves to refresh the max with the server's actual value.

**`dashPickerCreate(cwd)`**:
```javascript
function dashPickerCreate(cwd) {
  if (_dashPickerCapacity.held >= _dashPickerCapacity.max) {
    document.getElementById('dashPickerNote').textContent =
      _dashPickerCapacity.held + ' of ' + _dashPickerCapacity.max +
      ' sessions are already open, which is the limit. Close one to make room.';
    return;
  }
  var wantsClose = !!(
    document.getElementById('dashPickerCloseCurrent').checked &&
    _dashAttachedSid &&
    !_dashTurnActive
  );
  var mode = _dashPickedTaskMode;
  dashPickerClose();
  if (!wantsClose) {
    dashConnect(function() { send('new', {cwd: cwd, mode: mode}, null); });
  } else {
    _dashPendingCreate = {cwd: cwd, mode: mode};
    send('close', {}, _dashAttachedSid);
  }
}
```

**`dashPickerRunPending()`** (called from `dashHandle` on `session_closed` or `close_in_progress` when `_dashPendingCreate` is set):
```javascript
function dashPickerRunPending() {
  if (!_dashPendingCreate) return;
  // Re-check capacity — the state may have changed since the user pressed create
  if (_dashPickerCapacity.held >= _dashPickerCapacity.max) {
    _dashPendingCreate = null;
    dashSetComposerNote('Session limit reached while waiting; could not create. Close a session and try again.');
    return;
  }
  var pending = _dashPendingCreate;
  _dashPendingCreate = null;
  dashConnect(function() { send('new', {cwd: pending.cwd, mode: pending.mode}, null); });
}
```

Also add `_dashPendingCreate = null` to `dashCloseIfAbandoned()` — called on page unload and session switches — so that a pending close-then-create cannot fire after navigation:

```javascript
function dashCloseIfAbandoned() {
  if (_dashAttachedSid && _dashOrigin === 'dashboard' && !_dashSent) {
    send('close', {}, _dashAttachedSid);
  }
  _dashAttachedSid = null;
  _dashOrigin = null;
  _dashSent = false;
  _dashTurnActive = false;
  _dashLoadingSid = null;
  _dashPendingSend = null;
  _dashPendingCreate = null; // prevent stale close-then-create on back-navigation
}
```

1. **`session_closed` handler** (around line 910+): after the existing cleanup, add `dashPickerRunPending();`.

2. **`close_in_progress` error path**: `dashHandle` currently has no `close_in_progress` branch. Add one inside the `type === 'error'` handler:
   ```javascript
   if (type === 'error' && payload && payload.code === 'close_in_progress') {
     dashPickerRunPending();
     return;
   }
   ```
   This mirrors `/acp`'s own `close_in_progress` arm.

3. **Close-refused error paths** (e.g. `turn_in_progress`, `not_subscribed`): these mean close failed, so the pending create cannot proceed. Add:
   ```javascript
   if (type === 'error' && payload && (payload.code === 'turn_in_progress' || payload.code === 'not_subscribed')) {
     _dashPendingCreate = null;
     dashSetComposerNote('Could not close — ' + (payload.message || 'session could not be closed') + '. Try again when the turn finishes.');
     return;
   }
   ```

**Search filter wiring**: `document.getElementById('dashPickerSearch').addEventListener('input', function(e) { _dashPickerFilter = e.target.value; dashPickerRender(); })`.

**Cancel button**: `document.getElementById('dashPickerCancel').addEventListener('click', dashPickerClose)`.

**Neutral button**: `document.getElementById('dashPickerNeutral').addEventListener('click', function() { dashPickerCreate(''); })`.

**Picker close on Escape**: Add to the existing `document.addEventListener('keydown', ...)` handler (or add a new one): if `e.key === 'Escape'` and `#dashPicker` is not hidden, call `dashPickerClose()`. Match `/acp`'s layered-dismiss behavior: if the task-mode submenu is open, close it first; only close the picker itself when the submenu is already closed.

**Focus trap**: Mirror `/acp`'s `trapFocus()` pattern. When `#dashPicker` is shown, trap Tab focus within it: the focusable elements are `#dashPickerCancel`, `#dashPickerTaskModeToggle`, `#dashPickerCloseCurrent` (when visible and enabled), `#dashPickerNeutral`, `#dashPickerSearch`, and any workspace buttons in `#dashPickerList`. On `Tab`/`Shift+Tab` that would leave the picker panel, wrap around. On `dashPickerClose()`, remove the trap listener. Copy the exact `trapFocus` / `removeTrapFocus` implementation from `acp.html` (grep for `trapFocus`).

**`dashPickerLoad()` — update keep row after load**: Call `dashPickerRenderKeepRow()` inside the `.then()` handler after setting `_dashPickerCapacity`, so the displayed slot count reflects the server's actual `max` value.

**Init on DOM load**: In `DOMContentLoaded` (or equivalent boot sequence), call `dashPickerInitTaskMode()`.

**Exit criteria:**
- [x] `dashPickerOpen(cwd)` shows `#dashPicker`, resets task mode to `kiro_default`, pre-fills filter when `cwd` given, focuses search, loads workspaces
- [x] `#dashPickerNote` shows correct capacity string after load (e.g. "3 workspaces · 2/8 sessions open")
- [x] Workspace rows disabled when at capacity; `#dashPickerNeutral` disabled at capacity
- [x] `dashPickerCreate('')` (neutral option) sends `{type:'new', payload:{cwd:'', mode:'kiro_default'}}` over `_dashWs`
- [x] `dashPickerCreate(cwd)` with `wantsClose` true sends `close` then creates after `session_closed`
- [x] `#dashPickerKeepRow` hidden when `_dashAttachedSid` is null; shown when non-null
- [x] `dashPickerClose()` clears `_dashPendingCreate` and closes task-mode submenu
- [x] Focus is trapped within `#dashPicker` while it is open; Tab wraps correctly; focus released on close
- [x] Escape closes task-mode submenu first, then picker on second press
- [x] `dashPickerLoad()` calls `dashPickerRenderKeepRow()` after updating `_dashPickerCapacity`
- [x] `dashPickerRunPending()` re-checks capacity before sending `new` frame; shows note if at cap
- [x] `close_in_progress` error → `dashPickerRunPending()` called; `turn_in_progress` / `not_subscribed` → `_dashPendingCreate = null` + note shown
- [x] `node tests/acp_page.test.mjs` exits 0 (no new failures — same 5 pre-existing)

**Covers**: SC-2, SC-3 (partial — creation sent), SC-5, SC-6

---

### Phase 4: `dashHandle` creation branch [QA]

**Goal**: Make `dashHandle` correctly process `{type:'session', payload:{created:true}}` frames — setting `_viewingSid`, clearing the transcript, and triggering a targeted rail refresh — before falling through to the existing session-frame handling.

**File scope**: `src/power_atlas/templates/index.html`

**Current state** (`dashHandle` lines 851-868, §`type === 'session'` branch):
```javascript
if (type === 'session') {
  if (sid === _dashLoadingSid && sid !== _viewingSid) {
    send('close', {}, sid);
    _dashLoadingSid = null;
    _dashPendingSend = null;
    return;
  }
  if (sid !== _viewingSid) return;  // <-- silently drops created frames today
  _dashAttachedSid = sid;
  ...
  return;
}
```

**Fix**: Insert a `payload.created` guard at the top of the `type === 'session'` block, before the existing `_dashLoadingSid` and `_viewingSid` guards:

```javascript
if (type === 'session') {
  // New-session creation: adopt the new session before any _viewingSid guards
  if (payload && payload.created) {
    // Set viewing sid to the new session and clear the transcript pane
    _viewingSid = sid;
    _dashLoadingSid = null; // creation is not a load-slot; clear to avoid the abandon-close path
    var transcriptEl = document.getElementById('dashTranscript');
    if (transcriptEl) transcriptEl.textContent = '';
    // Show composer immediately — no dashMaybeAttach needed; this page created the session
    dashComposerEl.hidden = false;
    dashPromptInput.disabled = false;
    dashSetComposerNote('');
    _dashOrigin = 'dashboard';
    _dashSent = false;
    // Targeted rail refresh for the new session's workspace
    dashPickerRailAdopt(payload.cwd || '');
    // Fall through to the normal session-frame handling below
    // (it will now see _viewingSid === sid and set _dashAttachedSid, _dashTurnActive, etc.)
  }
  if (sid === _dashLoadingSid && sid !== _viewingSid) {
    // ... (existing code unchanged)
  }
  ...
}
```

> **Note**: `dashMaybeAttach` is NOT called here. That function fetches `/api/session-availability` to decide between `held`/`available`/`locked` states. A session this page just created is definitively `held` by this page — the availability check is unnecessary overhead and would race with the session being registered. Instead, show the composer directly and set `_dashOrigin = 'dashboard'` so `dashCloseIfAbandoned` handles cleanup correctly.

**`dashPickerRailAdopt(cwd)`** — targeted single-workspace refresh:
```javascript
function dashPickerRailAdopt(cwd) {
  if (!cwd || dashRailMode !== 'project') {
    // In date/status mode or no cwd: reload the flat list
    loadFlatPage(1, true /* silent */);
    return;
  }
  dashRailFetch({
    cwd: cwd,
    session_page: 1, session_size: DASH_RAIL_PROJECT_PRELOAD_SIZE,
  }).then(function(data) {
    (data.groups || []).forEach(function(g) {
      dashRailMergeGroup(g);
      // Move new workspace to front if it was just created
      var key = 'c:' + g.cwd;
      var idx = dashRailGroups.findIndex(function(x) { return 'c:'+x.cwd === key; });
      if (idx > 0) {
        dashRailGroups.splice(0, 0, dashRailGroups.splice(idx, 1)[0]);
      }
    });
    dashRenderRail();
  }).catch(function() {
    // Silent on failure — rail will catch up on next 60s refresh cycle.
    // But ensure the rail re-renders so that any data merged before the error is visible.
    dashRenderRail();
  }); // full reload
}
```

**Exit criteria:**
- [ ] `dashHandle` receives `{type:'session', payload:{created:true, cwd:'...', sessionId:'sess_...'}}`  → `_viewingSid` set, `_dashOrigin = 'dashboard'`, transcript cleared, composer shown and enabled, `dashPickerRailAdopt` called
- [ ] `_dashLoadingSid` cleared in the `payload.created` block (not left for the abandon-close path)
- [ ] Existing `load`/`subscribe` paths (sessions opened by clicking a rail row) still work correctly
- [ ] The abandoned-session close path (`sid === _dashLoadingSid && sid !== _viewingSid`) still fires for non-created sessions
- [ ] `node tests/acp_page.test.mjs` exits 0

**Covers**: SC-3, SC-4

---

### Phase 5: Sparkle dropdown item in `dashRailGroupNode` [QA]

**Goal**: Add the "kiro-cli v3 ACP session" item to the sparkle dropdown in `dashRailGroupNode()`, guarded by `ACP_TOKEN`, calling `dashPickerOpen(group.cwd)`.

**File scope**: `src/power_atlas/templates/index.html`

**Where**: In `dashRailGroupNode()` (index.html ~line 1950-2050), inside the sparkle dropdown builder, immediately before the `_availableProviders` `forEach` loop. The new item is a fixed entry — not from `_availableProviders`.

**Code to add** (before the `_availableProviders` loop):
```javascript
// Fixed ACP new-session item — only when ACP is available
if (ACP_TOKEN) {
  var acpItem = document.createElement('button');
  acpItem.type = 'button';
  acpItem.className = 'acp-rail-menu-item acp-rail-action-item';
  acpItem.setAttribute('role', 'menuitem');
  // Provider icon: kiro-cli-v3
  var acpIcon = document.createElement('img');
  acpIcon.className = 'acp-rail-action-icon';
  acpIcon.src = '/api/launcher-icon/provider--kiro-cli-v3';
  acpIcon.alt = '';
  acpItem.appendChild(acpIcon);
  acpItem.appendChild(document.createTextNode('New kiro-cli v3 ACP session'));
  acpItem.addEventListener('click', function(e) {
    e.stopPropagation();
    _closeAllRailActionMenus();
    dashPickerOpen(group.cwd);
  });
  aiMenu.appendChild(acpItem);
}
```

Note: `group.cwd` is already in scope within `dashRailGroupNode()` as the group data object's `cwd` field (use `group.cwd` — the variable name matches the outer closure in the existing builder).

**Exit criteria:**
- [ ] Sparkle dropdown on each workspace group contains "New kiro-cli v3 ACP session" as its first item when `ACP_TOKEN` is non-null
- [ ] Clicking the item closes the dropdown and opens `#dashPicker` with the workspace's cwd pre-filled in the filter
- [ ] When `ACP_TOKEN` is null, the item is absent — existing provider items are unaffected
- [ ] Existing sparkle items (terminal launches for other providers) still work correctly
- [ ] `node tests/acp_page.test.mjs` exits 0

**Covers**: SC-1, SC-4

---

### Phase 6: Tests in `acp_page.test.mjs` [QA]

**Goal**: Extend `tests/acp_page.test.mjs` with tests covering the new dashboard picker behavior.

**File scope**: `tests/acp_page.test.mjs`

**Test approach**: The existing harness renders `index.html` (not just `acp.html`) via the same Jinja renderer + DOM stand-in. New tests exercise the picker JS functions directly against the rendered DOM.

**Tests to add** (note: `acp_page.test.mjs` uses a check-function pattern — add to the existing `checks` array):

1. **Picker hidden by default**: `document.getElementById('dashPicker').hidden === true`.

2. **`dashPickerOpen` shows the picker**: Call `dashPickerOpen('')`; assert `#dashPicker.hidden === false`.

3. **`dashPickerOpen` resets task mode**: Set `_dashPickedTaskMode = 'spec'`; call `dashPickerOpen('')`; assert `_dashPickedTaskMode === 'kiro_default'` and `#dashPickerTaskModeToggle.dataset.taskMode === 'kiro_default'`.

4. **`dashPickerClose` hides the picker**: Call `dashPickerOpen('')`; call `dashPickerClose()`; assert `#dashPicker.hidden === true`.

5. **Workspace rows disabled at capacity**: Set `_dashPickerWorkspaces = [{cwd:'/a', name:'a', sessions:1}]` and `_dashPickerCapacity = {held:8, max:8}`; call `dashPickerRender()`; assert first workspace button in `#dashPickerList` is `disabled`.

6. **`#dashPickerNeutral` disabled at capacity**: Same setup; assert `#dashPickerNeutral.disabled === true`.

7. **`#dashPickerKeepRow` hidden when no session attached**: Set `_dashAttachedSid = null`; call `dashPickerRenderKeepRow()`; assert `#dashPickerKeepRow.hidden === true`.

8. **`#dashPickerKeepRow` shown when session attached**: Set `_dashAttachedSid = 'sess_test'`; `_dashTurnActive = false`; call `dashPickerRenderKeepRow()`; assert `#dashPickerKeepRow.hidden === false`; assert `#dashPickerCloseCurrent.disabled === false`.

9. **`#dashPickerCloseCurrent` disabled when turn active**: Set `_dashAttachedSid = 'sess_test'`; `_dashTurnActive = true`; call `dashPickerRenderKeepRow()`; assert `#dashPickerCloseCurrent.disabled === true`.

10. **`dashHandle` creation branch sets `_viewingSid`**: Simulate a `{type:'session', sessionId:'sess_new', payload:{created:true, cwd:'/ws'}}` frame by calling `dashHandle({type:'session', sessionId:'sess_new', payload:{created:true, cwd:'/ws'}})`; assert `_viewingSid === 'sess_new'`.

11. **`dashHandle` creation branch adopted over existing `_viewingSid`**: Set `_viewingSid = 'sess_other'`; call `dashHandle({type:'session', sessionId:'sess_new', payload:{created:true, cwd:'/ws'}})`; assert `_viewingSid === 'sess_new'` (creation branch supersedes the stale-guard, which would otherwise drop the frame).

12. **`dashHandle` non-creation path unchanged**: Set `_viewingSid = 'sess_other'`; call `dashHandle({type:'session', sessionId:'sess_new', payload:{}})`; assert `_viewingSid === 'sess_other'` (no `payload.created` → stale-guard fires, frame dropped).

13. **`dashPickerClose` clears `_dashPendingCreate`**: Set `_dashPendingCreate = {cwd:'/x', mode:'kiro_default'}`; call `dashPickerClose()`; assert `_dashPendingCreate === null`.

**Exit criteria:**
- [ ] All 13 new checks pass (exit code 0)
- [ ] No existing `acp_page.test.mjs` checks regress
- [ ] `node tests/acp_page.test.mjs` exits 0

**Covers**: SC-3 (creation flow), SC-4 (non-regression), SC-5 (task mode reset), SC-6 (keep row), SC-7

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `dashHandle` `payload.created` guard drops legitimate load/subscribe responses | High | The guard is `payload && payload.created` — load/subscribe frames have `payload.created` undefined/false, so they are unaffected. Covered by Phase 4 exit criterion and Phase 6 test #12. |
| Close-then-create race: `_dashPendingCreate` not cleared on cancel, close-refused, or navigation | Medium | Fixed in plan: `dashPickerClose()` clears `_dashPendingCreate`; `dashCloseIfAbandoned()` clears it; `turn_in_progress`/`not_subscribed` error arms clear it with a user note. Covered by Phase 6 test #13. |
| CSS cascade order change breaks `acp.html` task-mode picker visually | Medium | `acp.html` inherits `base.html` → `style.css` → the migrated rules. Since there is no other `.acp-taskmode-*` rule anywhere, the cascade is identical post-migration. Verified by `acp_page.test.mjs` Phase 1 exit criterion. |
| `dashPickerRailAdopt` with an empty `cwd` (neutral session) does a flat reload in project mode | Low | Accepted behavior — a neutral session has no workspace cwd; a flat reload in project mode correctly shows all sessions. |
| `dashPickerRailAdopt` catch silently loses the rail row on fetch failure | Medium | Fixed: catch now calls `dashRenderRail()` to at least surface any data merged before the error. Session is still live; next 60s poll will surface it regardless. |
| Stale capacity in `dashPickerRunPending` could create a session beyond the server cap | Medium | Fixed: `dashPickerRunPending()` re-checks `_dashPickerCapacity.held >= max` before sending `new`. Server also enforces the cap and returns an error frame if still exceeded. |

## 7) Verification

**Automated (run after each phase):**
```
node tests/acp_page.test.mjs
```
Exits 0 on pass.

**Manual verification sequence (after Phase 5):**
1. Hard reload (`Ctrl+Shift+R`) the dashboard.
2. Expand any workspace group in the rail. Hover the sparkle (✦) icon.
3. Confirm "New kiro-cli v3 ACP session" appears as the first item in the dropdown.
4. Click it. Confirm `#dashPicker` opens with the workspace's folder name pre-filled in the filter.
5. Confirm the workspace list loads and the capacity note shows correctly (e.g. "N workspaces · X/8 sessions open").
6. Select a workspace. Confirm the picker closes, the transcript panel clears, "Starting the agent." appears briefly, and the new session appears in the rail.
7. Confirm the transcript panel shows the new session's composer, ready to type.
8. Repeat with the neutral "The agent's own folder" option. Confirm session creates with no workspace in the rail.
9. With a session attached: open the picker. Confirm the "Close the session I have open first" checkbox row appears and is enabled.
10. Check the box and create. Confirm the old session closes and the new session opens.
11. Verify all existing sparkle items (terminal launches) still work.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `AGENTS.md` § "ACP UI iteration does not require a restart" | Remove `.acp-taskmode-*` from inline-`<style>` exception list; note it now lives in `style.css` | 1 |

## 9) Implementation Divergences from Plan

<Reserved — filled during implementation>

## Follow-up Work (Deferred)

<Nothing deferred — all risks addressed within this plan.>

## Review Log

### 2026-09-19 — Plan Creation (via /qplan)

18 findings (6 High, 9 Medium, 3 Low). 16 auto-resolved in cycle 1; 2 Low escalated for user review.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `dashPickerClose()` missing `_dashPendingCreate = null` — stale pending create fires on later `session_closed` | Fixed — added to `dashPickerClose()` body; Phase 3 exit criteria updated |
| 2 | High | `close_in_progress` error arm missing in `dashHandle` — server uses both paths to end a close | Fixed — specified three error-handler branches: `close_in_progress`, `turn_in_progress`/`not_subscribed`, generic |
| 3 | High | Created session never gets live-attach — `dashMaybeAttach` never called; composer stays hidden | Fixed — Phase 4 creation branch shows composer directly, sets `_dashOrigin = 'dashboard'`; avoids `dashMaybeAttach` availability-check fetch |
| 4 | High | No focus trap on the picker dialog — keyboard users can Tab out | Fixed — Phase 3 now specifies copying `trapFocus`/`removeTrapFocus` from `acp.html`; Phase 3 exit criterion added |
| 5 | High | Task-mode toggle missing `aria-haspopup="menu"` and `aria-label` attributes | Fixed — added to Phase 2 HTML snippet |
| 6 | High | Phase 1 CSS scope under-specifies — `.acp-picker-taskmode` and `.acp-picker-taskmode-label` also needed | Fixed — Phase 1 changes explicitly list these two selectors |
| 7 | Medium | `dashPickerRailAdopt` silent catch swallows failure — new session invisible until next poll | Fixed — catch calls `dashRenderRail()` |
| 8 | Medium | `dashPickerRenderKeepRow` reads `_dashPickerCapacity.max` before load — shows hardcoded 8 initially | Fixed — uses `(_dashPickerCapacity && _dashPickerCapacity.max) \|\| 8`; `dashPickerLoad` calls `dashPickerRenderKeepRow` after updating capacity |
| 9 | Medium | `_dashPendingCreate` not cleared in `dashCloseIfAbandoned` — stale pending fires on back-navigation | Fixed — `dashCloseIfAbandoned` body updated with explicit `_dashPendingCreate = null` |
| 10 | Medium | `dashPickerRunPending` skips capacity re-check | Fixed — capacity check added before `send('new')` |
| 11 | Medium | `dashPickerTaskModeMenu` not closed in `dashPickerClose()` | Fixed — `dashPickerClose()` spec updated |
| 12 | Medium | No visual feedback between picker close and socket-first-open delay | Fixed — Phase 4's `payload.created` block now shows composer and composer note directly |
| 13 | Medium | Test #11 doesn't discriminate the Phase 4 fix | Fixed — replaced with discriminating test; added test #12 (non-created path) and #13 (`dashPickerClose` clears pending) |
| 14 | Medium | Phase 2 exit criterion says "9 child elements" but lists 11 | Fixed — count corrected to 11 |
| 15 | Medium | Escape doesn't layer-dismiss (submenu first, picker second) | Fixed — Phase 3 Escape handler now specifies layered dismiss |
| 16 | Medium | `_dashLoadingSid` not cleared in creation branch — abandon-close path could fire for created sessions | Fixed — `_dashLoadingSid = null` added to `payload.created` block |
| 17 | Low | `dashPickerOpen` pre-fills filter with full cwd path — shows raw path in search box | Escalated — user confirmed pre-filling cwd during exploration (Q3). The filter *is* searched against the full path, so this is correct behavior. Workspace is typically highlighted visually. |
| 18 | Low | Task-mode list missing `autonomous` and `vibe` modes | Escalated — the project memory notes `kiro_default` is machine-specific and mode availability varies. Matching `/acp`'s 6-mode list is intentional scope per SC-5 "match /acp". A follow-up could fetch modes dynamically. |

## Harness Improvement Opportunities

- Dispatching three separate sub-agents before the interview is correct per the skill, but the skill's probe-gate instruction ("run every probe on the decidable-by-probe list before forming a single question") arrives only after reading `shared.md` — which is cited but not pre-loaded. The probe gate is non-trivially expensive to miss (as it was here on the first round). Suggested change: load `shared.md` automatically alongside `SKILL.md` in the qexplore skill activation, or include the probe-gate rule inline in SKILL.md. — cost: one extra interview round and user correction.


### 2026-09-19 — Phase 3 review (Senior Engineer + Reliability Engineer, high effort)

2 findings (0 High, 1 Medium, 1 Low). 1 auto-resolved (fix commit 6bfbac7).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | `dashPickerCreate` did not check `send('close')` return value — if socket drops between attach and create press, `_dashPendingCreate` is set but close never fires | Fixed — added guard matching `acp.html` line 4875; commit 6bfbac7 |
| 2 | Low | `_dashTrapFocus` appends inputs before buttons without comment — correct for today but fragile for future editors | Accepted — ordering is correct and comment added inline |
