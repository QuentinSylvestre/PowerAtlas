# Dashboard ACP New Session Picker

> **Date**: 2026-09-19
> **Status**: Exploring
> **Scope**: Port the /acp new-session picker modal to the main dashboard so users can create kiro-cli v3 ACP sessions inline without leaving the dashboard.

---

## Intent

### Problem statement & desired outcomes

The main dashboard lets users resume kiro-cli v3 sessions in the transcript panel and chat live, but has no way to *create* a new ACP session from the dashboard. The only "new session" affordance on the dashboard is the sparkle dropdown, which launches terminal sessions (`POST /api/new-session`). The `/acp` page has a polished workspace-picker modal that creates ACP sessions over the WebSocket and opens them in the transcript pane — this feature does not exist on the dashboard at all.

The desired outcome is feature parity: a user on the dashboard can trigger the same picker flow (workspace selection, task mode, optional close-of-current-session), create an ACP session, and have it immediately appear in the transcript panel ready to use — without navigating to `/acp`.

### Success criteria

1. A "kiro-cli v3 ACP session" item appears in the sparkle dropdown on every workspace group header, alongside the existing provider items.
2. Clicking that item (or the equivalent trigger for a from-scratch flow) opens a picker modal matching `/acp`'s look and feel: workspace search/filter, session count per workspace, "The agent's own folder" neutral option, task-mode picker, "close current session" checkbox (shown when a session is attached in the transcript panel), and at-capacity state with guidance.
3. On workspace selection, the dashboard sends `{type:'new', payload:{cwd, mode}}` over `_dashWs`, receives the `{type:'session', payload:{created:true}}` response, auto-opens the new session in the transcript panel, and performs a targeted single-workspace rail refresh to show the new session.
4. All existing dashboard features (terminal launches via sparkle, resume, transcript, live attach, close) are unaffected.
5. The task-mode picker resets to `kiro_default` on each picker open, matching `/acp`.
6. The "close current session" checkbox is shown when `_dashAttachedSid` is non-null; disabled when `_dashTurnActive` is true with the same label text as `/acp`.
7. `acp_page.test.mjs` is extended to cover the new picker JS behavior (open/close, at-capacity state, creation flow).

### Scope boundaries & non-goals

**In scope:**
- New "kiro-cli v3 ACP session" sparkle dropdown item in the dashboard workspace group header.
- Picker modal HTML added to `index.html` (mirroring `/acp`'s `#acpPicker`).
- Task-mode picker CSS (`.acp-taskmode-*`) migrated from `acp.html` inline `<style>` to `style.css` so both pages share it.
- JS for the dashboard picker: `dashPickerOpen`, `dashPickerLoad`, `dashPickerCreate`, `dashPickerRender`, close-current flow, at-capacity handling.
- New `dashHandle` branch for `{type:'session', payload:{created:true}}` to capture the new session sid and open the transcript panel.
- Targeted single-workspace rail refresh after creation (`/api/dashboard/sessions?cwd=<new_cwd>`).
- `acp_page.test.mjs` extensions for picker behavior.

**Out of scope:**
- Any changes to `/acp` itself.
- Backend changes (no new Python routes needed).
- Image paste / Steer / Queue mode in the new-session creation flow (those are post-creation features already working in the transcript pane).
- Global topbar "New session" button (not requested; per-workspace sparkle item is sufficient).

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

- **`.acp-picker` base CSS is already in `style.css`** (line 1995+): `.acp-picker`, `.acp-picker[hidden]`, `.acp-picker-panel`, etc. are shared. No migration needed for these.
- **`.acp-taskmode-*` CSS is only in `acp.html`'s inline `<style>` block** (~288 lines). This must be migrated to `style.css` to be usable from `index.html`. AGENTS.md confirms this as the correct path.
- **`.acp-picker-note`, `.acp-picker-head`, `.acp-picker-list`, `.acp-picker-option`, `.acp-picker-search` CSS is already in `style.css`** (confirmed via grep) — only the taskmode rules need migration.
- **`index.html` already has `ACP_TOKEN`** injected via Jinja and `_dashWs` using `/ws/acp` — the WebSocket infrastructure is fully in place.
- **`dashConnect()` + `send()` are the correct extension points** for opening a socket and sending frames. The picker's `send('new', {cwd, mode}, null)` will go through these unchanged.
- **`dashHandle` does not handle `payload.created === true`**: the `type === 'session'` branch guards `if (sid !== _viewingSid) return` which would drop the creation response since `_viewingSid` hasn't been set to the new sid yet. A new branch or guard relaxation is required — detect `payload.created === true` before the `_viewingSid` guard, set `_viewingSid` to the new sid, clear the transcript, and fall through to normal session-frame handling.
- **Targeted rail refresh is supported**: `/api/dashboard/sessions?cwd=<cwd>` returns a single-group response; `dashRailMergeGroup()` absorbs it and prepends new workspaces or updates existing ones. Same pattern as `/acp`'s `railAdoptCreated`.
- **AGENTS.md constraint**: "ACP UI iteration does not require a restart." Changes to `index.html` and `style.css` take effect on hard reload (Ctrl+Shift+R). Python changes to `acp.py`/`web.py` require restart — this feature touches none.
- **Sparkle dropdown pattern** (`index.html` ~line 2000): uses `_closeAllRailActionMenus()` on item click, `acp-rail-ghost-btn` + `acp-rail-menu acp-rail-action-menu` pattern. New item follows the same structure.
- **`_availableProviders`** is populated from `/api/available-providers` and includes `kiro-cli-v3`. The new ACP item is added alongside the existing per-provider items inside the sparkle dropdown builder loop, not from `_availableProviders` itself (it's a fixed ACP-specific item).
- **`acp_page.test.mjs`** uses a Jinja renderer + DOM stand-in to drive inline scripts. It currently covers `acp.html` and the remote-access panel in `index.html`. The same harness applies to the new picker JS in `index.html`.

### 5. Risks & mitigations

- **`dashHandle` session-frame guard**: The existing `if (sid !== _viewingSid) return` will silently drop `payload.created` frames. Mitigation: add a `payload.created` check at the top of the `type === 'session'` handler before the guard, extracting the new sid, setting `_viewingSid`, clearing the transcript, then continuing normally.
- **Close-then-create race**: If the close frame races with the new-session frame (same as `/acp`'s `pendingCreate` pattern), the dashboard picker must implement the same `pendingCreate` variable + `pickerRunPending()` flow. Mitigation: port this pattern verbatim from `/acp`.
- **`_dashWs` reconnect**: The comment in `dashConnect` notes "No reconnect-on-drop for this MVP." A creation flow that opens the socket fresh and then drops it would lose the `session` frame. Mitigation: creation follows the same `dashConnect(callback)` pattern used by subscribe/load — the callback fires on open and sends the `new` frame immediately.
- **Rail dedup**: If the new workspace already exists in the rail, `dashRailMergeGroup` updates it in place rather than prepending. This is correct behavior. No risk.
- **CSS migration ordering**: Moving `.acp-taskmode-*` rules from `acp.html` inline `<style>` to `style.css` changes the cascade order only for `acp.html` itself (the rules move from a page-level `<style>` to the linked sheet). Since these rules have no interaction with other components, the move is safe. Mitigation: verify `acp_page.test.mjs` still passes after migration.

### 6. Resolved decisions

- Q1: Should "close current session" checkbox be shown when `_dashAttachedSid` is non-null? — A: Yes — Decision: Match `/acp` exactly; show when attached, disable when turn active.
- Q2: Should dashboard picker reuse `_dashWs` + `dashConnect()`? — A: Yes — Decision: Single socket per page load, same pattern as existing subscribe/load.
- Q3: Where should the "new ACP session" trigger live? — A: kiro-cli v3 ACP session item in sparkle dropdown alongside existing kiro-cli item — Decision: New fixed item in the sparkle menu builder, separate from the `_availableProviders` loop.
- Q4: After creation, should the new session auto-open in the transcript panel? — A: Yes — Decision: `payload.created === true` branch in `dashHandle` sets `_viewingSid`, clears transcript, shows composer, performs targeted rail refresh.
- Q5: Should task-mode reset on each picker open? — A: Yes — Decision: Reset to `kiro_default` on `dashPickerOpen()`, matching `/acp`.
- Q6: Should "close current" checkbox be disabled when turn is active? — A: Yes — Decision: Same disabled+label behavior as `/acp`'s `pickerRenderKeepRow()`.
- Q7: Should rail refresh be targeted (single workspace) or full reload? — A: Targeted — Decision: `dashRailFetch({cwd: newCwd, session_page:1, session_size:3})` + `dashRailMergeGroup`.
- Q8: Should the sparkle item be disabled when at capacity? — A: No — Decision: Item always clickable; picker shows at-capacity state with guidance (matching `/acp`).
- Q9: Should `acp_page.test.mjs` be extended? — A: Yes — Decision: Extend the existing test file to cover picker open/close, at-capacity state, creation flow JS.

### 7. Open items

- After CSS migration of `.acp-taskmode-*` to `style.css`, confirm `acp_page.test.mjs` still passes (execution-contingent on the migration being done).
- Exact line placement for the new `payload.created` branch in `dashHandle` — deterministic, resolvable by reading the function during `/qdev`.

### 8. Recommended approach

**Phase 1 — CSS migration**: Move `.acp-taskmode-*` rules from `acp.html`'s inline `<style>` to `style.css`. Run `acp_page.test.mjs` to verify no regression.

**Phase 2 — Picker HTML**: Add the picker modal HTML to `index.html`, mirroring `/acp`'s `#acpPicker` structure with new ids (`dashPicker`, `dashPickerList`, `dashPickerSearch`, `dashPickerNote`, `dashPickerTaskModeRow`, `dashPickerKeepRow`, `dashPickerCloseCurrent`, `dashPickerNeutral`, `dashPickerCancel`).

**Phase 3 — Picker JS**: Add the picker JS to `index.html`:
- `dashPickerOpen(cwd)` — shows modal, resets task mode, pre-filters if cwd given, fetches workspaces.
- `dashPickerLoad()` — `GET /api/acp/workspaces`, updates `dashPickerWorkspaces`, calls `dashPickerRender()`.
- `dashPickerRender()` — renders workspace rows with filter, capacity note, neutral option.
- `dashPickerCreate(cwd)` — guards capacity, optionally closes current session first (`pendingCreate` pattern), sends `{type:'new', payload:{cwd, mode}}` over `_dashWs` via `dashConnect()`.
- `dashPickerRunPending()` — fires pending create after close confirms.
- `dashPickerRenderKeepRow()` — shows/hides/enables close-current checkbox per `_dashAttachedSid` and `_dashTurnActive`.
- Task-mode picker wiring (reuse the same `.acp-taskmode-*` classes).

**Phase 4 — `dashHandle` creation branch**: Add `payload.created === true` detection before the `sid !== _viewingSid` guard in the `type === 'session'` handler: set `_viewingSid = sid`, clear transcript, call `dashPickerRailAdopt(payload.cwd)` for targeted rail refresh, then fall through to normal session handling.

**Phase 5 — Sparkle item**: In `dashRailGroupNode()`'s sparkle dropdown builder (index.html ~line 2015), add a fixed "New ACP session" item before the `_availableProviders` loop. Item calls `dashPickerOpen(group.cwd)`. Item is only rendered when `ACP_TOKEN` is non-null (same guard as the existing ACP affordances).

**Phase 6 — Tests**: Extend `acp_page.test.mjs` with picker behavior tests (open/close, at-capacity row disabled state, creation flow frame handling).

### 9. QA environment

- `index.html` and `style.css`: hard reload (`Ctrl+Shift+R`) picks up changes immediately (no restart needed).
- Manual verification: open dashboard, expand a workspace group, click sparkle → confirm "New ACP session" item appears; click it → confirm picker modal opens with workspace list; select workspace → confirm session creates, appears in rail, transcript panel opens.
- Automated: `node tests/acp_page.test.mjs` — run after each phase. Exits 0 on pass.
- No external credentials or hardware needed.

## Harness Improvement Opportunities

- Dispatching three separate sub-agents before the interview is correct per the skill, but the skill's probe-gate instruction ("run every probe on the decidable-by-probe list before forming a single question") arrives only after reading `shared.md` — which is cited but not pre-loaded. The probe gate is non-trivially expensive to miss (as it was here on the first round). Suggested change: load `shared.md` automatically alongside `SKILL.md` in the qexplore skill activation, or include the probe-gate rule inline in SKILL.md. — cost: one extra interview round and user correction.
