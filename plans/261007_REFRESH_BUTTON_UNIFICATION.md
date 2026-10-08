# Refresh Button Unification

> **Date**: 2026-10-07
> **Status**: Draft  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: 2026-10-07 11:03
> **Scope**: Unify the three refresh buttons across dashboard and ACP into one consistent control per page; make manual refresh always do the full deep path; polish loading state and error feedback
> **Estimated effort**: Half-day


## Intent

### Problem statement & desired outcomes

PowerAtlas has three refresh controls with mismatched appearance and behavior:

- **Dashboard topbar** (`.refresh-btn`, 28 px round, spinning animation, last-refresh timestamp) — calls `POST /api/refresh`: clears both server caches, runs `warmup_all`, re-renders the rail. The only control that forces workspace re-discovery.
- **Dashboard rail** (`#dashRailReload`, `.ws-icon-btn`, 32 px square, no loading state) — calls `dashRailReload()`: re-renders from already-warm server cache. Cannot surface brand-new workspaces or sessions created after the last cache fill.
- **ACP rail** (`#acpRailReload`, `.acp-btn`, unsized text pill "Refresh", no loading state) — calls `railLoadFirstPage()`: same shallow behavior. The ACP page has no deep-refresh path at all.

User-reported symptom: new workspace sessions and liveness dots frequently don't appear until the topbar button is pressed, because the shallow rail buttons cannot bust the 30-second server-side cache. The fix that works exists; it just isn't available everywhere and is inconsistently presented.

**Desired outcomes:**
- One Refresh button per page, both doing the same full deep path
- Consistent visual style: `.ws-icon-btn` (32 px square icon), matching rotating-arrow SVG
- Loading state (spin + disable) during the operation on both pages
- Error surfaced in the rail status line on failure (currently silent on the topbar path)
- The dashboard topbar button and `.refresh-group` removed; the surviving dashboard button is `#dashRailReload` in the rail head

### What is NOT changing
- The 60-second auto-poll timers (`dashRailRefresh()` / `railRefresh()`) remain shallow — they are background maintenance, not user-triggered
- `dashRailReload()` itself stays shallow; all 4 non-button call sites (pollWarmup, switchProvider, dashRailInit, and the 60 s timer chain) are unaffected
- `railLoadFirstPage()` similarly stays shallow; its 2 non-button call sites (setSortMode, boot/reconnect) are unaffected
- `POST /api/refresh` server implementation is unchanged
- No changes to `web.py`, `data.py`, or any Python file

### Success Criteria

- **SC-1** `index.html` — Dashboard deep refresh:
  - The topbar `.refresh-group` div (button + `#refreshTime` span) is removed
  - `#dashRailReload` `aria-label` updated to `"Refresh sessions"` and `title` updated to `"Refresh all sessions now"` (was "Refresh workspaces" / "Refresh")
  - `#dashRailReload` click handler replaced with a new `dashDeepRefresh()` function that: disables + spins the button, calls `POST /api/refresh`, on success calls `refreshSettings()` then `dashRailLoadFirstPage()`, on error shows "Refresh failed" in the rail status, re-enables button in either case
  - The old `doRefresh()` function is removed entirely
  - Note: `last_refresh` timestamp is no longer displayed — the rail summary overwrites the status line synchronously before paint

- **SC-2** `acp.html` — ACP deep refresh:
  - `#acpRailReload` button class changed from `.acp-btn` (text pill) to `.ws-icon-btn`; text content replaced with the matching rotating-arrow SVG
  - Click handler replaced with a new `railDeepRefresh()` function (defined inside the IIFE) that: disables + spins the button, calls `POST /api/refresh`, on success calls `railLoadFirstPage()`, on error shows "Refresh failed" in `railStatus`, re-enables button in either case
  - No `refreshSettings()` call on ACP (no launcher tiles)

- **SC-3** `style.css` — Consistent loading state + dead code removal:
  - `.ws-icon-btn.spinning svg { animation: topbar-spin 0.8s linear infinite }` rule added (reuses existing `@keyframes topbar-spin`)
  - `.ws-icon-btn:disabled { opacity: 0.5; cursor: wait }` rule added
  - Orphaned CSS classes removed: `.refresh-group`, `.refresh-time`, `.refresh-btn` (all variants)

- **SC-4** `tests/acp_page.test.mjs` — Test passes unchanged:
  - `node tests/acp_page.test.mjs` exits 0 — no stub or harness changes needed (the test harness's `fakeFetch` already handles `POST /api/refresh` gracefully; `railDeepRefresh()` ignores the response body)


## 1) Current State

Three refresh controls exist across two pages — see Intent. Key code anchors:

- `doRefresh()` — `index.html:8098`, minified single line; calls `POST /api/refresh` then `refreshSettings()` then `dashRailReload()`; uses `document.querySelector('.refresh-btn')` to find its own button
- `dashRailReloadBtn.addEventListener('click', ...)` — `index.html:7948`, wires rail button to `dashRailReload()` (shallow)
- `railReload.addEventListener('click', ...)` — `acp.html:6004`, wires ACP button to `railLoadFirstPage()` (shallow)
- `.refresh-btn` CSS — `style.css:43-57`, includes the `@keyframes topbar-spin` animation that will be reused
- `.ws-icon-btn` CSS — `style.css:423-442`, the target class for both buttons post-change; no `:disabled` rule currently
- `dashRailStatusEl` — `index.html:6011`, module-level var; receives status text (e.g. "loading workspaces…")
- `railStatus` — `acp.html:755` (approx), IIFE-scoped var; same purpose on ACP


## 2) Goal

Replace the three refresh controls with one `#dashRailReload` button on the dashboard and one `#acpRailReload` button on ACP — both using `.ws-icon-btn` style, both wired to `POST /api/refresh` with loading state and error feedback. The topbar `.refresh-group` is removed. The ACP button becomes an icon button. Dead CSS is cleaned up.


## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Manual refresh depth | Deep path (`POST /api/refresh`) on all manual button clicks | Shallow re-render from cache | Background refresh only updates known workspaces; new workspaces need `warmup_all` to surface; user confirmed staleness is the real symptom |
| Dashboard button location | Rail head (`#dashRailReload` upgraded in-place) | Keep topbar button | Puts the button in the same relative position as ACP's button; removes asymmetry between pages |
| `dashRailReload()` behavior | Stays shallow; only the click handler upgrades | Change `dashRailReload()` itself | Has 4 other call sites (pollWarmup, switchProvider, dashRailInit, 60 s timer) that must stay shallow |
| Error display | Rail status text on failure | Toast / modal | Rail status is already the feedback channel; consistent with existing `dashRailFailed()` / `railFailed()` patterns |
| `last_refresh` display | Dropped — setting the rail status line is immediately overwritten by `dashRailSummary()` before the browser can paint, making it invisible. Timestamp is gone. | Show in rail status; keep in button title | Synchronous overwrite makes the status-line approach unreachable without restructuring the promise chain; informational value low |
| ACP IIFE scope | Define `railDeepRefresh()` inside the IIFE | Module-level function | `railReload`, `railStatus`, and `railLoadFirstPage` are all IIFE-private; the new function must be co-located |


## 4) External Dependencies & Costs

### Required external changes

None. Pure front-end JS/HTML/CSS change; no server, infra, data, or external service changes.

### Cost impact

None.


## 5) Implementation Phases

### Phase 1: CSS — spinning + disabled rules, dead code removal [P:2,3]
**Goal**: Add the two missing `.ws-icon-btn` rules and remove the now-orphaned `.refresh-btn` / `.refresh-group` / `.refresh-time` CSS.

**File scope**: `src/power_atlas/static/style.css`

**Covers**: SC-3

Changes:

1. **Add** after the existing `.ws-icon-btn` block (around `style.css:442`):
```css
.ws-icon-btn.spinning svg { animation: topbar-spin 0.8s linear infinite; }
.ws-icon-btn:disabled { opacity: 0.5; cursor: wait; }
```

2. **Remove** the following rules (search by selector, not line range — the range 43-57 also contains `.topbar-select:hover` and `.trust-dot` at lines 48-49 which must NOT be removed):
```css
.refresh-group { ... }       ← remove
.refresh-time { ... }        ← remove
.refresh-btn { ... }         ← remove (first block, sizing/color)
.refresh-btn:hover { ... }   ← remove
.refresh-btn:disabled { ... } ← remove
.refresh-btn { display: flex; ... }   ← remove (second block, layout)
.refresh-btn.spinning svg { ... }     ← remove
```
Keep `.topbar-select:hover`, `.trust-dot`, and `@keyframes topbar-spin` — the keyframe is reused by the new `.ws-icon-btn.spinning svg` rule.

3. **Remove** the in-code comment at `style.css:52-53` (currently reads "Topbar redesign: only Refresh keeps a permanent seat in the row...") — factually wrong after the topbar button is removed.

> **`.topbar-ic` is NOT unused** — doc-impact scan confirmed it is used in 6+ locations (settings modal, profile menu, remote access rows). Do not remove it.

**Exit criteria**:
- [ ] `.ws-icon-btn.spinning svg` rule present in `style.css`
- [ ] `.ws-icon-btn:disabled` rule present in `style.css`
- [ ] `.refresh-btn`, `.refresh-group`, `.refresh-time` class rules absent from `style.css`
- [ ] `@keyframes topbar-spin` still present
- [ ] In-code "only Refresh keeps a permanent seat" comment absent from `style.css`


### Phase 2: Dashboard — remove topbar button, add `dashDeepRefresh()` [P:1,3] [QA]
**Goal**: Remove the topbar `.refresh-group` div and `doRefresh()` function; upgrade `#dashRailReload`'s click handler to the deep path.

**File scope**: `src/power_atlas/templates/index.html`

**Covers**: SC-1

Changes:

1. **Remove** the topbar `.refresh-group` div (HTML around `index.html:6-10`):
```html
<!-- REMOVE this entire div: -->
<div class="refresh-group" title="Last data refresh time">
  <span class="refresh-time" id="refreshTime"></span>
  <button class="refresh-btn" onclick="doRefresh()" ...>
    <svg class="topbar-ic" ...></svg>
  </button>
</div>
```
> **Do NOT remove `.topbar-ic`** — doc-impact scan confirmed it is used in 6+ other locations.

2. **Remove** the startup `/api/last-refresh` fetch (around `index.html:5875`) that sets `refreshTime.textContent` at `DOMContentLoaded` — the target element no longer exists after step 1. Remove this in the same edit as step 1; if the element is removed while this fetch code survives, the `getElementById` will return null and throw a TypeError on page load.

3. **Remove** `doRefresh()` function (currently `index.html:8098`). It is no longer needed — its behavior is replaced by `dashDeepRefresh()` below.

4. **Update** `#dashRailReload` button attributes (currently `index.html:212-213`): change `aria-label="Refresh workspaces"` to `aria-label="Refresh sessions"` and `title="Refresh"` to `title="Refresh all sessions now"` to reflect the new deep-refresh semantics.

5. **Replace** the `#dashRailReload` click handler (currently `index.html:7948`):
```js
// Before:
dashRailReloadBtn.addEventListener('click', function(){ dashRailReload(); });
// After:
dashRailReloadBtn.addEventListener('click', dashDeepRefresh);
```

6. **Add** `dashDeepRefresh()` immediately after `dashRailReload()` at `index.html:8073` (the natural grouping point for deep/shallow rail functions):
```js
function dashDeepRefresh() {
  var btn = dashRailReloadBtn;
  btn.disabled = true;
  btn.classList.add('spinning');
  fetch('/api/refresh', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function() {
      refreshSettings();
      dashRailLoadFirstPage();
    })
    .catch(function() {
      dashRailStatusEl.textContent = 'Refresh failed \u2014 try again';
    })
    .finally(function() {
      btn.disabled = false;
      btn.classList.remove('spinning');
    });
}
```
> **Note**: the `last_refresh` value from the response is not displayed — the rail status line is synchronously overwritten by `loadGroupPage()`'s "loading workspaces…" before the browser can paint, making any prior assignment invisible.

> **No other call sites change**: `dashRailReload()` (shallow) remains used by pollWarmup, switchProvider, dashRailInit, and the auto-timer. Only the button's click handler calls `dashDeepRefresh()`.

**Exit criteria**:
- [ ] `.refresh-group` div absent from `index.html`
- [ ] `doRefresh` function absent from `index.html` (grep returns no hits)
- [ ] Startup `/api/last-refresh` fetch that sets `refreshTime.textContent` removed in the same commit as the HTML element
- [ ] `#dashRailReload` carries `aria-label="Refresh sessions"` and `title="Refresh all sessions now"`
- [ ] `dashDeepRefresh` function present immediately after `dashRailReload()` (~line 8073) and wired to `#dashRailReload` click
- [ ] `dashRailReload()` still called by pollWarmup, switchProvider, dashRailInit (no regressions to those paths)
- [ ] Hard reload of the dashboard in a running PowerAtlas shows one Refresh button in the rail head (not in topbar); clicking it spins the button and the rail re-renders
- [ ] `node tests/acp_page.test.mjs` suite unaffected (no references to dashboard's doRefresh)
- [ ] `plans/tests/260701_POWERATLAS.md` refresh probe entry updated to describe the new rail-head deep-refresh behavior (no `#refreshTime` reference)


### Phase 3: ACP — icon button + `railDeepRefresh()` [P:1,2] [QA]
**Goal**: Convert `#acpRailReload` from a text pill to an icon button and wire it to the deep path.

**File scope**: `src/power_atlas/templates/acp.html`

**Covers**: SC-2

Changes:

1. **Replace** the `#acpRailReload` button HTML (currently `acp.html:81`):
```html
<!-- Before: -->
<button class="acp-btn" id="acpRailReload" type="button">Refresh</button>

<!-- After: -->
<button class="ws-icon-btn" id="acpRailReload" type="button"
        aria-label="Refresh sessions" title="Refresh all sessions now">
  <svg class="ws-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 4v5h-5"/>
  </svg>
</button>
```
> **`[hidden]` trap**: `.ws-icon-btn` carries `display: inline-flex`, which beats `[hidden]`'s UA `display: none`. The `.ws-icon-btn[hidden] { display: none }` escape rule already exists at `style.css:438` — no action needed, but verify the button is never hidden via the `hidden` attribute in a path not covered by that rule.

2. **Replace** the click handler (currently `acp.html:6004`, inside the IIFE):
```js
// Before:
railReload.addEventListener('click', function () { railLoadFirstPage(); });

// After:
railReload.addEventListener('click', railDeepRefresh);
```

3. **Add** `railDeepRefresh` function inside the IIFE, immediately after `railLoadFirstPage()` at `acp.html:2469`:
```js
function railDeepRefresh() {
  railReload.disabled = true;
  railReload.classList.add('spinning');
  fetch('/api/refresh', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function() {
      railLoadFirstPage();
    })
    .catch(function() {
      railStatus.textContent = 'Refresh failed \u2014 try again';
    })
    .finally(function() {
      railReload.disabled = false;
      railReload.classList.remove('spinning');
    });
}
```

> **No `refreshSettings()` call**: ACP page has no launcher tiles or settings display to refresh.
> **IIFE scope**: `railReload`, `railStatus`, and `railLoadFirstPage` are all IIFE-private. `railDeepRefresh` must be defined inside the same IIFE.
> **Other call sites unchanged**: `railLoadFirstPage()` is still called by setSortMode and on boot/reconnect.

**Exit criteria**:
- [ ] `#acpRailReload` carries class `ws-icon-btn` (not `acp-btn`) and contains an SVG child
- [ ] `railDeepRefresh` defined inside the IIFE and wired to the click handler
- [ ] `railLoadFirstPage()` still called by setSortMode and boot/reconnect (no regressions)
- [ ] `acp_page.test.mjs:3765` — `page.el("acpRailReload").disabled === true` still passes (the disabled-on-module-failure path is unchanged)
- [ ] Hard reload of `/acp` in a running PowerAtlas shows the icon Refresh button; clicking it spins and shows "Refresh failed" or reloads the rail


### Phase 4: Test — verify `acp_page.test.mjs` still passes
**Goal**: Confirm the test suite passes after Phase 3's behavioral change to `#acpRailReload`.

**File scope**: `tests/acp_page.test.mjs` (likely no changes needed)

**Covers**: SC-4

> Phase 4 depends on Phase 3 (it tests the changed ACP behavior).

No stub or harness changes are needed. The test harness uses a `fakeFetch` function that returns `Promise.resolve({})` for unrecognized URLs (including `POST /api/refresh`). `railDeepRefresh()` does not read the response body — it only calls `railLoadFirstPage()` in the success `.then()` regardless of the response value. The entire promise chain resolves in microtasks before any `setImmediate`, so the existing `page.settle()` call at the end of the test is sufficient to drain it.

Verification:
1. Run `node tests/acp_page.test.mjs`
2. Confirm test at ~line 4303 (click `#acpRailReload`, capacity re-check) still passes
3. Confirm test at ~line 3765 (`#acpRailReload.disabled === true` on module failure) still passes

If the test at line 4303 unexpectedly fails, the likely cause is that the promise chain requires an extra `page.settle()` call (two async hops instead of one). In that case, add one more `await page.settle()` before the assertion — do not add a fetch stub.

**Exit criteria**:
- [ ] `node tests/acp_page.test.mjs` exits 0 with no failures
- [ ] The test at ~line 3765 (`disabled === true` on module failure) still passes
- [ ] The test at ~line 4303 (capacity re-check after click) still passes


## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `dashRailReload()` accidentally changed to deep path | High — every tab-focus, boot, and provider-filter click triggers a full disk scan | Phase 2 exit criterion explicitly verifies the 4 non-button call sites still call the shallow path |
| `railDeepRefresh` defined outside the IIFE — `railReload`/`railStatus`/`railLoadFirstPage` out of scope | High — runtime ReferenceError on click | Phase 3 implementation note flags this; exit criterion checks button click works in a running PA |
| `.ws-icon-btn[hidden]` display trap — button becomes permanently visible | Medium — cosmetic; button shows when it should be hidden | Phase 3 notes the existing escape rule at `style.css:438`; exit criterion should verify |
| Test fetch stubbing mechanism unknown — `page.stubFetch` may not exist | Medium — Phase 4 blocked | Implementer reads existing test stubs in `acp_page.test.mjs` before writing; if no stub mechanism exists, add one consistent with the harness |
| `railStatus` variable name in the IIFE — may differ from assumed name | Low — referencing wrong var causes silent failure | Phase 3 exit criterion verifies error text appears in the status line |


## 7) Verification

After all phases land:
1. Hard reload dashboard (`Ctrl+Shift+R`) — one Refresh button in the rail head only, none in topbar
2. Click the dashboard Refresh button — button spins and disables, rail re-renders with fresh data, rail status shows workspace count; new workspace sessions appear without needing the old topbar button
3. Hard reload `/acp` — Refresh button is an icon (not text), matches the dashboard rail button visually
4. Click the ACP Refresh button — same loading behavior as dashboard
5. `node tests/acp_page.test.mjs` exits 0
6. No `doRefresh` in `index.html` (grep clean)
7. No `.refresh-btn`, `.refresh-group`, `.refresh-time` in `style.css` (grep clean)
8. Simulate POST /api/refresh failure (e.g., stop the server briefly): rail status shows "Refresh failed — try again"; button re-enables


## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `plans/tests/260701_POWERATLAS.md` | Lines 273–277: rewrite the refresh button probe entry — remove `refreshTime` reference, describe new rail-head icon button and status-line feedback | 2 |
| `src/power_atlas/static/style.css` | Lines 52–53: remove or rewrite the "only Refresh keeps a permanent seat" in-code comment above `.topbar-ic` | 1 |
| `plans/ROADMAP.md` | Verify no live references to `doRefresh` or topbar refresh pattern — remove if found | 2 |


## Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | CSS — spinning + disabled rules, dead code removal | Not started | |
| 2 | Dashboard — remove topbar button, add `dashDeepRefresh()` | Not started | |
| 3 | ACP — icon button + `railDeepRefresh()` | Not started | |
| 4 | Test — stub POST /api/refresh in acp_page.test.mjs | Not started | |


## 9) Implementation Divergences from Plan
<!-- Reserved — filled during implementation -->


## Follow-up Work (Deferred)

None identified.


## Review Log

### 2026-10-07 — /qplan Step 4 review (Architect, Senior engineer, Frontend/UX)

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | `'refreshed HH:MM:SS'` written to `dashRailStatusEl` is synchronously overwritten by `'loading workspaces…'` before any browser paint — user never sees it and SC-1's exit criterion was unreachable | Auto-fixed: removed the timestamp write from `dashDeepRefresh()` and updated SC-1 and Design Decisions accordingly |
| 2 | Medium | `#dashRailReload` retains `aria-label="Refresh workspaces"` and `title="Refresh"` after behavior changes to deep-refresh — inconsistent with proposed `#acpRailReload` labels | Auto-fixed: Phase 2 now updates both attributes to `aria-label="Refresh sessions"` / `title="Refresh all sessions now"` |
| 3 | Medium | Phase 4 references `page.stubFetch()` which does not exist in the test harness; no stub is actually needed (`fakeFetch` returns `{}` for unknown URLs; `railDeepRefresh()` ignores the response body) | Auto-fixed: Phase 4 rewritten — no stub needed; added fallback note about extra `settle()` if the chain unexpectedly needs two hops |
| 4 | Medium | CSS "range 43-57" encompasses `.topbar-select:hover` and `.trust-dot` (lines 48-49) which must not be removed | Auto-fixed: Phase 1 now lists rules by selector with explicit guard; line range removed |
| 5 | Low | `railDeepRefresh()` IIFE placement vague | Auto-fixed: specified as "immediately after `railLoadFirstPage()` at acp.html:2469" |
| 6 | Low | `dashDeepRefresh()` insertion point listed two options | Auto-fixed: specified as "immediately after `dashRailReload()` at index.html:8073" |
| 7 | Low | `railStatus` line reference slightly off (~767 vs actual ~755) | Auto-fixed |


## Harness Improvement Opportunities
<!-- Reserved -->
