# Session fidelity, workflow panel tree, Workflows history, and WF agent interaction

> **Date**: 2026-10-16
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Viz/queue reload fidelity; crew panel tree; per-session Workflows history panel; direct prompt to workflow step agents

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

- SC1: After a page reload or WebSocket reconnect, visualization iframe cards reappear in the transcript at their original positions. Implemented by recording broadcast viz URLs in `meta["visualizations"]` and resending them via `_handle_subscribe`.
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
- Interaction from the Workflows history panel (past-run steps are read-only; composer only for live in-transcript crew panel)
- Dependency on `261016_VIZ_STORE...` — viz replay is implemented independently of store file location
- Rail or Needs Attention indicator for queued-behind sessions (steer-status line restoration only)
- Completed workflow panels replayed in the transcript itself (covered by the Workflows panel instead)

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

**Broadcast-only frames (never in replay buffer)**:
`acp.py:205–259` `SERVER_TYPES` comment block. Broadcast-only: `tool_output`, `subagents`, `queue_status`, `steer_status`, `visualization`. Recording goes via `_emit()` (acp.py:8380); broadcast-only frames call `_registry.broadcast()` directly.

**Visualization frame path**:
- Emitted at `web.py:7763` with `_registry.broadcast()` directly (not `_emit()`).
- Handled in `acp.html:5003` — calls `addVisualizationCard(payload.url, payload.title)` from `transcript-renderer.js:2988`.
- `index.html` has **zero** occurrences of "visualization" — no `dashHandle()` case; dashboard never renders viz cards even for live sessions. SC1 fix and the dashboard viz handler are separate.
- `meta["visualizations"]` does not exist; must be added.

**Queue-status frame path**:
- `_set_queued_behind(session_id, behind)` at `acp.py:4624`: stores `meta["queued_behind"]` and broadcasts `queue_status{"waiting": bool}`.
- `_handle_subscribe` (`acp.py:9118–9230`) sends a `session` frame (acp.py:9171) but does **not** include `queued_behind` or re-send `queue_status`. Fix: add `"queued": bool(meta.get("queued_behind"))` to the session frame payload.
- Client handlers at `acp.html:5170` and `index.html:5704` — both guarded by `!replaying` / `!_dashReplaying`. The session frame handler (which fires before history replay) is the right place to restore the message.

**Crew panel rendering**:
- `renderCrewPanel(slot)` at `acp.html:1879` — flat list, no hierarchy. `role` field from `acp.py:5903` is a path like `"run-both/step-a"`.
- `setCrew(entries, toolCallId, workflowLive)` at `acp.html:2007`.
- `_subagents_payload()` at `acp.py:8425` — strips internal fields, sends `sessionId`, `role`, `task`, `sessionName`, `status`, `action`, `done`, `error`, `startedAt`, `stoppedAt`.
- No group/container nodes in the wire frame (container nodes silently dropped at `acp.py:6063`). Group labels must be inferred client-side by splitting `role` on `/` and grouping entries with the same first segment.
- Dashboard equivalent: `dashSetCrew()` in `index.html` — marked "keep in sync" with `acp.html:setCrew`.

**Subscribe re-send conditions for subagents**:
`_handle_subscribe` at `acp.py:9192`: re-sends `subagents` only if `session_id in _supervisor.inflight OR any(not e["done"] for e in crew.values())`. Completed panels not re-sent. The Workflows panel (SC4) covers this gap instead of extending the subscribe condition.

**`_handle_prompt` guard**:
`acp.py:9547–9548`: refuses `session/prompt` for ANY session in `subagent_sessions` with `_READ_ONLY_SUBAGENT_MESSAGE`. Workflow step sessions are in both `subagent_sessions` and `_workflow_child_meta`. The fix: relax the guard to `if session_id in _supervisor.subagent_sessions and session_id not in _supervisor._workflow_child_meta: refuse`.

**`composer-chrome.js`** (788 lines): pattern for shared session chrome. Each new control follows `initXxxDom({refs})` wired by each page's inline script. Collision check required for any new global names introduced.

**`transcript-renderer.js`** (3066 lines): `clearTranscript()` at line ~2931. `addVisualizationCard()` at line 2988. `initTranscriptDom(refs)` at line ~60. No `innerHTML` anywhere — all `createElement`/`textContent`.

**Test infrastructure**:
- `tests/acp_page.test.mjs`: crew panel has coverage (lines 6241+). `queue_status` cross-session behavior has **no** coverage. Visualization frame has **no** coverage.
- `tests/test_web.py`: `TestAcpReplayOnSubscribe` (line ~3301) covers subscribe re-send. No tests for `queue_status` resend or `meta["visualizations"]`.

### 5. Risks & mitigations

**R1 — Direct prompt to step session may not resolve step questions correctly.**
Step question answers normally travel via the parent session relay (`_handle_prompt` on the parent → the parent sends the answer to kiro-cli's workflow engine). If the sub-agent composer sends `session/prompt` directly to the step session, kiro-cli's workflow engine may not clear the step question pending state, causing the step to remain paused indefinitely.
Mitigation: step question reply cards in the sub-agent panel continue to route via the parent session (same existing path). The sub-agent composer is for free-form prompts only. If kiro-cli is tested and found to handle direct prompts for step questions correctly, the mitigation can be relaxed. Flag as an O1 (execution-contingent Open Item).

**R2 — `_workflow_child_meta` may be cleared before interaction is attempted.**
`_workflow_drop_child()` removes a child from `_workflow_child_meta` when `node_complete` arrives. If the user opens the sub-agent panel slightly after a step completes, the guard relaxation `session_id not in _workflow_child_meta` may already be false again, re-blocking the prompt. Mitigation: use `session_id in _supervisor.subagent_sessions and session_id not in _supervisor._workflow_child_meta` as the guard — already correct for the normal case. Add a note that completed steps don't accept prompts.

**R3 — Workflow history extraction from `messages.jsonl`.**
It is not fully confirmed which record types in the kiro-cli session `messages.jsonl` contain workflow step data (`sub_agent_start`/`sub_agent_complete` vs. raw `_kiro/workflow/*` notification records vs. something else). The implementation phase must probe `messages.jsonl` format for a session with workflow steps before writing the parser.

**R4 — viz `_vizHeightsListening` listener persists across `clearTranscript()`.**
`transcript-renderer.js:3032–3048` adds a `window.addEventListener('message', ...)` for viz iframes that is never removed. After `clearTranscript()`, the iframes are gone but the listener stays. Because the listener matches on `frames[i].contentWindow === event.source`, orphaned iframes from a cleared session should fail to match — likely harmless, but verify.

**R5 — `composer-chrome.js` global name collision.**
Any new global names for the Workflows panel must be checked against the collision list in `composer-chrome.js:30–33`.

### 6. Resolved decisions

- Q1: How to decompose? — A: ok (Option B) — Decision: Two files — this file (fidelity + workflow UI) and `261016_ACP_SESSION_SWITCH_BUGS_AND_QUEUE_RESTORE.md` (session-switch bugs)
- Q2: Workflow UI scope? — A: A+C — Decision: Tree structure in inline crew panel + Workflows history panel
- Q3: Workflows panel placement? — A: right-side panel, both pages — Decision: Right-side panel accessible from session toolbar in both /acp and dashboard; implemented via `composer-chrome.js`
- Q4: Workflow history data source? — A: ok (Option B) — Decision: Parse kiro-cli session `messages.jsonl` on subscribe; new endpoint `/api/workflow-history?sid=…`; not dependent on PowerAtlas holding the session
- Q5: Per-step metadata? — A: current fields only — Decision: Agent name (`sessionName`/`task`), role-derived group, status icon, action text, elapsed; no model/effort (no server changes for step data)
- Q6: History panel click behavior? — A + interact with wf agents — Decision: Click a live step → opens sub-agent panel with composer; past runs view-only
- Q7: WF agent interaction mechanism? — A: C — Decision: Both step question replies (via parent, unchanged) and direct free-form prompt (directly to step `sessionId` via `session/prompt`)
- Q8: Interaction from history panel? — A: ok — Decision: Interaction only from live in-transcript crew panel; Workflows history panel is view-only for past runs
- Q9: Workflows panel live content? — A: ok — Decision: Panel shows currently-running workflow with live step updates + completed past runs below
- Q10: Sub-agent composer scope? — A: ok — Decision: Both /acp (`#acpSubPanel`) and dashboard (`#dashSubPanel`) via `composer-chrome.js`

### 7. Open items

- O1 (execution-contingent): Verify whether kiro-cli's workflow engine correctly processes step question answers that arrive via direct `session/prompt` to the step session (vs. via the parent relay). If it does, the mitigation for R1 (separate routing for step question replies) can be simplified.
- O2 (execution-contingent): Probe `messages.jsonl` for a session with workflow steps to identify which record types carry workflow step data (e.g., `sub_agent_start`/`sub_agent_complete` payload shapes, or raw notification records). Required before writing the workflow history parser.
- O3 (deterministic): Confirm `_vizHeightsListening` listener orphan is harmless (R4) — a read of `transcript-renderer.js:3032–3048` and a test that clears the transcript with a viz card present.

### 8. Recommended approach

**Phase 1 — Reload fidelity** (Python + minimal JS)
- `acp.py`: when a `visualization` frame is broadcast, also append the URL to `meta.setdefault("visualizations", [])`.
- `acp.py` `_handle_subscribe`: iterate `meta.get("visualizations", [])` and re-send each as a `visualization` envelope before the history frame.
- `acp.py` `_handle_subscribe`: add `"queued": bool(meta.get("queued_behind"))` to the session frame payload.
- `acp.html` session frame handler: if `payload.queued`, call `setSteerStatus('Waiting for another session\u2019s turn to finish\u2026')`.
- `index.html` session frame handler: same via `setDashSteerStatus`.
- `test_web.py`: add tests for both new subscribe behaviors.

**Phase 2 — Dashboard viz handler** (JS only)
- `index.html` `dashHandle()`: add a `visualization` case that calls `addVisualizationCard(payload.url, payload.title)` from `transcript-renderer.js`.
- `acp_page.test.mjs`: add coverage for the visualization frame case in the dashboard.

**Phase 3 — Crew panel tree structure** (JS + CSS)
- `acp.html` `renderCrewPanel()`: parse `role` on `/`; build group-label rows (`div.acp-crew-group`) above their child step rows. Aggregate group status from children.
- `index.html` `dashSetCrew()` / `dashRenderCrewPanel()`: mirror changes.
- `style.css`: add `.acp-crew-group` styles (indent children, group header styling).
- `acp_page.test.mjs`: add crew panel tree rendering tests.

**Phase 4 — Workflows panel shell** (JS + CSS, both pages)
- Add `#acpWorkflowsPanel` markup to `acp.html` and `#dashWorkflowsPanel` to `index.html`.
- Add toggle button to session toolbar.
- Wire via `composer-chrome.js` `initWorkflowsDom({refs})`.
- Panel shows live current run from `subagents` frames (already flowing); populates as they arrive.
- `style.css`: panel layout, toggle button, run-row/step-row styles.

**Phase 5 — Workflow history from disk** (Python + JS)
- `data_kiro_v3.py` (or new `data_kiro_workflow.py`): function `parse_workflow_runs(session_id)` — reads the kiro-cli session's `messages.jsonl`, extracts workflow step records (probe O2 first), returns a list of `{runId, runLabel, startedAt, completedAt, steps: [{name, role, status, startedAt, stoppedAt}]}`.
- `web.py`: new endpoint `GET /api/workflow-history?sid=…` returns the parsed runs. Authenticated (loopback and remote).
- `acp.html` / `index.html`: on Workflows panel open, fetch `/api/workflow-history?sid=…` and render past runs below the live run.
- `test_web.py`: add endpoint tests with fixture `messages.jsonl` files.

**Phase 6 — Sub-agent composer and direct step prompt** (Python + JS, both pages)
- `acp.py` `_handle_prompt`: relax guard — `if session_id in _supervisor.subagent_sessions and session_id not in _supervisor._workflow_child_meta: refuse(...)`.
- `acp.html` `#acpSubPanel`: add a composer (prompt input + send button). On send: call `send('prompt', {sessionId: subViewSid, message: text})` directly to the step session.
- `index.html` `#dashSubPanel`: mirror.
- `composer-chrome.js`: add `initSubAgentComposerDom({refs})` and `setSubAgentComposerEnabled(bool)`.
- `acp.html` `openSubagent()`: enable the composer only when `subViewSid` is in `_supervisor._workflow_child_meta` (live step), disable for done/closed steps and non-workflow sub-agents.
- `acp_page.test.mjs`: add coverage for the new composer behavior.
- `test_web.py`: add `_handle_prompt` guard relaxation tests.

### 9. QA environment

- PowerAtlas running at `http://127.0.0.1:<port>` (read port from `%LOCALAPPDATA%\power-atlas\config.toml` or `orchestrator.log`).
- Sign in via `pa_local` cookie (see `AGENTS.md § Verification Setup`).
- Browser: Playwright from `.venv-PowerAtlas` or Chrome via Claude-in-Chrome MCP.
- For SC1 (viz reload): trigger a viz emit via `/acp`, then reload the page and confirm the card reappears.
- For SC2 (queue-waiting restore): reproduce the queued-behind state (two sessions, one mid-turn), reload the page for the waiting session.
- For SC3 (crew panel tree): start a workflow with parallel steps; confirm group label rows appear.
- For SC4-SC5 (Workflows panel): run multiple workflow steps across several turns; confirm all runs appear in the panel.
- For SC7-SC8 (wf interaction): start a workflow, open the sub-agent panel for a running step, send a free-form prompt.
- Python tests: `pytest tests/test_web.py --timeout=300`; node tests: `node tests/acp_page.test.mjs`.
