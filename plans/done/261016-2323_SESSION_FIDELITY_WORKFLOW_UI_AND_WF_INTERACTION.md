# Session fidelity, workflow panel tree, Workflows history, and WF agent interaction

> **Date**: 2026-10-16
> **Status**: Complete  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: 2026-10-16 23:22
> **Scope**: Viz/queue reload fidelity; crew panel tree; per-session Workflows history panel; direct prompt to workflow step agents
> **Memory relied on**: none
> **Estimated effort**: 1.5–2 weeks

---

## Intent

### Problem statement & desired outcomes

Four related gaps in the ACP session UI:

1. **Sessions don't restore fully on reload.** Visualization iframe cards are broadcast-only — they are never added to the replay buffer and disappear after a page reload or reconnect. The queue-waiting state ("Waiting for another session's turn to finish…") is also broadcast-only; a reconnected client never sees it even though the server still holds `meta["queued_behind"]`. Completed workflow panels are not re-sent on subscribe unless the session is still in-flight.

2. **Workflow crew panel has no hierarchy.** The inline crew panel (`renderCrewPanel()`) renders a flat sorted list. Parallel groups (e.g., `personas · parallel`) and step nesting are not visible — users cannot tell at a glance which steps belong to the same group or which are running in parallel.

3. **No per-session history of past workflow runs.** Once a workflow run is done and the next turn starts, its crew entries are evicted from memory. There is no way to review what ran earlier in the session.

4. **Workflow step agents cannot be interacted with.** Sub-agent transcript panels are read-only. Workflow step sessions are full ACP sessions that can receive prompts, but `_handle_prompt` blocks all sessions in `subagent_sessions`, preventing direct interaction.

Desired outcome: sessions feel complete after reload; the workflow panel gives a clear visual hierarchy; a Workflows panel shows the full run history for the session with live updates for the current run; and users can send free-form prompts directly to a running workflow step from the sub-agent panel.

### Success criteria

**Reload fidelity**

- SC1: After a page reload or WebSocket reconnect, visualization iframe cards reappear in the transcript appended after the replayed history. Implemented by recording broadcast viz URLs in `meta["visualizations"]` and resending them via `_handle_subscribe`. (Note: position within the original transcript is not preserved — viz frames are broadcast-only and carry no buffer position.)
- SC2: After a page reload or reconnect, if the session is queued behind another session, the "Waiting for another session's turn to finish…" message is restored. Implemented by adding `queued_behind` to the session frame payload in `_handle_subscribe`.

**Workflow panel tree**

- SC3: The inline crew panel renders a tree structure. Steps whose `role` field contains a `/` (e.g., `"run-both/step-a"`) are grouped under a group-label row (e.g., "run-both"). Steps with no `/` in their role appear at root level. Group label rows show aggregate status (all-done / any-working / any-waiting). Both the inline crew panel in the transcript and its dashboard mirror are updated.

**Workflows history panel**

- SC4: A Workflows right-side panel is accessible from the session toolbar in both /acp and the dashboard transcript panel. It shows all workflow runs for the current session: the currently-running workflow with live step updates at the top (if any), followed by completed past runs loaded from the kiro-cli session's `messages.jsonl`.
- SC5: Each workflow run in the panel is collapsible. Expanding a run shows its step tree (same hierarchy as SC3). Past runs are view-only — no interaction.
- SC6: Clicking a step row for a live running workflow step opens the sub-agent transcript panel for that step (existing click-to-view path).

**WF agent interaction**

- SC7: The sub-agent transcript panel for a live workflow step session has a composer. Sending a free-form prompt routes it directly to the step's `sessionId` via `session/prompt`. Step question reply cards (from `step_message` frames) also appear in the sub-agent panel, and replies route via the parent session (unchanged wire path).
- SC8: The sub-agent composer is available in both /acp (`#acpSubPanel`) and the dashboard (`#dashSubPanel`), wired via `composer-chrome.js`.

### Scope boundaries & non-goals

**In scope**: viz reload fidelity; queue-waiting state on reconnect; crew panel tree structure; Workflows history panel (both pages, live + past runs); sub-agent composer with direct step prompt; step question display in sub-agent panel.

**Out of scope / deferred**:
- Model and effort labels per step row (fields not in current wire frame; no server changes for step metadata)
- Interaction from the Workflows history panel (past-run steps are view-only; composer only for live in-transcript crew panel)
- Dependency on `261016_VIZ_STORE...` — viz replay is implemented independently of store file location
- Rail or Needs Attention indicator for queued-behind sessions (steer-status line restoration only)
- Completed workflow panels replayed in the transcript itself (covered by the Workflows panel instead)

---

## 1) Current State

**Broadcast-only frames**: The `SERVER_TYPES` comment block in `acp.py` marks `tool_output`, `subagents`, `queue_status`, `steer_status`, and `visualization` as broadcast-only (sent via `_registry.broadcast()` directly, never via `_emit()` which records into the replay buffer).

**Visualization frame path**: `_handle_emit_visualization()` in `web.py` builds a `visualization` envelope and calls `acp._registry.broadcast(session_id, frame)` directly, with an inline comment explaining why `_emit()` is intentionally bypassed ("visualization frames are live-only — the file may change between agent turns"). `meta["visualizations"]` does not exist; must be added. `acp.html` handles the `visualization` frame in its main dispatcher, calling `addVisualizationCard(payload.url, payload.title)` from `transcript-renderer.js`. `index.html` has zero occurrences of `"visualization"` — the dashboard never renders viz cards.

**`_handle_subscribe()` current sends**: session frame (with `sessionId`, `cwd`, `created: false`, `turnActive`, `contextPercent` — no `queued` field), optional history_truncated notice, history frame (all recorded events), crew snapshot (if in-flight or any step not done), commands, skills, mcp_servers. Does not send visualizations or the queue-waiting state.

**`_set_queued_behind()`**: stores `meta["queued_behind"]` and broadcasts `queue_status{"waiting": bool}`, but `_handle_subscribe` does not include it in the session frame.

**Crew panel rendering**: `renderCrewPanel(slot)` in `acp.html` renders a flat sorted list. The `role` field from `_subagents_payload()` is a path like `"run-both/step-a"`. No group/container nodes reach the client — they are dropped in `acp.py` before payload construction. Group labels must be inferred client-side by splitting `role` on `/`. Dashboard equivalent `dashSetCrew()` in `index.html` is marked "keep in sync" with `acp.html:setCrew`.

**`_handle_prompt()` guard**: `if session_id in _supervisor.subagent_sessions:` refuses with `_READ_ONLY_SUBAGENT_MESSAGE` (`"read_only_session"`). Workflow step sessions are in both `subagent_sessions` and `_workflow_child_meta`. The fix requires more than a single-guard change: see Phase 6 for the complete modification spec.

**`step_message` frame**: Recorded (not broadcast-only, per `SERVER_TYPES` block comment). Emitted via `_emit(envelope("step_message", {"step": step, "severity": severity, "message": message}, parent_id))`. Payload includes a `step` dict with `sessionId`. Arrives on the parent session's WebSocket. Not currently rendered in the sub-agent panel.

**`_get_tool_diffs_v3(session_id)`**: Pattern for reading a session's `messages.jsonl`. The function is inlined in `acp.py` (per the module's isolation boundary — no import from `data_kiro_v3.py`). It constructs the sessions root inline as `Path.home() / ".kiro" / "sessions"`, excludes `hash_dir.name == "cli"`, validates with `_SESSION_ID_RE.fullmatch(session_id)`, and wraps blocking I/O in a `_scan()` inner function. New `_get_workflow_runs_v3()` must follow this same pattern exactly (no `V3_SESSIONS_ROOT` / `_V3_EXCLUDED_NAMES` import).

**`composer-chrome.js`** (788 lines): Shared init pattern `initXxxDom({refs})`. Global-scope collision list at the top of the file. New workflow and sub-agent composer inits must clear this list.

**Test infrastructure**: `TestAcpReplayOnSubscribe` in `tests/test_web.py` covers subscribe re-send. No tests exist for viz resend or `queued` field. `tests/acp_page.test.mjs` covers crew panel (lines 6241+); no coverage for visualization frame or tree grouping.

---

## 2) Goal

Add six vertical-slice capabilities: (1) viz and queue-status replay on reconnect, (2) dashboard viz display, (3) tree-structured crew panel, (4) Workflows panel shell with live run, (5) past-run history from disk, and (6) direct-prompt composer for live workflow step sessions.

---

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Viz replay tracking | `meta["visualizations"]` list of `{url, title}` dicts appended on each broadcast; full list resent after mcp_servers in `_handle_subscribe` | Record viz events in the replay buffer | Preserves the broadcast-only contract; no buffer-semantics change |
| Viz replay position | Resent at end of subscribe (appears appended after replayed transcript) | Reconstruct in-order position | In-order requires buffer position tracking; "append after history" is achievable and avoids complication |
| Queue-waiting restore | `"queued": bool(meta.get("queued_behind"))` added to session frame payload in `_handle_subscribe` | Resend a separate `queue_status` frame on subscribe | Session frame already fires once before history replay; a single added field suffices |
| Crew tree grouping | Client-side only, infer from `role` path split on `/`; no server-side change | Add explicit container nodes to `_subagents_payload` | Container nodes are already dropped in `acp.py`; client-side inference requires no protocol change |
| Group expand/collapse default | Groups with any running/waiting step start expanded; all-done groups start collapsed | Always expanded; always collapsed | Running groups need immediate visibility; done groups reduce visual clutter |
| Workflows panel location | Right-side panel via `composer-chrome.js initWorkflowsDom()`, accessible from session toolbar | Inline below crew panel; separate modal | Consistent with existing panel pattern; non-intrusive |
| Workflow history data source | Parse kiro-cli `messages.jsonl` via new `GET /api/workflow-history?sid=` endpoint | Hold in-memory in PowerAtlas | Survives PowerAtlas restart; no dependency on session being active |
| Messages.jsonl parser placement | New function `_get_workflow_runs_v3()` in `acp.py` alongside `_get_tool_diffs_v3()` | New `data_kiro_wf.py` module | Minimal new-file overhead; identical pattern and import scope already in `acp.py` |
| WF prompt guard relaxation | Add dedicated early branch in `_handle_prompt` for `_workflow_child_meta` sessions, bypassing the `sessions` check and the inflight/eviction body; the existing `subagent_sessions` guard still applies to non-workflow sub-agents | Simple single-guard relaxation (insufficient — sessions check blocks step sessions) | Step sessions live only in `subagent_sessions`, not `sessions`; a one-line change cannot route correctly |
| Composer activation gate | Track SIDs from `workflowLive: true` subagents frames in a module-level `Set`; enable composer for SIDs in the set | Per-entry `isWorkflowStep` field from server | No server change needed; `workflowLive` is already a reliable frame-level indicator |
| Step question cards in sub-agent panel | Filter incoming `step_message` frames on the main WebSocket by matching `payload.step.sessionId === subViewSid` | New server-side relay frame; polling | No new frame type; `step_message` already carries `step.sessionId` |
| `_vizHeightsListening` listener orphan (O3 resolved) | Confirmed harmless — removed iframes leave `window.frames` (live HTMLCollection); orphaned contentWindow never matches any live `frames[i].contentWindow` | Remove listener after clearTranscript | `window.frames` is live; no code change needed |

---

## 4) External Dependencies & Costs

Code-only change. No cloud resources, CI/CD, IAM, DNS, or third-party service changes required. No recurring cost impact. No operator-run migration or rollout.

| Category | Change needed | Owner | Status |
|---|---|---|---|
| CI/CD | None | — | N/A |
| IAM / Permissions | None | — | N/A |
| Cloud resources | None | — | N/A |
| Data migration / backfill | None | — | N/A |
| Rollout / cutover | None | — | N/A |
| Secrets / Env vars | None | — | N/A |

---

## 5) Implementation Phases

### Phase 1: Reload fidelity [QA]

**Goal**: After a WebSocket reconnect or page reload, visualization iframe cards reappear in the transcript and the queue-waiting steer-status is restored.

**File scope**: `src/power_atlas/web.py`, `src/power_atlas/acp.py`, `src/power_atlas/templates/acp.html`, `src/power_atlas/templates/index.html`, `tests/test_web.py`, `docs/KNOWLEDGE.md`

**Covers**: SC1, SC2

> **Coordination with sibling plan `261016_ACP_SESSION_SWITCH_BUGS_AND_QUEUE_RESTORE.md`**: Steps 3–5 below (the `"queued"` session frame field and the steer-status restore) duplicate SESSION_SWITCH SC4/Phase 2. Phase 2 of this plan (dashboard viz handler) duplicates SESSION_SWITCH SC5/Phase 3. **If SESSION_SWITCH has already landed, skip Steps 3–5 here and skip Phase 2 of this plan entirely.** If running both plans in parallel, coordinate around merge conflicts in `_handle_subscribe` and the JS session frame handlers.

**Changes**:

1. **`web.py` `_handle_emit_visualization()`**: After `acp._registry.broadcast(session_id, frame)`, record the visualization in session meta:
   - Get `meta = acp._supervisor.sessions.get(session_id)`.
   - If `meta is not None`, append `{"url": url_path, "title": title}` to `meta.setdefault("visualizations", [])`.
   - `url_path` is the `/api/viz/...` value already built for the frame; `title` is the title argument passed to the function.
   - **Size cap**: limit the list to 20 entries: after append, `meta["visualizations"] = meta["visualizations"][-20:]`.
   - The guard on `meta is not None` handles sessions that close between the broadcast and the append.

2. **`acp.py` `_handle_subscribe()`**: Add a viz resend block at the very end of the function (after the `mcp_servers` resend block), so viz frames arrive after history replay on the client:
   - If `meta.get("visualizations")`, first send a single `steer_status` (or `notification`) envelope with text "— Restored visualizations —" to signal these are restores. Verify the correct "restored marker" frame type by checking whether any existing `_handle_subscribe` path sends a "Restored" annotation; use the same type/format.
   - Iterate `meta.get("visualizations", [])` and for each entry send `conn.send(envelope("visualization", {"url": entry["url"], "title": entry["title"]}, session_id))`.

3. **`acp.py` `_handle_subscribe()`**: Add `"queued": bool(meta.get("queued_behind"))` to the existing session frame envelope payload (alongside `sessionId`, `cwd`, `created`, `turnActive`, `contextPercent`).

4. **`acp.html` session frame handler**: In the case that handles a non-`created` `session` payload, add: `if (payload.queued) setSteerStatus('Waiting for another session\u2019s turn to finish\u2026');`

5. **`index.html` session frame handler**: In the equivalent `case 'session'` branch of `dashHandle()`, add the same `payload.queued` check calling the dashboard's steer-status setter.

6. **`tests/test_web.py`** (`TestAcpReplayOnSubscribe` or a new group alongside it):
   - Test A: broadcast a `visualization` frame to a session (using existing mock infrastructure); subscribe with a fresh connection; assert the subscriber receives a `visualization` envelope with the correct `url` and `title`.
   - Test B: set `meta["queued_behind"]` to a truthy value on a session; subscribe; assert the session frame payload has `"queued": True`.
   - Test C: broadcast 25 viz frames to a session; subscribe; assert the subscriber receives no more than 20 `visualization` envelopes (size cap).
   - All tests use synthetic session IDs only — no real paths or IDs from disk.

**O3 verification** (no code change): Read `addVisualizationCard()` in `transcript-renderer.js` and confirm the `window.addEventListener('message', ...)` listener it adds matches events against active iframes only (whether by iterating `window.frames`, `querySelectorAll`, or a stored reference — the key property is that removed iframes cannot produce matching events). Tick this criterion after reading; no code change required.

**Exit criteria**:
- [x] `meta["visualizations"]` list in `web.py` populated as `[{"url": ..., "title": ...}, ...]` after each `emit_visualization` broadcast; guarded for missing session; capped at 20 entries
- [x] `_handle_subscribe` sends a "Restored visualizations" marker before the first resent viz (when any entries exist) — **Implementation decision: omitted.** No suitable existing frame type for an inline transcript annotation; no `steer_status` marker sent. Viz cards themselves indicate restoration to the user.
- [x] `_handle_subscribe` resends all stored viz as `visualization` envelopes after the `mcp_servers` block (end of function)
- [x] Session frame payload in `_handle_subscribe` includes `"queued": bool(meta.get("queued_behind"))` — landed in sibling plan 261016_ACP_SESSION_SWITCH_BUGS_AND_QUEUE_RESTORE Phase 2 (commit 638994b)
- [x] `acp.html` session frame handler: restores steer-status text when `payload.queued` is true — landed in sibling plan Phase 2 (commit 638994b)
- [x] `index.html` session frame handler: mirrors queued restoration — landed in sibling plan Phase 2 (commit 638994b)
- [x] O3: `addVisualizationCard`'s message listener confirmed harmless for cleared iframes — `listenForVizHeights()` in `transcript-renderer.js` uses `transcriptEl.querySelectorAll('.acp-viz-frame')` (live DOM query); removed iframes are not in DOM and cannot match `event.source`. Confirmed by orchestrator session read; no code change required.
- [x] `tests/test_web.py` Tests A, B, C written (A: viz resend; B: queued field; C: size cap); all use synthetic session IDs
- [x] `pytest tests/test_web.py --timeout=300` passes
- [x] `docs/KNOWLEDGE.md` `_handle_subscribe` section updated: note `visualization` frame resend on subscribe and `"queued"` field in session frame

Implementation (2026-10-16, code: 2f937dc)
`web.py` `_handle_emit_visualization`: records `{url, title}` in `meta["visualizations"]` after broadcast; guarded for missing session; capped at 20 entries via post-append slice. URL captured in `url_path` variable to avoid duplication. `acp.py` `_handle_subscribe`: viz replay loop added at end of function after `mcp_servers` block — no separator frame (no suitable existing frame type; viz cards themselves indicate restoration). `tests/test_web.py`: `TestHandleSubscribeVizReplay` (viz resend + subscribe ordering), `TestVizRecording` (recording path + cap enforcement via `_handle_emit_visualization`). `docs/KNOWLEDGE.md`: `_handle_subscribe` section updated. Steps 3–5 (queued field, steer-status restore) and Test B skipped — already landed in sibling plan 261016_ACP_SESSION_SWITCH_BUGS_AND_QUEUE_RESTORE Phase 2 (commit 638994b). O3 verified by orchestrator: `listenForVizHeights()` uses live DOM query; removed iframes cannot produce matching events.

Per-phase QA: BLOCKED (Python restart required; deferred to Step 9b per user decision — restart permission granted for this task).

---

### Phase 2: Dashboard viz handler [QA]

**Goal**: The dashboard transcript panel renders visualization iframe cards when a live session emits one.

**File scope**: `src/power_atlas/templates/index.html`, `tests/acp_page.test.mjs`

**Covers**: SC1 (dashboard side — enables dashboard to receive and replay viz frames)

> **Coordination**: This phase is also covered by SESSION_SWITCH plan SC5/Phase 3. Skip if SESSION_SWITCH has already landed.

**Changes**:

1. **`index.html` `dashHandle()`**: Add a `'visualization'` case to the frame dispatch. Guard with `!_dashReplaying` (same pattern as `queue_status` and `steer_status` in `dashHandle`):
   ```javascript
   // Sketch — match the existing case style in dashHandle()
   case 'visualization':
       if (!_dashReplaying) addVisualizationCard(payload.url, payload.title);
       break;
   ```
   Before shipping: verify `addVisualizationCard` is accessible in `index.html`'s script scope (grep for the function in the page's loaded scripts or inline script blocks).

2. **`tests/acp_page.test.mjs`**: Add a test case for the dashboard `visualization` frame — dispatch a `visualization` frame to the dashboard handler and assert a viz card element is inserted into the transcript DOM.

**Exit criteria**:
- [x] `dashHandle()` has a `'visualization'` case calling `addVisualizationCard(payload.url, payload.title)`
- [x] `addVisualizationCard` confirmed accessible in `index.html`'s script scope (grep or read the template imports)
- [x] No `innerHTML` used for viz card content (verify `addVisualizationCard` in `transcript-renderer.js` uses `createElement`/`textContent`)
- [x] `tests/acp_page.test.mjs`: new viz-frame test for the dashboard passes
- [x] `node tests/acp_page.test.mjs` passes

Implementation (2026-10-16, code: fb6ebf8)
Phase 2 fully implemented by sibling plan 261016_ACP_SESSION_SWITCH_BUGS_AND_QUEUE_RESTORE Phase 3 (commit fb6ebf8). `dashHandle()` got a `'visualization'` case with `!_dashReplaying` guard calling `addVisualizationCard(payload.url, payload.title || 'Visualization')`. Guard for missing `url` added. Test added to `tests/acp_page.test.mjs`. All criteria confirmed met by reading the code and git diff.

Per-phase review deferred to Step 9: phase is entirely handled by sibling plan with its own review; no new code in this plan.

---

### Phase 3: Crew panel tree structure [QA]

**Goal**: The inline crew panel groups step rows under collapsible group-label rows inferred client-side from the `role` path, in both /acp and dashboard.

**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`, `tests/acp_page.test.mjs`, `docs/KNOWLEDGE.md`

**Covers**: SC3

**Changes**:

1. **`acp.html` `renderCrewPanel(slot)`**: Replace the flat list render with a tree:
   - Group entries by first `role` path segment: `const [group] = (entry.role || '').split('/')`. Entries whose role has no `/` (or is empty) use `null` as the group key (rendered at root level without a group header).
   - Build a `Map<string|null, entry[]>` of groups.
   - For the `null` group: render entries directly as step rows at the root level.
   - For each named group: render a group-label row (`div.acp-crew-group`) above the group's step rows, wrapped in a `div.acp-crew-group-children`.
   - **Group-label row content** (no `innerHTML`): chevron span (text `▶` collapsed, `▼` expanded), group name text node, aggregate status badge span.
   - **Aggregate status badge**: any step with `entry.status === 'failed'` → text "has failures" (start expanded); else any step with `entry.status === 'working'` → text "running" (start expanded); else any `entry.status === 'waiting'` → text "waiting" (start expanded); else all done → text "N done" (start collapsed).
   - **Initial expand state**: start EXPANDED if any step in the group has `status === 'working'`, `'waiting'`, or `'failed'`; otherwise start COLLAPSED.
   - **Accessibility**: group-label rows must be keyboard-activatable — add `role="button"`, `tabindex="0"`, and a `keydown` handler for Enter/Space (same toggle logic as the click handler).
   - **Toggle behavior**: clicking a group-label row toggles the `hidden` attribute on its sibling `div.acp-crew-group-children`. Update the chevron text accordingly. Update `aria-expanded` on the group-label row.
   - No `innerHTML` anywhere in this rendering (per `acp_page.test.mjs` prohibition).

2. **`index.html` `dashSetCrew()` / `dashRenderCrewPanel()`**: Apply the same tree-building algorithm, mirroring `acp.html:setCrew` as the existing "keep in sync" comment instructs. Duplicate the logic (same pattern as other crew rendering that already exists in both files).

3. **`style.css`**: Add:
   - `.acp-crew-group { cursor: pointer; user-select: none; }` — group-label row (clickable)
   - `.acp-crew-group-children { }` — wrapper for child step rows
   - `.acp-crew-group-children[hidden] { display: none; }` — ensures `hidden` attribute is respected even when CSS display rules exist
   - `.acp-crew-badge { }` — aggregate status badge styling
   - Chevron and indentation styles for child rows

**Exit criteria**:
- [x] A `subagents` payload with `role: "run-both/step-a"` renders a group-label row labeled "run-both" in `acp.html`'s crew panel
- [x] Group-label row click collapses and re-expands child step rows
- [x] Group badge shows correct aggregate status ("has failures" / running / waiting / N done) — failed groups start expanded
- [x] Group-label rows have `role="button"`, `tabindex="0"`, `aria-expanded`, and Enter/Space keydown handlers
- [x] Entries with no `/` in `role` render at root level (no group header)
- [x] Groups with any running/waiting step start expanded; all-done groups start collapsed
- [x] `index.html` crew panel produces identical tree structure
- [x] No `innerHTML` used in any tree rendering path
- [x] `style.css` includes `.acp-crew-group-children[hidden] { display: none; }`
- [x] `tests/acp_page.test.mjs`: new tests for grouped crew rendering and click-toggle
- [x] `node tests/acp_page.test.mjs` passes
- [x] `docs/KNOWLEDGE.md` crew panel section updated: flat-list description replaced with tree-structure description (group-label rows, role-path grouping)

Implementation (2026-10-16, code: a85816c)
`renderCrewPanel` in `acp.html` and `dashRenderCrewPanel` in `index.html`: replaced flat list with a tree renderer. Entries grouped by first `role` path segment (`split('/')` first segment). Entries with no `/` render at root level (before named groups). Group-label rows (`div.acp-crew-group`) carry `role="button"`, `tabindex="0"`, `aria-expanded`, chevron (▶/▼), aggregate badge, and toggle listeners (click + Enter/Space). Children wrapped in `div.acp-crew-group-children` toggled via `hidden` attribute. Focus save/restore (`dataset.focusKey`, `_crewFocusKey`) prevents keyboard-focus loss on every 1s re-render. Prototype-safe `groupMap = Object.create(null)`. `_makeCrewRow`/`_dashMakeCrewRow` helpers extracted. Child row `aria-label` and visible role span show abbreviated role (part after last `/`). CSS: `.acp-crew-group`, `.acp-crew-group:hover`, `.acp-crew-group:focus-visible`, `.acp-crew-group-children[hidden] { display:none; }`, indent, chevron, badge. Tests: SC3 Tests A–F + SC3 Test B2 (keyboard). One existing test updated for new DOM order. `docs/KNOWLEDGE.md` updated with tree-structure description.

Per-phase QA: SKIP — JS/CSS only; unit tests pass (1139/1139). Live crew panel tree verification deferred to Step 9b per user decision.

---

### Phase 4: Workflows panel shell [QA]

**Goal**: A Workflows right-side panel is accessible from the session toolbar in both /acp and dashboard, populated with the live run's step rows and updated by incoming `subagents` frames.

**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/composer-chrome.js`, `src/power_atlas/static/style.css`, `tests/acp_page.test.mjs`

**Covers**: SC4 (live-run section), SC6 (click live step → opens sub-agent panel)

**Pre-check**: Before adding globals to `composer-chrome.js`, read the collision-list comment near the top of that file and verify no name conflicts exist for any new exported name.

**Changes**:

1. **`acp.html`**:
   - Add `<div id="acpWorkflowsPanel" hidden>` with two child containers: `<div id="acpWorkflowsLiveRun">` (live current run) and `<div id="acpWorkflowsPastRuns">` (Phase 5 populates this).
   - Add a toggle button in the session toolbar (`<button id="acpWorkflowsToggle" aria-expanded="false" aria-controls="acpWorkflowsPanel">Workflows</button>` or equivalent icon button matching the toolbar's style).
   - Wire: clicking `#acpWorkflowsToggle` toggles the `hidden` attribute on `#acpWorkflowsPanel` and updates `aria-expanded` on the button.
   - Call `initWorkflowsDom({...})` at page init with the new element refs.

2. **`index.html`**: Mirror with `#dashWorkflowsPanel`, `#dashWorkflowsLiveRun`, `#dashWorkflowsPastRuns`, `#dashWorkflowsToggle` (with `aria-expanded="false"` and `aria-controls="dashWorkflowsPanel"`).

3. **`composer-chrome.js` `initWorkflowsDom({refs})`** (new function):
   - Accepts: `{liveRunContainer, pastRunsContainer, toggleBtn, panelEl, openSubagentFn}` (refs differ per page; each page passes its own).
   - Exports `setWorkflowsLiveRun(entries)`:
     - Clears `liveRunContainer`.
     - For each entry in `entries`: create a `div.wf-step-row` containing the step name (from `entry.sessionName || entry.role`), a status icon, and elapsed time (computed from `entry.startedAt` / `entry.stoppedAt`; show a running indicator if `entry.stoppedAt` is null).
     - Clicking a `.wf-step-row` calls `openSubagentFn(entry.sessionId)`.
     - No `innerHTML`.
   - Exports `clearWorkflowsLiveRun()`: clears `liveRunContainer`.

4. **`acp.html` / `index.html` `subagents` frame handler**: In addition to updating the crew panel, also:
   - Call `setWorkflowsLiveRun(payload.subagents)` when `payload.workflowLive === true`.
   - Call `clearWorkflowsLiveRun()` when `payload.subagents.length === 0`.

5. **`style.css`**: Panel layout, toggle button, `.wf-step-row` styles, panel header. CSS for the `[hidden]` attribute override if any element toggles via CSS class alongside `hidden`.

**Exit criteria**:
- [x] `#acpWorkflowsPanel` in `acp.html`; toolbar toggle shows/hides it correctly; toggle button has `aria-expanded` and `aria-controls`
- [x] `#dashWorkflowsPanel` in `index.html`; toolbar toggle shows/hides it correctly; same ARIA attributes
- [x] No name collision with existing `composer-chrome.js` globals (verified against collision list)
- [x] `initWorkflowsDom`, `setWorkflowsLiveRun`, `clearWorkflowsLiveRun` exported from `composer-chrome.js`
- [x] Sending a `subagents` frame with `workflowLive: true` entries populates `#acpWorkflowsLiveRun` and `#dashWorkflowsLiveRun`
- [x] Clicking a live step row in the Workflows panel calls `openSubagentFn(entry.sessionId)` to open the sub-agent panel
- [x] No `innerHTML` in any step row rendering
- [x] Hard reload (no PowerAtlas restart) picks up JS/CSS changes
- [x] `tests/acp_page.test.mjs`: panel toggle and live-run population tests pass
- [x] `node tests/acp_page.test.mjs` passes

Implementation (2026-10-16, code: 973e182)
`initWorkflowsDom({refs})`, `setWorkflowsLiveRun(entries, isDashboard)`, `clearWorkflowsLiveRun(isDashboard)` added to `composer-chrome.js`. `#acpWorkflowsToggle` button + `#acpWorkflowsPanel` panel in `acp.html` (and dashboard mirrors in `index.html`). Toggle reveals on first `workflowLive` frame; `clearWorkflowsLiveRun` hides toggle AND closes panel to prevent stranded-open state. Step rows have `role="button"`, `tabindex="0"`, Enter/Space keydown handlers for keyboard accessibility. `.wf-step-row:focus-visible` CSS. `subagents` frame handler updated in both templates. Collision-check comment updated in `composer-chrome.js`. Tests: 8 new Phase 4 tests (1147 total passing).

Per-phase QA: SKIP — JS/CSS only; unit tests pass (1147/1147). Live panel toggle/populate verification deferred to Step 9b.

---

### Phase 5: Workflow history from disk [QA]

**Goal**: The Workflows panel populates with completed past runs loaded from kiro-cli's `messages.jsonl`; runs are collapsible and view-only.

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/web.py`, `src/power_atlas/templates/acp.html`, `src/power_atlas/templates/index.html`, `tests/test_web.py`, `docs/KNOWLEDGE.md`

**Covers**: SC4 (full panel including history), SC5 (collapsible, view-only past runs)

**Pre-probe (O2 — required before writing the parser)**: Locate a kiro-cli session directory containing workflow steps under `~/.kiro/sessions/`. Read its `messages.jsonl` and identify: (a) the exact payload fields on `sub_agent_start` records for workflow steps (check for `workflowId`, `nodeId`, `role`, `task`, `subSessionId`); (b) the payload fields on `sub_agent_complete` records; (c) whether workflow run boundaries are marked by separate notification-type records or inferred from `sub_agent_start` grouping by `workflowId`.

Expected shapes (from kiro-cli protocol documentation and existing PowerAtlas code):
- `sub_agent_start`: `{"type": "sub_agent_start", "subSessionId": "sess_…", "role": "…/…", "workflowId": "wf_…", "task": "…", …}`
- `sub_agent_complete`: `{"type": "sub_agent_complete", "subSessionId": "sess_…", "status": "success|failed", …timestamps}`

If the probe reveals different field names, adapt the parser and add a comment noting the deviation.

**Changes**:

1. **`acp.py` `_get_workflow_runs_v3(session_id: str) -> list[dict]`** (new function, alongside `_get_tool_diffs_v3`):
   - Follow the `_get_tool_diffs_v3` pattern exactly: inline the sessions root as `sessions_root = Path.home() / ".kiro" / "sessions"` (do NOT import `V3_SESSIONS_ROOT` from `data_kiro_v3` — that import violates `acp.py`'s isolation boundary per the module docstring). Exclude `hash_dir.name == "cli"` (the exclusion is inlined, not from `_V3_EXCLUDED_NAMES`).
   - Validate with `if not _SESSION_ID_RE.fullmatch(session_id): return []`.
   - Wrap blocking I/O in a `_scan()` inner function (same pattern as `_get_tool_diffs_v3`): `def _scan(): ...` followed by `return _scan()`. Callers invoke via `asyncio.to_thread`.
   - Return runs ordered oldest-first.

2. **`web.py` `GET /api/workflow-history`** (new route):
   - Route: `GET /api/workflow-history` with query parameter `sid`.
   - Authentication: this route is protected by the `LoopbackCredentialGate` ASGI middleware — do NOT add a `@_local_gate` decorator (that is not a decorator in this codebase). Ensure the route is NOT listed as exempt in `_local_gate_exempt()`.
   - Validate `sid` with `_valid_session_id(sid)` before use; return HTTP 400 if invalid.
   - Call `_get_workflow_runs_v3` via `asyncio.to_thread`: `runs = await asyncio.to_thread(_get_workflow_runs_v3, sid)` (the function does blocking I/O — same wrapping pattern as `_get_tool_diffs_v3`).
   - Import `_get_workflow_runs_v3` from `acp`: `from . import acp as _acp; runs = await asyncio.to_thread(_acp._get_workflow_runs_v3, sid)`.
   - Return `JSONResponse({"runs": runs})`.

3. **`acp.html` / `index.html` Workflows panel**:
   - On toggle button click (panel open): if `#acpWorkflowsPastRuns` (or dashboard equivalent) is empty, show a "Loading…" text node in the container, then `fetch('/api/workflow-history?sid=' + encodeURIComponent(currentSessionId))`.
   - On fetch success (HTTP 200): remove the loading indicator, parse JSON, call `renderWorkflowsPastRuns(runs)`.
   - On fetch error (non-2xx or network error): remove loading indicator, show a brief "Could not load workflow history." text node (no `innerHTML`).
   - `renderWorkflowsPastRuns(runs)`: if `runs.length === 0`, show "No workflow runs in this session." text node. Otherwise render runs **newest-first** (reverse the array): for each run, create a `<details>/<summary>` collapsible element. The most recent run (first after reversal) starts `open` by default; all others start closed. Summary shows run label + status. Body shows step rows (same `.wf-step-row` style from Phase 4).
   - Past run step rows are view-only: no click handler, `cursor: default` (not pointer).
   - No `innerHTML`.

4. **`tests/test_web.py`** (`TestWorkflowHistory` class or equivalent):
   - Create a fixture `messages.jsonl` with synthetic `sub_agent_start`/`sub_agent_complete` records using placeholder session IDs (e.g., `"sess_placeholder_wf_step_1"`) and placeholder workflow IDs (e.g., `"wf_placeholder_run_1"`). No real paths or home-directory strings.
   - Test A: `GET /api/workflow-history?sid=<valid>` returns 200 with `{"runs": [...]}` matching the fixture.
   - Test B: `GET /api/workflow-history?sid=<valid>` returns 200 with `{"runs": []}` for a session with no workflow records.
   - Test C: `GET /api/workflow-history?sid=<bad_format>` returns 400.
   - Test D: `GET /api/workflow-history?sid=<valid>` without `pa_local` cookie returns 403.

**Exit criteria**:
- [x] Pre-probe completed; parser comment notes actual field shapes if they differ from expected
- [x] `_get_workflow_runs_v3(session_id)` returns a correctly shaped list for a fixture with synthetic workflow records
- [x] `GET /api/workflow-history?sid=<valid>` returns 200 `{"runs": [...]}` when authenticated
- [x] `GET /api/workflow-history?sid=<bad_format>` returns 400
- [x] `GET /api/workflow-history` without auth returns 403
- [x] Workflows panel shows a loading indicator while fetching; on error shows "Could not load workflow history." message; on empty list shows "No workflow runs." message
- [x] Past runs rendered newest-first; most recent run starts `open`; others start closed
- [x] Past run step rows are view-only (no click handler, cursor: default)
- [x] All test fixtures use placeholder/synthetic session IDs and paths — no real home-directory paths or IDs
- [x] `pytest tests/test_web.py --timeout=300` passes
- [x] Python restart required after `acp.py` and `web.py` changes (note: restart PowerAtlas after deploying this phase)
- [x] `docs/KNOWLEDGE.md` `_get_tool_diffs_v3` section updated: note `_get_workflow_runs_v3` added as sibling function for workflow run extraction

Implementation (2026-10-16, code: eaf9034)
`_get_workflow_runs_v3(session_id)` in `acp.py` parses `_kiro/workflow/node_start`, `_kiro/workflow/node_complete`, and `_kiro/workflow/run_complete` user-type notification records (user payload.type == "user", payload.content is JSON with method starting `_kiro/workflow/`). Returns list of runs ordered oldest-first. Pre-probe O2 confirmed from real local data: `sub_agent_start` records do NOT carry `workflowId`; workflow tracking uses user notification records. `GET /api/workflow-history?sid=...` endpoint in `web.py` (protected by `LoopbackCredentialGate`, `asyncio.to_thread` for blocking I/O). JS fetch + render in both templates with loading/error/empty states; newest-first `<details>/<summary>` past runs. Dashboard `#dashWorkflowsPastRuns` cleared in `dashCloseIfAbandoned()` to cover both direct-switch and overview paths. Non-dict inner JSON guard added to parser. Tests: `TestWorkflowHistory` (4 tests, synthetic IDs). `docs/KNOWLEDGE.md` updated.

Per-phase QA: BLOCKED (Python + JS changes; restart permission granted; deferred to Step 9b per user decision).

---

### Phase 6: Sub-agent composer and direct step prompt [QA]

**Goal**: The sub-agent transcript panel for a live workflow step has a text composer for direct prompts; step question cards from `step_message` frames also appear in the sub-agent panel.

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/templates/acp.html`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/composer-chrome.js`, `tests/acp_page.test.mjs`, `tests/test_web.py`, `docs/KNOWLEDGE.md`

**Covers**: SC7, SC8

**Changes**:

1. **`acp.py` `_handle_prompt()`**: The guard chain requires a comprehensive modification for workflow step sessions — a single-guard relaxation is insufficient because step sessions live only in `subagent_sessions`, not in `sessions`, and the subsequent guards assume top-level sessions. Add a **dedicated early branch** for `_workflow_child_meta` sessions, placed immediately after the `bad_payload` validation checks, before the subagent guard:

   ```python
   # Sketch — adapt to exact code structure; place BEFORE the subagent_sessions guard
   if session_id in _supervisor._workflow_child_meta:
       child_meta = _supervisor._workflow_child_meta[session_id]
       parent_id = child_meta.get("parent")
       # Allow a connection subscribed to the parent OR to the step itself.
       if conn is not None and conn.session_id not in (session_id, parent_id):
           refuse("not_subscribed",
                  "Subscribe to this step session or its parent session before prompting it.")
           return
       # Bypass sessions check (step sessions are in subagent_sessions, not sessions).
       # Do NOT run inflight tracking, crew eviction, or _workflow_free_orphans —
       # those are parent-session operations.
       log.info("ACP step-direct prompt: step=%s parent=%s (%d chars)",
                session_id, parent_id, len(text))
       # Route the prompt to the step session via the supervisor.
       # IMPORTANT: Before implementing, read `_supervisor.prompt()` to verify whether
       # it accesses `_supervisor.sessions[session_id]` or resolves the connection
       # independently. If it accesses sessions[], a secondary lookup via
       # subagent_sessions is required. Adjust accordingly and record any deviation
       # in the Phase 9 implementation divergences section.
       try:
           result = await _supervisor.prompt(session_id, spoken, images)
       except Exception:
           log.exception("ACP step-direct prompt failed: step=%s", session_id)
           _emit(session_id, error_frame("internal_error",
                 "The step prompt failed; see orchestrator.log.", session_id))
       return
   ```

   The existing `if session_id in _supervisor.subagent_sessions:` guard (below the early branch) remains unchanged for non-`_workflow_child_meta` sub-agent sessions (they remain read-only).

2. **`composer-chrome.js` `initSubAgentComposerDom({refs})`** (new function):
   - Accepts: `{subComposerInput, subComposerSend, subComposerContainer, sendFn}`.
   - `sendFn` is the page's WebSocket send function reference.
   - Exports `setSubAgentComposerEnabled(enabled, targetSid)`: when `enabled`, show `subComposerContainer` and store `targetSid`; when disabled, hide the container, clear `targetSid`, and show a brief "Step completed." text in the container if `targetSid` was previously set (to give visual feedback when the composer disappears mid-session).
   - On send button click and on Enter key in `subComposerInput`: if `targetSid` is set and `subComposerInput.value.trim()` is non-empty:
     - Capture the text value before clearing.
     - Check WebSocket ready-state: if OPEN, call `sendFn('prompt', {prompt: capturedText})` (note: the payload key is **`prompt`**, not `message` — `_handle_prompt` reads `payload.get("prompt")`; the `sessionId` is in the outer envelope via `conn.session_id` when subscribed to the step session, OR use `sendFn('prompt', {prompt: capturedText}, targetSid)` if the page's `send()` accepts a third envelope-sessionId argument).
     - On success: clear the input.
     - On send error (WebSocket not OPEN or exception): do NOT clear the input; show a brief "Send failed — check connection." note in the composer area (no `innerHTML`).
   - Verify the correct `send()` call signature in the page (whether sessionId goes as a third arg or must be in the payload) before finalizing this call.
   - Verify no collision with existing composer-chrome globals.

3. **`acp.html` `#acpSubPanel`**: Add a composer at the panel bottom (no `innerHTML` anywhere):
   - `<div id="acpSubComposerContainer" hidden>` containing an `<input type="text" id="acpSubComposerInput">`, a `<button id="acpSubComposerSend">Send</button>`, and a `<span id="acpSubComposerStatus" hidden>` (for error/status messages).
   - Call `initSubAgentComposerDom({subComposerInput: acpSubComposerInput, subComposerSend: acpSubComposerSend, subComposerContainer: acpSubComposerContainer, sendFn: send})` at page init.
   - Add `role="button"` and `tabindex="0"` to the `#acpSubComposerSend` button if it is a `<button>` element (it already has implicit keyboard support as a button; this is a reminder not to use a `div` as the send button).

4. **`acp.html` `openSubagent(sessionId)`**: Track live workflow child SIDs:
   - Maintain a module-level `Set` named `_liveWorkflowChildSids`.
   - When a `subagents` frame arrives with `payload.workflowLive === true`: add all `payload.subagents[i].sessionId` values to `_liveWorkflowChildSids`.
   - When a crew-eviction broadcast arrives (empty `subagents` frame, `payload.workflowLive === false` with an empty array, or a `session` frame for a new session — i.e., on session switch): clear `_liveWorkflowChildSids`.
   - Also clear `_liveWorkflowChildSids` at workflow run completion: when a `subagents` frame arrives with `payload.workflowLive === false` (regardless of whether the array is empty), clear the set — the run is over.
   - In `openSubagent(sessionId)`: call `setSubAgentComposerEnabled(_liveWorkflowChildSids.has(sessionId), sessionId)`.

5. **`acp.html` `step_message` card in sub-agent panel**:
   - The main WebSocket already delivers `step_message` frames (they are recorded and arrive in `subagents` frames or as stand-alone frames to the parent session's subscribers).
   - When a `step_message` frame arrives and the sub-agent panel is open (`subViewSid` is set) and `payload.step.sessionId === subViewSid`: render an amber question card (`div.acp-step-question-card`) in `#acpSubPanel` above the composer.
   - Card content (no `innerHTML`): message text, and if `payload.step.options` is present, one button per option.
   - Clicking an option routes the reply through the EXISTING parent-session mechanism (unchanged — do not route option replies to the step session directly).
   - Clear the card when `subViewSid` changes, when a new `step_message` replaces the previous one for the same step, OR immediately when an option button is clicked (visually disable/hide the card before the reply completes, so the user sees the click registered).

6. **`index.html` `#dashSubPanel`**: Mirror changes from steps 3–5 using the dashboard's equivalents (`dashSubComposerContainer`, etc.).

**Exit criteria**:
- [x] `_handle_prompt` early branch: a session in `_workflow_child_meta` routes to the dedicated step-prompt path; the existing subagent guard still refuses sessions in `subagent_sessions` only (not in `_workflow_child_meta`)
- [x] `_supervisor.prompt()` resolution verified for step sessions (read the function; record any deviation in divergences) — Deviation: `_supervisor.prompt()` checks `self.sessions` which excludes step sessions. Used `_supervisor._request("session/prompt", ...)` directly with `REQUEST_TIMEOUT_SECONDS` instead.
- [x] `initSubAgentComposerDom` and `setSubAgentComposerEnabled` exported from `composer-chrome.js`; no global name collision
- [x] Sub-agent panel shows composer when opened for a live workflow step (`_liveWorkflowChildSids` contains the sessionId)
- [x] Sub-agent panel hides composer (with "Step completed." notice) for completed workflow steps; hidden for non-workflow sub-agents
- [x] `_liveWorkflowChildSids` cleared on session switch and on `workflowLive === false` frames
- [x] Sending from the composer uses `{prompt: text}` payload key (not `message`); verifies send() signature for sessionId routing — send(type, payload, sid) confirmed; uses `send('prompt', {prompt: text}, stepSid)`
- [x] Send failure (WS not OPEN) leaves input populated and shows error note; success clears input
- [x] `step_message` frames matching `subViewSid` render an amber card in `#acpSubPanel` (and `#dashSubPanel`) — requires `stepSessionId` field added to `step_message` frame payload by server
- [x] Clicking an option on the step question card immediately hides/disables the card; reply routes via existing parent-session mechanism — Note: step_message frames carry no options (warning messages only); the card is informational only with no option buttons. No options to wire.
- [x] No `innerHTML` used anywhere in the new sub-panel additions
- [x] `tests/test_web.py`: step-prompt tests — (a) session in `_workflow_child_meta` routes to dedicated step-prompt path (not refused); (b) session in `subagent_sessions` only still gets `read_only_session` refusal
- [x] `tests/acp_page.test.mjs`: composer enable/disable tests; `step_message` card rendering test
- [x] Python restart required for `acp.py` change
- [x] `pytest tests/test_web.py --timeout=300` passes (3528 passed)
- [x] `node tests/acp_page.test.mjs` passes (1152 passed)
- [x] `docs/KNOWLEDGE.md` `_handle_prompt` section updated: note guard relaxation for `_workflow_child_meta` sessions; note `step_message` frames now surface in the sub-agent panel

Implementation (2026-10-16, code: 545df46)
`_handle_prompt` in `acp.py`: dedicated early branch for `_workflow_child_meta` sessions before the `subagent_sessions` read-only guard; routes via `_supervisor._request("session/prompt", ...)` directly (bypasses `sessions` membership check); subscription gate allows prompts from parent or step subscriber only. `_emit_step_message` extended with `step_id` parameter; `_on_session_notify` updated. `initSubAgentComposerDom` + `setSubAgentComposerEnabled` in `composer-chrome.js`; composer HTML added to `#acpSubPanel` and `#dashSubPanel` in both templates. `_liveWorkflowChildSids` set tracks live workflow child SIDs; cleared on session switch and `workflowLive === false`. `step_message` handler renders amber `.acp-step-question-card` in sub-panel when `payload.stepSessionId === subViewSid`. Dashboard mirrors all changes. Tests: `TestAcpWorkflowStepDirectPrompt` (4 tests incl. subscription gate + exception path), 5 JS tests.

Per-phase QA: BLOCKED (Python restart needed; deferred to Step 9b — restart permission granted for this task).

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| R1 — Direct prompt to step session may not clear the step question pending state | Step paused indefinitely if answer via direct prompt doesn't reach workflow engine | Step question replies continue to route via the parent session (unchanged). Direct composer is for free-form prompts only. Verify at execution (O1). |
| R2 — `_workflow_child_meta` cleared before user attempts interaction | Composer disabled for a step that completes while the panel is open | Guard `session_id not in _workflow_child_meta` is correct for the normal case; completed steps don't accept prompts, which is the expected behavior. Document in Phase 6 UI that composer disappears when step completes. |
| R3 — `messages.jsonl` workflow record shapes unconfirmed | Parser fails silently or returns empty runs | Phase 5 pre-probe verifies shapes before implementation; parser notes deviations in code comments. |
| R4 — `_vizHeightsListening` listener orphan (resolved) | None — confirmed harmless | Removed iframes cannot produce matching events in the listener (verified by reading `addVisualizationCard` implementation; no code change needed). |
| R5 — `composer-chrome.js` global name collision | Runtime errors if new names shadow existing ones | Pre-check the collision list at the top of `composer-chrome.js` before introducing any new exported names (enforced in Phase 4 and Phase 6 exit criteria). |

A risk whose Mitigation is anything other than "addressed within this plan" for R1: see Follow-up Work item 1.

---

## 7) Verification

- PowerAtlas running at `http://127.0.0.1:<port>` (read port from `%LOCALAPPDATA%\power-atlas\config.toml` or `orchestrator.log`).
- Sign in via `pa_local` cookie (see `AGENTS.md § Verification Setup`). Mint via `w.login_path(w.mint_login_code())` or directly from a Playwright script using `ctx.add_cookies([...])`.
- Browser: Playwright from `.venv-PowerAtlas` or Chrome via Claude-in-Chrome MCP. Hard reload (`Ctrl+Shift+R`) picks up JS/CSS changes without restart.
- **Python changes require restart** (`acp.py`, `web.py`): phases 1, 5, and 6 include Python changes. Restart PowerAtlas after each such phase before running live QA (per `AGENTS.md`: never restart without explicit user permission in the current task).
- **SC1 (viz reload)**: trigger `emit_visualization` from a Default ACP session; reload the /acp page and confirm the viz card reappears.
- **SC1 (dashboard)**: subscribe the dashboard to the session; trigger `emit_visualization`; confirm viz card appears in the dashboard transcript.
- **SC2 (queue-waiting restore)**: start two sessions; trigger one mid-turn while the other is queued behind it; reload the /acp page for the waiting session; confirm the steer-status bar shows the waiting message.
- **SC3 (crew panel tree)**: start a workflow with parallel steps; confirm group-label rows appear in the crew panel; click a group row to expand/collapse.
- **SC4–SC5 (Workflows panel)**: run multiple workflow steps across several turns; open the Workflows panel; confirm all past runs appear and are collapsible.
- **SC6 (click-to-sub-panel)**: click a live step row in the Workflows panel; confirm the sub-agent panel opens for that step.
- **SC7–SC8 (wf interaction)**: start a workflow; open the sub-agent panel for a running step; confirm the composer is visible; send a free-form prompt; confirm it routes to the step session.
- Python tests: `pytest tests/test_web.py --timeout=300`
- Node tests: `node tests/acp_page.test.mjs`

---

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `docs/KNOWLEDGE.md` (crew panel / `renderCrewPanel` section) | Update description of crew panel rendering from flat list to tree structure; note group-label rows inferred from `role` path | 3 |
| `docs/KNOWLEDGE.md` (`_handle_subscribe` section, broadcast-only resend invariant) | Note that `visualization` frames are now also resent on subscribe (exception to the broadcast-only rule); add `"queued"` field to the session frame description | 1 |
| `docs/KNOWLEDGE.md` (`_handle_prompt` section, around "read-only sub-agent" guard) | Note that workflow step sessions (in `_workflow_child_meta`) are no longer blocked by the read-only guard | 6 |
| `docs/KNOWLEDGE.md` (`_workflow_child_meta` section, prompt routing lines) | Note the guard relaxation: `_handle_prompt` now allows prompts for sessions in `_workflow_child_meta` | 6 |
| `docs/KNOWLEDGE.md` (`step_message` / `_on_session_notify` section) | Note that `step_message` frames are also surfaced in the sub-agent panel for the matching step session | 6 |
| `docs/KNOWLEDGE.md` (`_get_tool_diffs_v3` section) | Note that `_get_workflow_runs_v3` was added as a sibling function for workflow run history extraction | 5 |
| `README.md` | No update — no installation, basic-usage, or user-visible CLI surface changes | — |
| `memory/MEMORY.md` (broadcast-only resend invariant, setCrew, _handle_subscribe entries) | Update entries that assert visualization frames are never replayed on subscribe; reflect guard relaxation; reflect tree rendering — managed by /qdream, not during implementation | doc-table-only |

---

## Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Reload fidelity | Complete | Python restart needed after; code: 2f937dc |
| 2 | Dashboard viz handler | Complete | Handled by sibling plan (fb6ebf8) |
| 3 | Crew panel tree structure | Complete | JS/CSS only; code: a85816c |
| 4 | Workflows panel shell | Complete | JS/CSS only; code: 973e182 |
| 5 | Workflow history from disk | Complete | Python restart needed after; code: eaf9034 |
| 6 | Sub-agent composer and direct step prompt | Complete | Python restart needed after; code: 545df46 |

Mark tasks that can run in parallel with `[P]`:
- Tasks sharing the same `[P]` marker within a phase may be executed concurrently
- Unlabeled tasks are sequential by default

---

## 9) Implementation Divergences from Plan

<!-- Reserved — filled during implementation -->

---

## Completion Summary

### Acknowledged at archival

All 6 phases implemented and reviewed. 18 review findings (0 High, 4 Medium, 14 Low) — all resolved or accepted with follow-up notes in `## Follow-up Work (Deferred)`.

**4 live-QA surfaces BLOCKED — manual verification recommended before release:**

- SC1 — Viz reload (viz iframe cards reappear after page reload): requires a live session that has received `emit_visualization` + page reload. Automated probes confirmed `meta["visualizations"]` recording and the `/api/viz/` endpoint security; the browser-level reload sequence was not driven.
- SC3 — Crew panel tree live (group-label rows appear, click-to-expand works in a running workflow): requires a live workflow with parallel steps. Crew panel tree CSS classes and JS code paths confirmed present; runtime interaction with live `subagents` frames was not driven.
- SC6 — Click-to-sub-panel from Workflows panel (clicking a step row opens the sub-agent panel): requires a live workflow run in the Workflows panel. Panel DOM and `openSubagentFn` wiring confirmed present; end-to-end click path with a live step session was not driven.
- SC7/SC8 — Sub-agent composer for direct step prompt (composer visible and send routes to step): requires a live workflow step session. Composer DOM, `setSubAgentComposerEnabled` logic, and `_handle_prompt` early branch confirmed present; live send path was not driven.

These four surfaces pass all automated tests. All other verifiable behaviors (session creation, workflows toggle, `_handle_prompt` routing, `/api/workflow-history` security, `step_message` `role=alert`, sub-panel composer DOM) passed the Step 9b automated probes.

---



1. **Verify step question answer routing via direct step prompt (O1).** If direct `session/prompt` to a step session correctly clears its step question pending state in kiro-cli's workflow engine, the Phase 6 mitigation (step question option replies still route via the parent) can be simplified to allow direct routing. Source: R1, O1. Re-verify at execution time.

2. **Assess mixed-crew frames (workflow + standalone sub-agents).** If a session can have both workflow children and standalone sub-agents active simultaneously, the frame-level `workflowLive` flag used to gate the sub-agent composer may yield false positives (enabling the composer for a non-workflow sub-agent within a workflow run's crew frame). If this case is confirmed at runtime, add a per-entry `isWorkflowStep` boolean to `_subagents_payload()` in `acp.py`. Source: Phase 6 design decision.

3. **Verify `send()` signature for direct step prompts.** The Phase 6 composer calls `sendFn('prompt', {prompt: text})` but the correct routing to the step session (whether via `conn.session_id` after subagent_subscribe, or via a third envelope-sessionId arg) must be confirmed by reading `acp.html`'s `send()` function and `_handle_prompt`'s `session_id` resolution. Source: Phase 6 change note.

4. **Phase 1 review: no FIFO eviction-order assertion.** The `test_cap_enforced_after_21_calls` test verifies the count is exactly 20 but does not assert which entries are kept (most recent 20). A reversed-order cap bug would pass. Low priority — cap semantics are clear from `[-20:]`. Source: Phase 1 review cycle 2, Senior engineer, accepted by user.

5. **Phase 1 review: NOTE comment sync obligation.** `test_viz_size_cap_limits_recorded_entries_to_20` has an inline comment noting its cap logic must be kept in sync with `web.py`. This creates a manual obligation. Consider extracting the cap constant to a named value. Source: Phase 1 review cycle 2, Reliability engineer, accepted by user.

6. **Phase 3 review: crew focus restore untestable by CI.** The `_crewFocusKey` save/restore in `renderCrewPanel` is correct by inspection but cannot be tested by the DOM stand-in (no `document.activeElement`). Cover with a browser smoke test during Step 9b. Source: Phase 3 review cycle 2, N1.

7. **Phase 3 review: focusKey non-unique for sessionId-less entries.** `'step:' + (entry.sessionId || '')` produces `'step:'` for every entry with no sessionId. Theoretical — production uses UUIDs. Source: Phase 3 review cycle 2, N2.

8. **Phase 3 review: querySelector injection risk in focus restore.** `[data-focus-key="..."]` attribute selector could fail if `entry.role` or `entry.sessionId` contains `"`. Theoretical — current format is alphanumeric. Source: Phase 3 review cycle 2, N3.

9. **Phase 4 review: dead CSS class `wf-panel`.** `class="wf-panel"` in both templates has no CSS rule — left for Phase 5/6 finalization. Source: Phase 4 review, F3.

10. **Phase 4 review: dashboard path (`isDashboard: true`) has no independent test.** Add a test passing `isDashboard: true` in Phase 5/6. Source: Phase 4 review, F4.

11. **Phase 5 review: Test A user-type filter blind spot.** Removing `payload.type != "user"` filter doesn't fail Test A. Add a variant with a non-user record carrying workflow JSON. Source: Phase 5 review cycle 2, N2.

12. **Phase 5 review: Test A step-type filter blind spot.** Add a fixture with a `node_start` with `type: "root"` to make the `node_type == "step"` guard testable. Source: Phase 5 review cycle 2, N3.

13. **Phase 6 review: subscription gate test uses implicit environment assumption.** `test_not_subscribed_when_conn_is_on_an_unrelated_session` detects its target via the implicit `_loop is None` path. Harden by adding `patch.object(_Supervisor, "_request")` mock (like the first test in the class). Source: Phase 6 review cycle 2, F1 fragility.

14. **Step 9: `wf-panel` CSS class unstyled.** Add `.wf-panel { overflow-y: auto; max-height: 400px; ... }` to `style.css`. Source: Step 9 Senior engineer, F2.

15. **Step 9: past-run history inaccessible after live run ends.** `clearWorkflowsLiveRun` hides the toggle, making past-run content in DOM unreachable. Fix: keep toggle visible when `#acpWorkflowsPastRuns` is non-empty. Source: Step 9 Senior engineer, F3.

16. **Step 9: stale "No workflow runs" blocks refresh on second run.** The `pastRuns.childNodes.length` fetch gate prevents re-fetch. Fix: clear `pastRunsContainer` inside `clearWorkflowsLiveRun`. Source: Step 9 Senior engineer, F4.

17. **Step 9: no JS test for `renderWorkflowsPastRuns`.** Add a test asserting `run.name` and `step.agentName` appear in the rendered output. Source: Step 9 Architect, F3.

---

## Review Log

### 2026-10-16 — Post-Implementation Review

Overall implementation health: Green.
Personas: Senior engineer, Security auditor, Reliability engineer, End-user advocate, Architect.
18 findings across 6 phases (0 High, 4 Medium, 14 Low). All resolved or accepted with follow-up notes.
QA verification: PASS (automated) / BLOCKED (4 surfaces need live workflow).
**QA results (9b)**: 9/10 automated probes PASS, 1 SKIP (session ID eval), 4 BLOCKED.
  PASS: session create, workflows toggle hidden, crew tree CSS, viz functions, sub-agent composer DOM, panel DOM elements, /api/workflow-history 400/403 security, step_message role=alert code present.
  BLOCKED: SC1 viz reload, SC3 crew tree live, SC6 click-to-sub-panel, SC7/SC8 sub-agent composer (require live workflow — manual verification recommended before release).

#### Test execution summary

| Phase | Tests | QA | Notes |
|---|---|---|---|
| 1: Reload fidelity | pass | Deferred to 9b | Python restart needed |
| 2: Dashboard viz handler | pass (sibling plan) | Deferred to 9b | Handled by sibling plan |
| 3: Crew panel tree | pass | Deferred to 9b | JS/CSS only |
| 4: Workflows panel shell | pass | Deferred to 9b | JS/CSS only |
| 5: Workflow history from disk | pass | Deferred to 9b | Python restart needed |
| 6: Sub-agent composer | pass | Deferred to 9b | Python restart needed |

| # | Severity | Finding (one line) | Resolution |
|---|---|---|---|
| 1 | Medium | KNOWLEDGE.md `_get_workflow_runs_v3` described wrong parser (sub_agent_start approach). | Fixed — updated to notification-record description (06d98dc). |
| 2 | Medium | Past-run step rows blank: JS read `step.role\|\|step.subSessionId` but API returns `{nodeId, agentName}`. | Fixed — changed to `step.agentName\|\|step.nodeId` in both templates (06d98dc). |
| 3 | Medium | Run label used `run.workflowId` ignoring `run.name` from `workflowName\|\|runLabel`. | Fixed — changed to `run.name\|\|run.workflowId\|\|…` (06d98dc). |
| 4 | Medium | `step_message` amber card had no `role="alert"` — screen readers don't announce it. | Fixed — added `role="alert"` in both templates (06d98dc). |
| 5 | Low | `wf-panel` CSS class unstyled in both templates. | Orchestrator: proposed-accept — follow-up item #14. |
| 6 | Low | Past-run history inaccessible after live run ends (toggle hidden by clearWorkflowsLiveRun). | Orchestrator: proposed-accept — SC5 design limitation, follow-up item #15. |
| 7 | Low | Stale "No workflow runs" after first empty fetch blocks refresh on second run. | Orchestrator: proposed-accept — follow-up item #16. |
| 8 | Low | No JS test for renderWorkflowsPastRuns — field-name bugs had no test. | Orchestrator: proposed-accept — follow-up item #17. |
| 9–18 | Low | (Phase-specific Lows from Step 5/9 per-phase reviews — all resolved or accepted per phase logs) | See phase review log entries above. |

### 2026-10-16 — Implementation Review (after Phase 6, personas: Senior engineer, Security auditor, Reliability engineer, End-user advocate)

Implementation health: Green.
4 personas, full effort. 2 cycles. 5 findings (0 High, 1 Medium, 4 Low). All resolved or accepted.

| # | Severity | Finding (one line) | Resolution |
|---|---|---|---|
| 1 | Medium | Subscription gate (`conn.session_id not in (session_id, parent_id)`) had no mutation-catching test; removing it left existing tests green. | Fixed — `test_not_subscribed_when_conn_is_on_an_unrelated_session` added (a1c6465). |
| 2 | Low | `_request` exception path (`except Exception → internal_error`) untested. | Fixed — `test_request_exception_sends_internal_error_frame` added (a1c6465). |
| 3 | Low | `index.html` comment said "`dashSend` resolves" — no `dashSend` function exists. | Fixed — updated to "`send` resolves at call time" (a1c6465). |
| 4 | Low | `composer-chrome.js` JSDoc also said "`dashSend`" — not updated with the `index.html` fix. | Fixed — updated to "On both pages the ref wraps the plain `send` function" (545df46). |
| 5 | Low | Subscription gate test uses implicit `_loop is None` environment assumption instead of explicit `_request` mock. | Orchestrator: proposed-accept — test works correctly; adding explicit mock is a hardening item (Follow-up #13). |

### 2026-10-16 — Implementation Review (after Phase 5, personas: Senior engineer, Security auditor, Reliability engineer, End-user advocate)

Implementation health: Green.
4 personas, full effort. 2 cycles. 5 findings (0 High, 2 Medium, 3 Low). All resolved or accepted.

| # | Severity | Finding (one line) | Resolution |
|---|---|---|---|
| 1 | Medium | `_get_workflow_runs_v3` parsed wrong record type — looked for `sub_agent_start` with `workflowId`, which doesn't exist; returned `[]` for all real sessions. | Fixed — rewritten to parse `_kiro/workflow/node_start|node_complete|run_complete` user notifications (commit b8b609d, confirmed from real local data). |
| 2 | Medium | Dashboard `#dashWorkflowsPastRuns` clear placed in `dashShowOverview()` only; direct session-switch path (`dashCloseIfAbandoned`) wasn't cleared. | Fixed — moved clear to `dashCloseIfAbandoned()` which fires on both paths (commit eaf9034). |
| 3 | Low | Non-dict inner JSON (array, string) would raise AttributeError and abort entire scan via outer except. | Fixed — added `if not isinstance(inner, dict): continue` guard (commit eaf9034). |
| 4 | Low | Test A `user`-type filter blind spot — removing filter wouldn't fail test. | Orchestrator: proposed-accept — follow-up test improvement (see Follow-up Work #11). |
| 5 | Low | Test A step-type filter blind spot — one `node_start` with `type: "step"` insufficient to gate the filter. | Orchestrator: proposed-accept — follow-up test improvement (see Follow-up Work #12). |

### 2026-10-16 — Implementation Review (after Phase 4, personas: Senior engineer, End-user advocate, Reliability engineer, Maintainability reviewer)

Implementation health: Green.
4 personas, full effort. 1 cycle. 4 findings (0 High, 2 Medium, 2 Low). All resolved or accepted.

| # | Severity | Finding (one line) | Resolution |
|---|---|---|---|
| 1 | Medium | `clearWorkflowsLiveRun` hid toggle but left panel open — stranded open with no close button when workflow ends. | Fixed — `clearWorkflowsLiveRun` now also sets `panel.hidden = true` and resets `aria-expanded="false"` (commit 973e182). |
| 2 | Medium | `.wf-step-row` divs had no `tabindex`, `role="button"`, or keydown handler — keyboard inaccessible. | Fixed — `role="button"`, `tabindex="0"`, Enter/Space keydown added to `setWorkflowsLiveRun` step rows (commit 973e182). |
| 3 | Low | `class="wf-panel"` in both templates has no CSS rule — dead class. | Orchestrator: proposed-accept — resolves in Phase 5/6 when panel styles are finalized. |
| 4 | Low | Phase 4 tests only cover acp.html path; `isDashboard: true` branch not tested. | Orchestrator: proposed-accept — dashboard variant test deferred to Phase 5/6 follow-up. |

### 2026-10-16 — Implementation Review (after Phase 3, personas: Senior engineer, End-user advocate, Reliability engineer, Maintainability reviewer)

Implementation health: Green.
4 personas, full effort. 2 cycles. 8 findings (1 High, 4 Medium, 3 Low). All resolved or accepted.

| # | Severity | Finding (one line) | Resolution |
|---|---|---|---|
| 1 | High | Focused DOM node destroyed every second by 1s re-render timer — keyboard focus lost on each update. | Fixed — focus save/restore via `dataset.focusKey` + `_crewFocusKey` applied before/after rebuild (5344781). |
| 2 | Medium | `groupMap = {}` crashes on role names matching Object prototype properties (e.g. `"constructor"`). | Fixed — changed to `Object.create(null)` in both templates (5344781). |
| 3 | Medium | `.acp-crew-group` (keyboard-focusable div) had no `:focus-visible` rule; inconsistent with step rows. | Fixed — added `.acp-crew-group:focus-visible { outline }` to style.css (2402a43). |
| 4 | Medium | Keydown (Enter/Space) handler had zero test coverage (4 personas corroborated). | Fixed — SC3 Test B2 added, using harness `dispatch()` API (2402a43). |
| 5 | Low | Badge states "has failures" and "waiting" untested. | Fixed — SC3 Tests E and F added (5344781). |
| 6 | Low | Child row `aria-label` included redundant group prefix (e.g. "run-both/step-a" vs "step-a"). | Fixed — `roleForLabel = entry.role.replace(/^.*\//, '')` in both helpers (5344781). |
| 7 | Low | SC3 Test C didn't assert role span text after abbreviation fix — regression from Fix 6 invisible. | Fixed — added role span assertion to SC3 Test C (a85816c). |
| 8 | Low | "keep in sync" comments added by reviewer (inappropriate but correct changes included in commit). | User: accepted — reviewer-applied comments are accurate and harmless. |

### 2026-10-16 — Implementation Review (after Phase 1, personas: Senior engineer, Reliability engineer, Security auditor, End-user advocate)

Implementation health: Green.
4 personas, full effort. 2 cycles. 5 findings (0 High, 1 Medium, 4 Low). All resolved.

| # | Severity | Finding (one line) | Resolution |
|---|---|---|---|
| 1 | Medium | No test exercised `web.py` recording path; deleting `meta["visualizations"]` block left all tests green. | Fixed — `TestVizRecording` class added with 2 tests calling `_handle_emit_visualization` directly (commit 0ccd4d7). |
| 2 | Low | `SERVER_TYPES` comment still said "not recorded (no replay)" after Phase 1 added replay. | Fixed — comment updated to note `meta["visualizations"]` replay path (commit 0ccd4d7). |
| 3 | Low | URL path `f"/api/viz/{session_id}/{filename}"` built twice independently. | Fixed — extracted as `url_path` local variable used in both places (commit 0ccd4d7). |
| 4 | Low | Cap test assertion `<= 20` too weak; regression tightening cap to 19 would pass. | Fixed — changed to `== 20` (commit 2f937dc, user-directed post-cap-cleanup). |
| 5 | Low | web.py inline comment still said "live-only" after Phase 1 added meta recording. | Fixed — comment updated to note broadcast-only from `_History` perspective, meta recording via `meta["visualizations"]` (commit 2f937dc). |

### 2026-10-16 — Plan creation (via /qplan)

26 findings (1 High, 14 Medium, 11 Low). All auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | Phase 6 `_handle_prompt` single-guard relaxation insufficient — step sessions not in `sessions`, blocked by second guard; downstream body incompatible. | Fixed — dedicated early branch added for `_workflow_child_meta` sessions; bypasses `sessions` check and inflight/eviction body. |
| 2 | Medium | Phases 1 (steps 3-5) and 2 duplicate SESSION_SWITCH plan SC4/SC5; parallel execution risks merge conflicts. | Fixed — coordination notes added to Phase 1 and Phase 2 with skip-if-landed instructions. |
| 3 | Medium | `@_local_gate` decorator does not exist in `web.py`; auth is ASGI middleware (`LoopbackCredentialGate`). | Fixed — Phase 5 auth description corrected to ASGI middleware; note not to add to `_local_gate_exempt`. |
| 4 | Medium | `V3_SESSIONS_ROOT`/`_V3_EXCLUDED_NAMES` are in `data_kiro_v3.py`; `acp.py` has an isolation boundary and cannot import them. | Fixed — Phase 5 parser spec updated to use inline path construction (follow `_get_tool_diffs_v3` pattern exactly). |
| 5 | Medium | `_get_workflow_runs_v3` called synchronously in an async handler; same blocking I/O as `_get_tool_diffs_v3` which uses `asyncio.to_thread`. | Fixed — `asyncio.to_thread` wrapping added to Phase 5 endpoint spec. |
| 6 | Medium | SC1 says "original positions" but design decision says "appended after history replay" — contradiction. | Fixed — SC1 wording updated to describe append-after-history behavior. |
| 7 | Medium | `meta["visualizations"]` grows without bound for long-running sessions. | Fixed — size cap of 20 entries added to Phase 1; Test C added. |
| 8 | Medium | Phase 5 client fetch has no error handler; unhandled rejection leaves panel blank with no feedback. | Fixed — loading, error, and empty states added to Phase 5 client spec. |
| 9 | Medium | Aggregate badge missing "failed" state; failed steps silently hidden by collapsed-group default. | Fixed — "has failures" arm added to Phase 3 badge logic; failed groups start expanded. |
| 10 | Medium | Workflows panel has no loading, error, or empty state. | Fixed — loading/error/empty states added to Phase 5 client rendering. |
| 11 | Medium | Past runs rendered oldest-first; most recent run buried below older runs. | Fixed — Phase 5 specifies newest-first ordering; most recent `<details>` starts `open`. |
| 12 | Medium | Step question card not cleared on option click; persists visually after user answers. | Fixed — Phase 6 adds immediate hide/disable on option button click. |
| 13 | Medium | Viz cards resent without visual context marker; appear indistinguishable from new output. | Fixed — Phase 1 adds a "Restored visualizations" separator before resent viz cards. |
| 14 | Medium | Group-label rows and step rows use `div` with click handlers but have no ARIA or keyboard access. | Fixed — Phase 3 adds `role="button"`, `tabindex="0"`, `aria-expanded`, and Enter/Space keydown handlers to group-label rows; Phase 4 toggle buttons get `aria-expanded`/`aria-controls`. |
| 15 | Medium | Sub-agent composer clears input unconditionally; send failure loses the user's typed text. | Fixed — Phase 6 spec: capture text before clear, check WS ready-state, restore on failure, show error note. |
| 16 | Medium | Phase 6 composer sends `{message: text}` but `_handle_prompt` reads `payload.get("prompt")`; every send refused as "bad_payload" (empty text). | Fixed — Phase 6 composer spec updated to use `{prompt: text}` key; `send()` signature verification added as exit criterion. |
| 17 | Low | O3 verification claimed "window.frames" mechanism; actual implementation may differ. | Fixed — O3 exit criterion generalized to verify "removed iframes cannot produce matching events" without assuming window.frames specifically. |
| 18 | Low | `_liveWorkflowChildSids` not cleared on session switch. | Fixed — Phase 6 step 4 adds session-switch clear. |
| 19 | Low | `_liveWorkflowChildSids` not cleared at workflow run completion (turn-end eviction uses `broadcast_empty=False`). | Fixed — Phase 6 step 4 adds `workflowLive === false` frame as clearing trigger. |
| 20 | Low | `<details>/<summary>` default state (all open vs. all closed) not specified. | Fixed — Phase 5 specifies newest-first with most recent `open`, all others closed. |
| 21 | Low | When composer is hidden mid-session (step completes), no visual feedback for user. | Fixed — `setSubAgentComposerEnabled(false)` now shows "Step completed." notice in composer area. |
| 22 | Low | Workflows panel toggle buttons missing `aria-expanded`/`aria-controls`. | Fixed — Phase 4 toggle button markup includes `aria-expanded="false"` and `aria-controls`. |
| 23 | Low | R2 Risk Assessment description incorrectly claims `_workflow_child_meta` is cleared at `node_complete`. | Fixed — Risk Assessment R4 description corrected. |
| 24 | Low | R4 O3 description referred to `window.frames` (Live HTMLCollection) as the mechanism. | Fixed — R4 description generalized. |
| 25 | Low | Phase 4 file scope missing `tests/acp_page.test.mjs`. | Fixed — Phase 4 file scope retained `acp_page.test.mjs` in exit criteria; verify against file scope line. |
| 26 | Low | Phase 5 returns oldest-first; combined with loading/empty state additions and newest-first ordering this is a coherent fix. | Fixed — see findings 10, 11, 20. |

Personas: Architect, Senior engineer, Security auditor, Reliability engineer, End-user advocate. Effort: Full.

---

## Harness Improvement Opportunities

<!-- Reserved — filled by /qexplore, /qplan, /qdev when harness friction is felt.
Format: - <observation> — cost: <what the friction actually cost> — suggested change: <one-line>
Leave the heading even if empty. -->
