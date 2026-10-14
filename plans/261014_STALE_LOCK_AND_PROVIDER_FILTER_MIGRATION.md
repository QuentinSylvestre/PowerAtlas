# Stale Lock Fix and Provider Filter Migration

> **Date**: 2026-10-14
> **Status**: Draft  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Fix stale in_progress session lock; migrate provider filter chips into rail panel
> **Memory relied on**: none
> **Estimated effort**: 0.5–1 day

---

## Intent

### Problem statement & desired outcomes

**Change 1 — Stale session lock.** When kiro-cli is killed ungracefully (laptop restart,
force-quit, crash), `session.json` is left with `status: "in_progress"`. PowerAtlas reads
this and greys out the row in the dashboard. The only current recovery mechanism is a
30-minute mtime-staleness window: once `session.json` has not been touched for 1800 s,
PA decides the process is gone and unlocks. This means after any hard restart, stale rows
persist for up to 30 minutes with no user-actionable recovery path.

**Change 2 — Provider filter strip.** The provider filter chips (All | Claude Code | Codex |
kiro-cli v3 | Kiro IDE) live in a `.search-area` div between the topbar and the three-panel
layout. This strip consumes vertical space even though the functionality fits naturally
inside the existing `#wsFilterMenu` popover alongside Tag and Time filters.

**Desired outcomes:**
- Stale-locked rows become available on the next listing refresh (~5 s) after a laptop
  restart or force-quit, with no user intervention.
- The provider filter strip is removed; provider filtering works from the existing
  filter-funnel button (`#wsFilterBtn`) in the workspace panel header.

### Success criteria

- SC-1: After a system restart or kiro-cli force-quit, session rows that were `in_progress`
  unlock within one listing refresh (≤5 s after PA next renders the session list).
- SC-2: `Snapshot.any_live_process(providers)` method added; `_acp_availability` short-circuits
  to `"available"` for non-held `in_progress` sessions when the snapshot shows no kiro-cli
  process running.
- SC-3: Provider filter chips render inside `#wsFilterMenu` as a new "Provider" section
  (top of menu, above Tag); selection drives `_activeProvider` and triggers `dashRailReload`
  exactly as before.
- SC-4: `wsFilterDot` badge activates when provider ≠ `"all"`; "Clear filters" resets
  `_activeProvider` to `"all"` alongside tag/time.
- SC-5: `.search-area` div removed from `index.html`; its CSS rules removed from
  `style.css`; `initProviderFilter` rewritten using `createElement`/`textContent`/`appendChild`
  (no `innerHTML` — AGENTS.md prohibition).
- SC-6: All existing tests pass; new unit tests added for both changes.

### Scope boundaries & non-goals

**In scope:**
- `presence.py`: add `Snapshot.any_live_process(providers)` method.
- `web.py`: add process-presence short-circuit in `_acp_availability`.
- `index.html`: add Provider section to `renderWorkspaceFilterMenu`; rewrite
  `initProviderFilter`; remove `.search-area` div.
- `style.css`: remove `.search-area` and `.provider-filter` / `.provider-filter-btn`
  CSS rules.
- `tests/test_web.py`: extend `TestAcpAvailabilityV3`.
- `tests/acp_page.test.mjs`: add tests for the new Provider section.

**Out of scope:**
- Reducing `_V3_SESSION_STALE_SECONDS` (the 30-min mtime window) — the process-presence
  check already covers the target failure modes; the window remains as a fallback.
- Any change to the 30-min mtime behavior when kiro IS running.
- Provider filter persistence across PA restarts.

---

## 1) Current State

**Change 1:**
- `presence.py:389` — `Snapshot` class; `_live_cwds: set[tuple[str, str]]` stores
  `(provider, normalized_cwd)` for every live kiro-cli process.
- `presence.py:827` — `get_snapshot()` returns a cached snapshot (TTL: 3 s); scan uses
  `psutil.process_iter(["pid","name","cmdline"])`.
- `presence.py:102` — `_KIRO_PROVIDERS = frozenset({"kiro-cli-v3"})`.
- `acp.py:3423` — `_lock_holder_v3`: reads `session.json` status; returns
  `_V3_HOLDER_PID_UNKNOWN` if `in_progress`/`waiting_on_user` AND mtime fresh (< 1800 s).
- `web.py:3275` — `_acp_availability(session_ids, held, workspace_hashes)`: sole caller
  of `_lock_holder_v3`; already imports `presence`; `presence.get_snapshot()` is called
  in both caller scopes (`_acp_listing`, `_acp_flat_listing`) but NOT threaded through.
  Returns `"held"`, `"locked"`, or `"available"`. Fails open to `"available"` on any
  exception (web.py:3287-3288, confirmed by code read 2026-10-14).
- 6 kiro-cli processes running at time of probe (PA's own ACP pool — none carry
  `--resume-id <session_id>` so they never appear in `_live_sids`).
- Tests: `TestAcpAvailabilityV3` at `tests/test_web.py:21660` (5 tests, all monkeypatch
  `acp._lock_holder_v3`, none currently mock `presence.get_snapshot`); `_lock_holder_v3`
  unit tests are in `tests/test_data_kiro_v3.py` (no class named `TestLockHolderV3` exists
  in `test_web.py`; confirmed 2026-10-14).

**Change 2:**
- `index.html:88-93` — `.search-area` div wraps `#providerFilter`; sits between `.topbar`
  and `<main class="panels-container">`.
- `index.html:6840` — `initProviderFilter()`: fetches `/api/available-providers`, sets
  `window._availableProviders`, builds chips via **innerHTML** (AGENTS.md violation to fix),
  hides `container.parentNode` (`.search-area`) when < 2 providers.
- `index.html:6841` — `switchProvider(provider)`: sets `_activeProvider`, updates
  `.provider-filter-btn` aria-selected, calls `dashRailReload()`.
- `index.html:9227` — `renderWorkspaceFilterMenu()`: builds `#wsFilterMenu` content via
  `createElement`/`textContent`. Sections: Tag → divider → Time → optional Clear.
- `index.html:614` — `window._activeProvider = 'all'` initial state.
- `index.html:6815` — `initProviderFilter(); initWorkspaceFilters();` called at
  `DOMContentLoaded`.
- `_buildWorkspaceQs` (index.html:623) — dead code (confirmed by comment at line 617
  and search showing no callers); `loadGroupPage`/`loadFlatPage` inline `_activeProvider`
  directly.
- `window._availableProviders` read by two other consumers: workspace AI quick-launch
  dropdown (`index.html:8137`) and provider settings modal lookup (`index.html:9670`).
  Both read the global, not the DOM — safe to remove the chips.
- `style.css:319` — `.search-area { display:flex; padding:8px 16px; flex-shrink:0; }`
  + `[hidden]` rule.
- `style.css:944` — `.provider-filter`, `.provider-filter-btn`, `.provider-filter-btn.active`,
  `.provider-filter-icon` rules.
- No existing tests cover `initProviderFilter`, `switchProvider`, or `_activeProvider`.

---

## 2) Goal

Add a process-presence short-circuit to `_acp_availability` so stale `in_progress` sessions
unlock immediately when no kiro-cli process is running, and migrate the provider filter chips
into `#wsFilterMenu` so the top strip is removed and vertical space is recovered.

---

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Where to add the process-presence check | `_acp_availability` in `web.py` | Inside `_lock_holder_v3` in `acp.py` | `web.py` already imports `presence`; calling `presence.get_snapshot()` in `_acp_availability` reuses the 3 s TTL cache built for each listing refresh rather than doing a separate psutil scan per session id. `acp.py`'s isolation boundary only restricts power_atlas module imports; psutil is allowed, but duplicating the scan is wasteful. |
| New Snapshot method vs inline check | `Snapshot.any_live_process(providers)` method | Inline `any(prov in providers for prov,_ in snap._live_cwds)` in `_acp_availability` | The method keeps `presence.py`'s internals encapsulated; callers should not access `_live_cwds` directly. Adding a public method is the cleanest contract. |
| Scope of process-presence check | "no kiro at all" short-circuit only | Also reduce `_V3_SESSION_STALE_SECONDS` | User confirmed: fix the "no kiro running" case only; leave the 1800 s mtime window unchanged for when kiro IS running. |
| Provider filter placement | New "Provider" section at top of `#wsFilterMenu` | Dedicated new button in `.ws-icon-group` | User confirmed this. Single filter button reduces toolbar clutter; `wsFilterDot` and "Clear filters" already cover state indication. `renderWorkspaceFilterMenu` already uses the correct DOM pattern. |
| Provider section position in menu | Top (before Tag section) | After Time section | Provider is a coarser filter than tag/time; placing it first matches the hierarchy of specificity. |
| `initProviderFilter` responsibility after migration | Load `_availableProviders` only; no chip HTML | Keep building chips | Chips are no longer in the DOM; function's only job becomes fetching the provider list and ensuring `renderWorkspaceFilterMenu` has it when needed. |

---

## 4) External Dependencies & Costs

### Required external changes

None. All changes are local to the PowerAtlas repo.

### Cost impact

None.

---

## 5) Implementation Phases

### Phase 1: Process-presence short-circuit in `_acp_availability` [QA] [P:2]

**Goal**: Add `Snapshot.any_live_process(providers)` to `presence.py` and use it in
`_acp_availability` to immediately return `"available"` for non-held `in_progress` sessions
when no kiro-cli process is in the snapshot.

**Covers**: SC-1, SC-2, SC-6 (presence/web tests)

**File scope**: `src/power_atlas/presence.py`, `src/power_atlas/web.py`,
`tests/test_web.py`

**Details:**

1. **`presence.py` — add `Snapshot.any_live_process(providers)`:**

   ```python
   # Sketch only — implementer writes final form
   def any_live_process(self, providers: frozenset[str]) -> bool:
       """Return True if the scan saw at least one process for any of these providers.

       Uses _live_cwds rather than _live_sids: an ACP session has no session id
       on argv and therefore never appears in _live_sids, but its process still
       occupies a cwd, so _live_cwds is the right signal. Returns False when
       psutil was unavailable (_live_cwds is empty).
       """
       return any(p in providers for p, _ in self._live_cwds)
   ```

   Add this method to the `Snapshot` class (after `live_session_ids_for_cwd` at ~line 523,
   following the existing trailing-keyword-default ordering rule for new attributes). Because
   the method reads `_live_cwds` which is set in `__init__`, no constructor changes needed.

2. **`web.py` — short-circuit in `_acp_availability`:**

   Inside `_acp_availability`, call `presence.get_snapshot()` once at the top of the loop
   (before the per-id loop) and check `snapshot.any_live_process(presence._KIRO_PROVIDERS)`.
   When it returns `False`, skip `acp._lock_holder_v3` and return `"available"` for every
   non-held `sess_`-prefixed id in one pass.

   Sketch of the modified loop body (existing logic unchanged when kiro IS running):

   ```python
   # Sketch only
   kiro_alive = presence.get_snapshot().any_live_process(presence._KIRO_PROVIDERS)
   for sid in session_ids:
       if sid in held:
           out[sid] = "held"
           continue
       if not sid.startswith("sess_"):
           out[sid] = "available"
           continue
       if not kiro_alive:
           out[sid] = "available"   # no kiro process → can't be held
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
   ```

   The `kiro_alive` call is made once per `_acp_availability` invocation, not per id.
   psutil unavailable → `get_snapshot()` returns `_EMPTY` → `any_live_process` returns
   `False` → fails open to `"available"` (consistent with the existing fail-open contract;
   a wrongly-available row shows the agent's own refusal on click rather than being
   permanently locked).

3. **`tests/test_web.py` — update `TestAcpAvailabilityV3` (5 existing tests + 3 new):**

   **Existing tests to update** (confirmed by code read 2026-10-14 — 5 tests, none mock
   `presence.get_snapshot`; two currently-locked-path tests will fail without this update):
   - `test_a_held_v3_session_is_reported_locked` — add `monkeypatch.setattr(presence_mod,
     "get_snapshot", lambda *a, **k: presence_mod.Snapshot({("kiro-cli-v3","/fake")}, set(), ...)`
     (a snapshot that includes kiro in `_live_cwds` so `any_live_process` returns True).
   - `test_workspace_hash_is_threaded_to_lock_holder_v3` — same presence mock required.
   - Tests 2, 3, 5 (available/held paths) still pass without the mock but SHOULD also receive
     it for consistency; the patch is cheap.

   **New tests to add** (3 total):
   - `test_acp_availability_no_kiro_process_unlocks_in_progress`: snapshot with empty
     `_live_cwds` → in_progress session → "available" (process check fires, `_lock_holder_v3`
     NOT called — verify with a mock that raises if called).
   - `test_acp_availability_kiro_running_falls_through_to_lock_holder`: snapshot with kiro
     in `_live_cwds` → in_progress session → "locked" (process check skips, `_lock_holder_v3`
     called and returns `_V3_HOLDER_PID_UNKNOWN`).
   - `test_acp_availability_kiro_running_acp_none_still_available`: snapshot with kiro in
     `_live_cwds`, `web_mod.acp = None` → "available" (fail-open contract through new branch).

   All tests use `monkeypatch.setattr(presence_mod, "get_snapshot", ...)` to inject a
   controlled `Snapshot`. Build the snapshot with `presence_mod.Snapshot(live_sids, live_cwds)`
   using the positional constructor — note `_live_cwds` is the second positional argument
   (confirmed from `Snapshot.__init__` at `presence.py:389`).

   **Class name correction**: the `_lock_holder_v3` tests live in `tests/test_data_kiro_v3.py`
   (not `test_web.py` as stated in Current State). Do not look for `TestLockHolderV3` in
   `test_web.py`; it does not exist.

**Exit criteria**:
- [x] `Snapshot.any_live_process(providers)` method present in `presence.py`; returns `True`
  when `_live_cwds` contains a kiro provider entry, `False` otherwise (including psutil
  unavailable).
- [x] `_acp_availability` calls `presence.get_snapshot()` once per invocation; when
  `any_live_process` returns `False`, all non-held `sess_`-prefixed ids return `"available"`
  without calling `_lock_holder_v3`.
- [x] When `any_live_process` returns `True`, existing `_lock_holder_v3` path is unchanged.
- [x] 5 existing `TestAcpAvailabilityV3` tests still pass (with presence mock added to
  the 2 currently-locked-path tests: `test_a_held_v3_session_is_reported_locked` and
  `test_workspace_hash_is_threaded_to_lock_holder_v3`).
- [x] 3 new tests in `TestAcpAvailabilityV3` pass (no-kiro, kiro-running, kiro+acp-None).
- [x] Full test suite passes: `pytest tests/test_web.py`.
- [ ] `memory/MEMORY.md` decision entry "v3 session liveness uses session.json status field"
  updated with an Update record noting Phase 1 resolves the crash-disambiguation residual.

---

### Phase 2: Provider filter migration to `#wsFilterMenu` [QA] [P:1]

**Goal**: Remove the `.search-area` / `#providerFilter` strip from the HTML; add a "Provider"
section at the top of `#wsFilterMenu`; rewrite `initProviderFilter` without `innerHTML`;
wire `wsFilterDot` and "Clear filters" to the provider filter state.

**Covers**: SC-3, SC-4, SC-5, SC-6 (page tests)

**File scope**: `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`,
`tests/acp_page.test.mjs`

**Details:**

1. **`index.html` — remove `.search-area` block (lines 88-93):**

   Delete the entire:
   ```html
   <div class="search-area">
     ...
     <div class="provider-filter" id="providerFilter" role="tablist"></div>
   </div>
   ```

2. **`index.html` — rewrite `initProviderFilter` (line 6840):**

   The function no longer builds chip HTML. Its new sole responsibility:
   - Fetch `/api/available-providers`
   - Store `window._availableProviders`
   - After storing, call `renderWorkspaceFilterMenu()` so the menu is pre-populated
     (in case the user opens the filter popover before the first click triggers a rebuild)

   No DOM manipulation here; no `innerHTML`. Sketch:
   ```js
   // Sketch only
   function initProviderFilter(){
     fetch('/api/available-providers').then(function(r){return r.json()})
       .then(function(providers){
         window._availableProviders = providers || [];
         renderWorkspaceFilterMenu();
       }).catch(function(){});
   }
   ```

3. **`index.html` — remove `switchProvider` function (line 6841):**

   This function toggled `.active` / `aria-selected` on `.provider-filter-btn` elements,
   which no longer exist. Its work (setting `_activeProvider` + calling `dashRailReload`)
   is inlined directly into the Provider section's click handlers in
   `renderWorkspaceFilterMenu`. Delete `switchProvider` entirely.

4. **`index.html` — add Provider section to `renderWorkspaceFilterMenu` (line 9227):**

   Insert a "Provider" section **before** the existing Tag section. All DOM construction
   uses `createElement`/`textContent`/`appendChild` — no `innerHTML`.

   Sketch (inside `renderWorkspaceFilterMenu`, at the top after `menu.textContent = ''`):
   ```js
   // Sketch only
   var providers = window._availableProviders || [];
   if (providers.length >= 2) {
     var provHead = document.createElement('div');
     provHead.className = 'acp-rail-menu-head';
     provHead.textContent = 'Provider';
     menu.appendChild(provHead);

     // "All" option
     menu.appendChild(_wsFilterOptionRow('All providers', null,
       !window._activeProvider || window._activeProvider === 'all',
       function(){
         window._activeProvider = 'all';
         _updateProviderFilterDot();
         dashRailReload();
       }));

     providers.forEach(function(p){
       menu.appendChild(_wsFilterOptionRow(p.display, p.color || null,
         window._activeProvider === p.name,
         function(){
           window._activeProvider = p.name;
           _updateProviderFilterDot();
           dashRailReload();
         }));
     });

     var provDivider = document.createElement('div');
     provDivider.className = 'acp-rail-menu-divider';
     menu.appendChild(provDivider);
   }
   ```

   The `_wsFilterOptionRow` function at line 9201 already uses `createElement`/`textContent`
   — no changes needed there.

5. **`index.html` — wire `wsFilterDot` and "Clear filters" — extend `_updateFilterUI` directly:**

   Do NOT add a separate `_updateProviderFilterDot()` helper. Instead, extend the existing
   `_updateFilterUI()` function to include `_activeProvider` in its `active` computation.
   This ensures the filter button's `has-filter` CSS class (which drives the accent border on
   `#wsFilterBtn` per `style.css`) also activates for provider-only filter state, and all 4
   existing `_updateFilterUI` call sites inherit the fix automatically.

   **4 `_updateFilterUI` call sites that must be updated or already benefit from the change:**
   - `initWorkspaceFilters` (called at DOMContentLoaded, after `_tagColorData` is set)
   - `setTagFilter(value)` (called when a tag option is clicked)
   - `setTimeFilter(value)` (called when a time option is clicked)
   - `clearWorkspaceFilters()` (called by the Clear button and keyboard shortcut)

   Sketch of the `_updateFilterUI` extension:
   ```js
   // Sketch only — find the existing function and add the provider check
   function _updateFilterUI() {
     var dot = document.getElementById('wsFilterDot');
     var btn = document.getElementById('wsFilterBtn');
     var providerActive = window._activeProvider && window._activeProvider !== 'all';
     var active = !!(window._activeTag || window._activeTimeFilter || providerActive);
     if (dot) dot.hidden = !active;
     if (btn) btn.classList.toggle('has-filter', active);
   }
   ```

   Also update `clearWorkspaceFilters` to reset `_activeProvider = 'all'` AND call
   `_updateFilterUI()` (which the function already calls; the only new line is the reset):
   ```js
   // In clearWorkspaceFilters — add BEFORE the existing _updateFilterUI() call:
   window._activeProvider = 'all';
   ```

   In the Provider section click handler (in `renderWorkspaceFilterMenu`), call
   `_updateFilterUI()` directly (not a new function):
   ```js
   // Provider option onClick:
   window._activeProvider = p.name;   // or 'all' for the All option
   _updateFilterUI();
   dashRailReload();
   ```

   **"Clear filters" visibility**: update the condition to include provider:
   ```js
   if (window._activeTag || window._activeTimeFilter ||
       (window._activeProvider && window._activeProvider !== 'all')) {
     // ... append Clear button
   }
   ```

6. **`style.css` — remove dead rules:**

   Delete:
   - `.search-area` block (line 319) and its `[hidden]` companion.
   - `.provider-filter`, `.provider-filter-btn`, `.provider-filter-btn:hover`,
     `.provider-filter-btn.active`, `.provider-filter-icon` rules (lines 944-955 approx).

7. **`tests/acp_page.test.mjs` — add Provider section tests:**

   Following the existing `createElement`/`textContent` test pattern (no innerHTML):
   - `renderWorkspaceFilterMenu with ≥2 providers renders a Provider section at the top`
   - `renderWorkspaceFilterMenu with <2 providers omits the Provider section`
   - `clicking a provider option in the menu sets _activeProvider and calls dashRailReload`
   - `Clear filters resets _activeProvider to "all"`
   - `wsFilterDot shows when only provider filter is active (tag and time unset)`

   **vm sandbox globals required per test** (AGENTS.md: a missing global in the vm box
   causes a silent ReferenceError swallowed by `.catch`; use `grep` to find every global
   `renderWorkspaceFilterMenu` reads before writing tests):
   At minimum: `window._availableProviders`, `window._activeProvider`, `window._activeTag`,
   `window._activeTimeFilter`, `window._tagColorData`, `document.getElementById`
   (already wired via the DOM stand-in), `closeWsFilterMenu` (a function ref — must be
   present or menu-close calls throw), `dashRailReload` (mock it to capture calls).
   `#wsFilterMenu`, `#wsFilterBtn`, and `#wsFilterDot` elements must be present in the
   test's DOM.

   Tests must set up `window._availableProviders` with 2+ providers before calling
   `renderWorkspaceFilterMenu`. Use the existing vm sandbox pattern from the file.

   **Note on provider icons:** the old `.provider-filter-btn` chips included an `<img>`
   icon sourced from `/static/icons/<provider>.png`. The new menu uses `_wsFilterOptionRow`
   with a `dotColor` dot (colored `<span>` or `<div>`), not an `<img>`. The dot color
   comes from `p.color` (from `/api/available-providers` response). If `p.color` is an
   empty string `""`, `p.color || null` evaluates to `null` (empty string is falsy) —
   this means colorless providers show no dot, which is acceptable (consistent with how
   `_wsFilterOptionRow` handles `null` dotColor for "All tags" and time options). The
   visual change from icon to color dot is in-scope and intentional; note it in the
   implementation notes.

**Exit criteria**:
- [ ] `.search-area` div and `#providerFilter` element absent from `index.html`.
- [ ] `initProviderFilter` contains no `innerHTML` assignment; only fetches providers
  and stores `window._availableProviders`.
- [ ] `switchProvider` function removed from `index.html`.
- [ ] `renderWorkspaceFilterMenu()` renders a "Provider" heading + options at the top
  of `#wsFilterMenu` when `_availableProviders.length >= 2`.
- [ ] Selecting a provider option sets `_activeProvider` and triggers `dashRailReload`.
- [ ] `wsFilterDot` shows when `_activeProvider !== 'all'`.
- [ ] "Clear filters" button resets `_activeProvider` to `"all"`.
- [ ] `.search-area`, `.provider-filter`, `.provider-filter-btn` CSS rules absent from
  `style.css`.
- [ ] `node tests/acp_page.test.mjs` passes all existing tests plus new Provider tests.
- [ ] `pytest tests/test_web.py` passes (no regressions from Phase 2 changes).

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `_live_cwds` empty when psutil unavailable | All non-held kiro sessions return "available" — a **deliberate behavioral change** from current (which returns "locked" for fresh in_progress). | Intentional: psutil-unavailable environments have degraded detection; fail-open is safer than permanently locking valid sessions. Consistent with web.py:3288 fail-open contract. |
| `renderWorkspaceFilterMenu` called before `_availableProviders` is populated | Provider section empty on first open | `initProviderFilter` calls `renderWorkspaceFilterMenu()` after setting `_availableProviders`; if the fetch is still in-flight when the user opens the menu, the section is absent (same as current 0-providers behavior). Acceptable. |
| `clearWorkspaceFilters` callers other than the menu button | Provider not reset on programmatic clear | Search all `clearWorkspaceFilters` call sites; ensure `_activeProvider = 'all'` is set in the function body, not per-call-site. |
| `_updateFilterUI` call sites not covered by `_updateProviderFilterDot` | Dot state stale after rail reload | Find all `_updateFilterUI` calls; add `_updateProviderFilterDot()` alongside each. Phase 2 exit criterion enforces this. |
| `_wsFilterOptionRow` click handler closes the menu before setting state | Race between menu close and rail reload | `_wsFilterOptionRow` already calls `closeWsFilterMenu()` then `onClick()` (index.html:9224) — the order is close-then-execute, which is correct; no change needed. |

---

## 7) Verification

**Change 1 (automated):**
```powershell
# From workspace root
.venv-PowerAtlas\Scripts\python -m pytest tests/test_web.py -k "TestAcpAvailability" -v
# Expected: 8 tests pass (5 existing + 3 new)
```

**Change 2 (automated):**
```powershell
node tests/acp_page.test.mjs
# Expected: all existing tests + new Provider section tests pass
```

**Change 2 (manual — browser):**
1. Start PowerAtlas; open `http://127.0.0.1:<port>/` (sign in via tray).
2. Confirm the top strip between the topbar and the workspaces panel is gone.
3. Click the filter-funnel button (`#wsFilterBtn`) in the workspace panel header.
4. Confirm "Provider" heading appears at the top of the dropdown, with "All providers"
   + one row per installed provider.
5. Select a provider (e.g. "kiro-cli v3"); confirm the dot appears on the button and
   the rail reloads filtered.
6. Click filter button again → "Clear filters" visible → click it → provider resets to
   "All providers", dot disappears.

**Change 1 (manual — simulated crash):**
1. Create an ACP session; note its session.json path.
2. While session is idle, manually edit session.json `"status"` to `"in_progress"`
   (simulates crash) and close all kiro-cli processes (`taskkill /IM kiro-cli.exe /F`).
3. Reload the PA dashboard; within 5 s the row should show as available (not greyed).

*(See AGENTS.md Verification Setup for `pa_local` cookie setup.)*

---

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `docs/KNOWLEDGE.md` | No update needed — the stale-lock mechanism is already documented there; this plan adds a process-presence layer, not a new mechanism. | — |
| `README.md` | No user-visible behavioral change to document (the filter button already existed; this only adds a section to its popover). | — |
| `memory/MEMORY.md` | Append an Update record to the decision entry "v3 session liveness uses session.json status field — lock-file approach does not apply": Phase 1 resolves the previously-unaddressed crash-disambiguation residual by adding `Snapshot.any_live_process(providers)` and wiring it into `_acp_availability`. | 1 (doc-table-only) |

---

## Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Process-presence short-circuit | Complete | code: b2d8b52 |
| 2 | Provider filter migration | Not started | |

---

## 9) Implementation Divergences from Plan

### Phase 1 (2026-10-14, code: b2d8b52)

- **H2 fix (additional tests)**: Review found 3 more tests outside `TestAcpAvailabilityV3`
  that call `_acp_availability` with expected `"locked"` results without mocking
  `presence.get_snapshot` (`test_locked_session_reports_locked_with_no_status`,
  `test_availability_is_computed_only_for_returned_rows`,
  `test_the_supervisor_snapshot_happens_on_the_loop`). All 3 patched in the same commit.
- **H1 fix (raising-mock)**: `test_acp_availability_no_kiro_process_unlocks_in_progress`
  initially used a raising mock that was swallowed by `except Exception` in
  `_acp_availability`; replaced with a counter mock (`lock_calls = []` + `assert
  lock_calls == []`).
- **Cross-scope divergence (Phase 2 impact)**: `TestTheDashboardHasNoAgentPageLink` in
  `tests/test_web.py` (Phase 1 scope) used `.search-area` as a topbar end-marker. Phase 2's
  deletion of that div broke the test; the orchestrator updated the marker to
  `<main class="panels-container"` in the same commit.

### Phase 2 (2026-10-14, code: pending)

## Follow-up Work (Deferred)

1. **Reduce `_V3_SESSION_STALE_SECONDS`.**  When kiro IS running but a specific session
   crashed (e.g., PA crash while a terminal kiro is open), the 30-min mtime window still
   applies. A follow-up could reduce it to ~60 s (safe given the measured 25-40 s maximum
   observed write gap). Deferred at user's explicit direction (Q3 in /qexplore interview).
   Source: Design Decisions row 3.

## Review Log

### 2026-10-14 — Plan Creation (via /qplan)

Full-effort review (4 personas: Architect, Senior engineer, Domain expert JS, Reliability engineer). 1 cycle. 9 auto-resolved findings.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Existing 2 locked-path tests in `TestAcpAvailabilityV3` break after Phase 1 adds the presence short-circuit (no `presence.get_snapshot` mock). | Fixed — Phase 1 details now explicitly list all 5 existing tests, name the 2 that need the presence mock, and add 3 new tests (not 2). |
| 2 | Medium | `_updateProviderFilterDot` parallel helper leaves `wsFilterBtn.has-filter` stale for provider-only active state; `_updateFilterUI` call sites not all covered. | Fixed — Phase 2 now extends `_updateFilterUI` directly with provider check; all 4 call sites enumerated; no separate helper needed. |
| 3 | Medium | 4 `_updateFilterUI` call sites not enumerated in Phase 2 details. | Fixed — all 4 call sites listed: `initWorkspaceFilters`, `setTagFilter`, `setTimeFilter`, `clearWorkspaceFilters`. |
| 4 | Medium | R1 risk table described psutil-unavailable behavior as "consistent with current behavior" — incorrect; it is a deliberate behavioral change. | Fixed — Risk table row reworded to state it is an intentional change and why it is acceptable. |
| 5 | Medium | vm sandbox globals for new `acp_page.test.mjs` tests not enumerated (AGENTS.md memory p

### 2026-10-14 — Phase 1 Implementation Review (Full effort, 4 personas)

4 personas: Architect, Senior engineer+Reliability (combined). 1 review cycle, 1 auto-fix cycle.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Raising mock in `test_no_kiro_process` is swallowed by `except Exception`; mutation passes silently. | Fixed — replaced with counter mock (`lock_calls = []`; assert empty after call). |
| 2 | High | 3 additional tests assert `"locked"` without presence mock: `test_locked_session_reports_locked_with_no_status`, `test_availability_is_computed_only_for_returned_rows`, `test_the_supervisor_snapshot_happens_on_the_loop`. | Fixed — presence mock added to all 3 tests in b2d8b52. |
| 3 | High | qvalidate FAIL: Phase 1 exit criteria not ticked (0/6). | Fixed — orchestrator ticked 6/7 exit criteria; criterion 7 (`memory/MEMORY.md`) is doc-table-only pending. |
| 4 | Medium | §7 Verification expected count said "4 tests + 2 new" — contradicts Phase 1 saying "5 + 3". | Fixed — §7 updated to "8 tests (5 existing + 3 new)". |
| 5 | Low | `acp_store_dir_v3` fixture in 3 new tests unnecessary (mocks bypass real I/O). | Fixed — removed from all 3 new test signatures. |
| 6 | Low | `any_live_process` docstring incomplete: only mentioned psutil-unavailable case. | Fixed — added "Also returns False when kiro-cli not in any process's cwd." |attern). | Fixed — Phase 2 now lists required globals and elements for each test's vm sandbox. |
| 6 | Low | Inaccurate existing test count (4, not 5) and wrong class name reference (`TestLockHolderV3` in `test_web.py`). | Fixed — Current State corrected: 5 tests at line 21660; `_lock_holder_v3` tests are in `test_data_kiro_v3.py`. |
| 7 | Low | `memory/MEMORY.md` update in Documentation Updates table has no corresponding exit criterion in Phase 1. | Fixed — Phase 1 exit criteria now include a checkbox for the memory entry update. |
| 8 | Low | Provider icon visual regression (old chips had `<img>` icon; new menu uses color dot) not called out in Phase 2. | Fixed — Phase 2 test section notes the intentional visual change and acceptable `null` color behavior. |
| 9 | Low | Thread-safety of `presence.get_snapshot()` in worker thread undocumented in plan; per-process cwd failure not covered. | Accepted — CPython GIL makes reference assignment atomic; stale snapshot (worst case double scan) is harmless. Add a doc comment in implementation; no plan change needed. |

## Harness Improvement Opportunities

- `run_workflow` with `workflowPrompt` dispatched the workflow-creator, which never completed
  before the session was interrupted — the sub-agent trio was never dispatched. Had to re-start
  the exploration with direct `subagent_wf-coder` calls on resume. — cost: one full interruption
  + recovery round-trip — suggested change: add a "workflow-creator in flight" signal to the
  /qexplore flow so a resume can detect the partial state without scanning session files.
