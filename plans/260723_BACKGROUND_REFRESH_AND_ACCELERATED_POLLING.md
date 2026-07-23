# Background Refresh and Accelerated Polling

> **Date**: 2026-07-23
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Eliminate "Loading..." flicker on expanded workspace cards during refresh; accelerate session/status updates for active workspaces

---

## Intent

### Problem statement & desired outcomes

Expanded workspace cards flash "Loading..." text for 5-10 seconds on every periodic refresh (15s burst / 30s steady). This happens because `refreshCards()` replaces the entire `#workspace-cards` container via `innerHTML`, and the server always renders cards with `sessions=[]` (sessions are lazy-loaded separately). The user sees populated sessions vanish, "Loading..." appear, then sessions repopulate — a jarring experience for cards they're actively watching.

Additionally, the uniform 30s polling rate means status changes (agent finished, error occurred) take up to 30s to reflect in the UI, regardless of whether the workspace has an actively-running process. Key workspaces with running agents should feel snappier.

**Desired outcomes:**
- Expanded workspace cards never show "Loading..." during periodic background refresh — existing session content remains visible while fresh data loads behind the scenes
- Status dots (working/waiting/errored) update within 5 seconds for workspaces with active processes
- Session content for active workspaces refreshes every 10s (vs 30s today)
- Workspace panel ordering remains correct even for expanded cards (DOM relocation when position changes)

### Success criteria

1. An expanded workspace card with visible session rows never flashes "Loading..." during a periodic refresh cycle — content stays visible while the background fetch completes, then updates in-place.
2. Status dots on workspace cards and session rows for active workspaces (with a running kiro-cli/claude process) update within 5 seconds of a state transition (working→waiting, working→errored).
3. Session rows within expanded cards of active workspaces refresh content (new sessions, title changes) every 10 seconds without destroying existing DOM content.
4. When an expanded card's time-group position changes (e.g., "Yesterday" → "Today" due to new activity), the card is relocated to the correct position in the workspace panel without collapsing or losing session content.
5. The right-panel (all-sessions) status dots update at the same 5s cadence for sessions in active workspaces.
6. Scroll position and selection state in both panels are preserved during the fast status poll and the 10s session content refresh.
7. The 30s full workspace discovery cycle continues to handle structural changes (new workspaces appearing, workspaces being removed, ordering of collapsed cards).

### Scope boundaries & non-goals

**In scope:**
- New lightweight `/api/session-status` endpoint returning per-session and per-workspace status as JSON
- Client-side 5s status-only poll that updates dots in-place (CSS class changes, no HTML re-render)
- Client-side 10s session content refresh for expanded cards in active workspaces (cache-aware, no `fresh=1`)
- Refactored `refreshCards()` to preserve expanded card DOM nodes during full workspace panel refresh
- DOM relocation logic to move expanded cards to correct time-group position when ordering changes
- Maintaining `htmx.process()` contract for any new DOM insertions

**Non-goals:**
- SSE/WebSocket infrastructure (staying with polling)
- Changing the 30s workspace discovery cadence
- Changing the server-side `refresh_stale_entries()` 30s background task
- Accelerating refresh for workspaces without active processes
- Preserving session-level selections across the 30s full refresh (existing limitation, orthogonal)

---

## Discovery

### Existing patterns & constraints

- `refreshCards()` (index.html:169-222) does full innerHTML replacement of `#workspace-cards` and `#all-sessions`
- `/partials/workspaces` (web.py:506-660) always renders with `sessions=[]` — sessions are lazy-loaded via `/partials/sessions`
- `workspace_card.html:34-38` shows "Loading..." when sessions is empty (always the case from workspace endpoint)
- Pin/unpin uses optimistic DOM manipulation without full refresh (precedent for in-place updates)
- `loadMoreSessions` uses `insertAdjacentHTML` (precedent for non-destructive DOM updates)
- Custom htmx-mini requires `htmx.process(el)` after every innerHTML swap (project memory)
- `SessionCache.get()` returns copies, not references (data.py:82) — safe for concurrent access
- `presence.get_snapshot()` has 3s TTL; `status_classifier` has 5s TTL — floor for meaningful status updates
- `_background_refresh()` runs `refresh_stale_entries()` every 30s, updating SessionCache when file mtime changes
- Currently `refreshCards(false)` still sends `fresh=1` to `/partials/sessions` for expanded cards (index.html:213), bypassing cache and hitting disk

### Risks & mitigations

1. **DOM relocation handler preservation** — Moving a DOM subtree via `removeChild`/`insertBefore` preserves event listeners per spec. Low risk but should be verified with Playwright.
2. **Race between 5s status poll and 30s full refresh** — If both fire simultaneously, full refresh may overwrite dots. Mitigation: next 5s poll self-corrects within 5s. Acceptable.
3. **Expanded card identity matching** — Cards identified by `CSS.escape(cwd)` selector. Windows paths with backslashes require CSS escaping. Existing pattern works (already in use).
4. **30s latency for new session discovery in 10s content poll** — Cache-aware fetch won't see new sessions until `refresh_stale_entries()` updates the cache. Mitigated by 5s status dots providing immediate visual activity feedback.

### Resolved decisions

- Q1: Approach for preventing flicker — A: Option B (in-place DOM updates for expanded cards, skip innerHTML destruction) — Decision: Expanded cards are excluded from innerHTML replacement; their content is fetched in background and swapped only when response arrives
- Q2: Fast-poll interval for status — A: Separate lightweight status poll at 5s — Decision: New `/api/session-status` endpoint polled at 5s, updates dots in-place via CSS class changes
- Q3: Scope of accelerated session refresh — A: Workspaces with active processes (presence-based), not UI-state-based — Decision: `live_cwds()` from presence snapshot determines which workspaces get fast polling
- Q4: Right panel update strategy — A: Hybrid (C) — lightweight status update at 5s + full innerHTML at 30s — Decision: Status dots update in-place at 5s; structural changes (new sessions, ordering) on 30s cycle
- Q5: Status poll interval — A: 5s (aligned with classifier TTL) — Decision: 5s status poll
- Q6: Expanded card ordering — A: DOM relocation (B) — Decision: After innerHTML for collapsed cards, expanded card DOM nodes are relocated to correct position in the time-group ordering
- Q7: Status endpoint contract — A: Server returns both per-session and per-workspace aggregated status — Decision: Endpoint returns `{sessions: {id: status}, workspaces: {cwd: status}}`
- Q8: Accelerated session content interval — A: 10s for active workspaces — Decision: 10s cache-aware session content refresh for expanded cards in active workspaces
- Q9: Cache strategy for 10s session refresh — A: Cache-aware (no fresh=1) — Decision: Reads from SessionCache updated by background `refresh_stale_entries()`; avoids disk IO on fast path

### Open items

None — all design decisions resolved.

### Recommended approach

Three-tier polling architecture:

1. **5s lightweight status poll** — New `/api/session-status` endpoint. Client sends list of visible session IDs (or active workspace cwds). Server returns JSON with per-session status + per-workspace aggregated status. Client updates status dot CSS classes in-place on existing DOM elements (both panels). No HTML rendering server-side.

2. **10s session content refresh** (active workspaces only) — For expanded cards whose workspace has a running process, fetch `/partials/sessions?cwd=...` (cache-aware, no `fresh=1`). Replace card-body innerHTML only when response arrives (never destroy existing content before new content is ready). Also update matching rows in right panel if visible.

3. **30s full workspace refresh** (existing, refined) — `refreshCards()` refactored: before innerHTML replacement of `#workspace-cards`, detach expanded card DOM nodes. After innerHTML, relocate preserved nodes to their correct position in the new DOM structure (matching time-group). Collapsed cards get full re-render as today. Right panel gets full innerHTML replacement.

The `/api/session-status` endpoint leverages existing `presence.get_snapshot()` + `_session_status()` + `_workspace_status()` logic — no new backend computation, just a JSON serialization of what's already computed for HTML rendering.

### QA environment

- PowerAtlas startable locally: `python -m power_atlas` from the project venv
- Web UI accessible at `http://127.0.0.1:<port>` (port shown at startup or in config)
- Playwright MCP available for browser verification
- Active sessions can be simulated by running `kiro-cli chat -a` in a workspace
- Status transitions observable by starting/stopping kiro-cli processes
