# Refresh Button Unification

> **Date**: 2026-10-07
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
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
- `last_refresh` time displayed in the rail status line after a successful refresh
- The dashboard topbar button and `.refresh-group` removed; the surviving dashboard button is `#dashRailReload` in the rail head

### What is NOT changing
- The 60-second auto-poll timers (`dashRailRefresh()` / `railRefresh()`) remain shallow — they are background maintenance, not user-triggered
- `dashRailReload()` itself stays shallow; all 4 non-button call sites (pollWarmup, switchProvider, dashRailInit, doRefresh chain) are unaffected
- `railLoadFirstPage()` similarly stays shallow; its 2 non-button call sites (setSortMode, boot/reconnect) are unaffected
- `POST /api/refresh` server implementation is unchanged
- No changes to `web.py`, `data.py`, or any Python file

### Success Criteria

- **SC-1** `index.html` — Dashboard deep refresh:
  - The topbar `.refresh-group` div (button + `#refreshTime` span) is removed
  - `#dashRailReload` click handler replaced with a new `dashDeepRefresh()` function that: disables + spins the button, calls `POST /api/refresh`, on success updates the rail status with "refreshed HH:MM:SS", calls `refreshSettings()` then `dashRailLoadFirstPage()`, on error shows "Refresh failed" in the rail status, re-enables button in either case
  - The old `doRefresh()` function is removed or replaced by `dashDeepRefresh()`

- **SC-2** `acp.html` — ACP deep refresh:
  - `#acpRailReload` button class changed from `.acp-btn` (text pill) to `.ws-icon-btn`; text content replaced with the matching rotating-arrow SVG
  - Click handler replaced with a new `railDeepRefresh()` function that: disables + spins the button, calls `POST /api/refresh`, on success calls `railLoadFirstPage()`, on error shows "Refresh failed" in `railStatus`, re-enables button in either case
  - No `refreshSettings()` call on ACP (no launcher tiles)

- **SC-3** `style.css` — Consistent loading state:
  - `.ws-icon-btn.spinning svg { animation: topbar-spin 0.8s linear infinite }` rule added (reuses existing `@keyframes topbar-spin`)
  - `.ws-icon-btn:disabled` rule added with `opacity: 0.5; cursor: wait` (currently missing)

- **SC-4** `tests/acp_page.test.mjs` — Test update:
  - Test at ~line 4303 (`page.click("acpRailReload")` / capacity re-check) updated: `fetch` stubbed to return `{ last_refresh: "00:00:00" }` for `POST /api/refresh` before the click, so the new deep-refresh flow completes and `railLoadFirstPage()` still runs
  - Suite stays green


## Exploration Discovery

1. **Root cause**: `background_refresh` (web.py:1422) runs every 30 s but only calls `data.refresh_stale_entries()` — it updates already-known workspaces but never re-runs workspace discovery. `_cache` (workspace list, 30 s TTL) and `session_cache` can therefore be stale for up to 30 s. `POST /api/refresh` is the only path that clears both and calls `warmup_all()`.

2. **Behavioral architecture**: `doRefresh()` (index.html:8098) — the only existing deep path — calls `POST /api/refresh`, then `refreshSettings()`, then `dashRailReload()`. Error handling is absent (silent `.then()` chain). ACP has no equivalent path at all.

3. **CSS**:
   - Topbar button: `.refresh-btn` (28 px round, spinning via `.spinning` + `@keyframes topbar-spin`, `style.css:43-57`)
   - Dashboard rail button: `.ws-icon-btn` (32 px square, no loading state, `style.css:423-442`)
   - ACP rail button: `.acp-btn` (text pill, 20 `.acp-btn` elements total in `acp.html`, changing this one has no blast)
   - `topbar-spin` keyframe already exists; a single `.ws-icon-btn.spinning svg` rule is all that's needed
   - `.ws-icon-btn:disabled` CSS is absent — needs adding

4. **Step 1.5 dispatched code-tracing trio** — in-scope files were predominantly JS/HTML/CSS (index.html, acp.html, style.css). The trio covered mutation traces, CSS blast radius, and full subsystem/polling architecture.

5. **Risks & mitigations**:
   - `doRefresh()` uses `document.querySelector('.refresh-btn')` as a JS selector (index.html:8098) — removing the topbar button without removing this querySelector would throw. Mitigation: `doRefresh()` is removed entirely and replaced by `dashDeepRefresh()` wired to `#dashRailReload`.
   - `dashRailReload()` has 5 call sites; only the button click changes to deep. The other 4 must stay shallow or every tab-focus and boot cycle does a full disk scan.
   - `acp_page.test.mjs:4303` clicks `#acpRailReload` and checks capacity re-check; after the change the click triggers `POST /api/refresh` which will fail in the test DOM environment unless `fetch` is stubbed (SC-4).
   - `acp_page.test.mjs:3765` asserts `#acpRailReload.disabled === true` on module failure — unaffected by class/SVG changes (references by ID).

6. **Resolved decisions**:
   - Q1/Q3: All manual Refresh buttons do the full deep path — A: user confirmed — Decision: one behavior, both pages
   - Q4: Dashboard button lives in the rail head; topbar `.refresh-group` removed — A: user confirmed — Decision: `#dashRailReload` upgraded in-place; topbar div removed
   - Q-staleness: Is cache-clear necessary? — A: user confirmed staleness is the real symptom — Decision: yes, deep path is required; do not collapse to shallow

7. **Open items**: None. All design questions resolved in the interview.

8. **Assumptions (confirmed at checkpoint)**:
   - `dashRailReload()` stays shallow; only `#dashRailReload`'s click handler upgrades to deep
   - Dashboard deep-refresh continues to call `refreshSettings()` (launcher tile refresh); ACP does not
   - `last_refresh` shown in rail status line ("refreshed HH:MM:SS") from `POST /api/refresh` response
   - Error feedback in rail status text on failure (both pages)
   - `.ws-icon-btn` class and same rotating-arrow SVG icon used on both pages
