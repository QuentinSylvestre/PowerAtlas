# ACP Settings Alignment and Workflow Support

## Status: In Progress

## Intent

### Problem

PowerAtlas ACP sessions are missing a large part of the kiro-cli capability surface because `_build_kas_session_params` never sends a `_meta.kiro.settings` block in `session/new`. KAS ships all feature flags as `"off"` and only activates them when the client explicitly enables them — so PA sessions lose whatever the user has configured in kiro-cli's own `/settings`. The most acute consequence is that PA sessions never receive the `run_workflow` tool, so sessions driven through PA use the older `invoke_sub_agent` fan-out while TUI sessions (where the user has `chat.enableWorkflows: true`) dispatch multi-step parallel workflows. The `_kiro/workflow/*` notification family that drives the TUI's workflow monitor has zero handlers in `acp.py`; all workflow protocol frames fall to the INFO log and are discarded.

### Goals

1. **Dynamic settings alignment**: Read kiro-cli's `~/.kiro/settings/cli.json` at `session/new` time and forward the relevant feature flags to KAS as `_meta.kiro.settings`, mirroring the TUI's `w1()` function. Only forward settings for which PA has full productized support. No PA-specific settings — if a user changes a setting in kiro-cli's `/settings`, PA sessions pick it up automatically.

2. **Full workflow support (Layers 1–3)**: Enable `run_workflow`, `inspect_workflow`, `update_workflow`, and `send_message` in PA sessions. Handle the `_kiro/workflow/*` notification family in `acp.py` so the crew panel shows running workflow steps. Add a workflow monitor UI in `/acp` and the dashboard panel. (Layer 4 — per-step ACP subscriptions — is deferred to its own plan.)

3. **Workflow liveness**: Sessions running a workflow currently show as idle. Fix liveness so the parent session's status reflects the highest-priority status among its running workflow child sessions.

### Non-goals

- `/compact` fix (broadcast → emit) — separate plan (`plans/261008_ACP_COMPACT_FIX.md`)  
- Session cap increase — separate plan  
- Clean close — separate plan  
- Settings that need new PA UI/UX before being safe to enable (tangentMode, checkpoint, c2s, memory) — separate plans once their notification types and UI requirements are understood  
- Layer 4 (per-step child session ACP subscriptions) — follows the process-model spike

### Source

`/qexplore` 2026-10-08. Sub-agent reports: `.agents/tasks/explore-directed.md`, `.agents/tasks/explore-subsystem.md`, `.agents/tasks/explore-mutation-finder.md`.

---

## Exploration Discovery

1. **Files in scope**: `src/power_atlas/acp.py` (primary — ~7000+ lines), `src/power_atlas/data_kiro_v3.py`, `src/power_atlas/presence.py`, `src/power_atlas/templates/acp.html`, `src/power_atlas/static/composer-chrome.js`, `src/power_atlas/static/style.css`, `src/power_atlas/web.py` (for liveness pipeline).

2. **What was read**: Full directed, subsystem, and mutation-finder sub-agent passes. Live probes: KAS settings registry (`kp()` definitions from `acp-server.js`), TUI `w1()` function (full session/new settings builder), `cli.json` current state, TUI constants mapping all `chat.*` keys.

3. **Key constraints**:
   - Items 2 (enable workflows) and 5 (workflow notification handler) **must ship together**. Enabling `workflows.enabled = true` without the handler means workflow notifications are silently discarded (logged at INFO). This is confirmed by all three sub-agents.
   - Only forward settings that PA can fully support in UI/UX. Each enabled setting must have productized PA behavior; don't enable `tangentMode`, `checkpoint`, etc. until PA knows how to handle their notification types.
   - PA already reads `cli.json` for `toolSearch`; the same `_load_cli_json_settings()` path should be extended.

4. **Step 1.5**: Code-tracing trio dispatched (brownfield, predominantly source code). All three sub-agents completed successfully.

5. **Existing patterns and constraints**:
   - `_build_kas_session_params` (`acp.py:959`): currently emits only `modeId` and `steering` under `_meta.kiro`. No `settings` block.
   - `_kiro_client_capabilities` (`acp.py:887`): sends `toolSearch` in `clientCapabilities._meta.kiro.settings` (a different protocol level from `session/new`). Both may need updating.
   - KAS settings registry (24 keys, all `"off"` default except `semanticReview = "on"`): `workflows`, `thinking`, `knowledge`, `codeIntelligence`, `checkpoint`, `tangentMode`, `_subagent`, `_delegate`, `largeToolOutputHandler`, etc.
   - TUI `w1()` mapping (cli.json key → KAS settings key):
     - `chat.enableThinking` → `thinking` (default: `true`)
     - `chat.enableKnowledge` → `knowledge` (default: `true`)
     - `chat.enableCodeIntelligence` → `codeIntelligence` (default: `true`)
     - `chat.enableLargeToolOutputHandler` → `largeToolOutputHandler` (default: `true`)
     - `chat.enableCheckpoint` → `checkpoint` (no default)
     - `chat.enableTangentMode` → `tangentMode` (no default)
     - `chat.enableSubagent` → `_subagent` (no default)
     - `chat.enableDelegate` → `_delegate` (no default)
     - `chat.disableAutoCompaction` → `disableAutoCompaction` (no default)
     - `chat.enableWorkflows` → `workflows` + `goal` (both same value)
     - `chat.enableC2s` → `c2s` (feature-gated)
   - `workflowNotifications` is ignored by KAS (0 occurrences in `acp-server.js`). Include for forward-compat but not load-bearing.
   - Workflow notification family (10 methods): `run_start`, `node_start`, `node_complete`, `node_paused`, `loop_iteration`, `watch_poll`, `paused`, `run_complete`, `steps_queued`, `recipes_changed`. `node_start` payload: `{ sessionId, nodeId, nodePath, branchId, iteration, agentName }`.
   - Compaction bug (`_registry.broadcast` instead of `_emit`) is independent — separate plan.
   - `rootConversationId` used only as boolean filter in `_is_subagent_session`; no inverse parent→children lookup exists anywhere.
   - Liveness pipeline: `classify_kiro_v3` in `status_classifier.py` has `sub_agent_start/complete` handlers but no workflow-specific payload types; `_publish_live()` publishes only `frozenset(sessions)`.
   - `invoke_sub_agent` is suppressed by KAS when `workflows.enabled = true`. PA's existing crew panel for the old `_kiro.dev/subagent/list_update` shape (`_on_subagent_list`) is unaffected for TUI sessions PA observes but didn't create.

6. **Risks and mitigations**:
   - Enabling workflows without the notification handler → silent discard. Mitigation: Phase 1 and Phase 2 gate together.
   - Settings we enable may produce new notification types PA doesn't handle. Mitigation: settings audit in Phase 1 classifies each setting before enabling.
   - Workflow liveness Part A (ACP-based) depends on Phase 2's `_kiro/workflow/*` handlers. Part B (disk-based) is independent.

7. **Prior art**: No prior workflow or settings-alignment work in `plans/done/`. `plans/ROADMAP.md` has the four items this plan implements under Session Control & Integration.

8. **Open items**:
   - Whether `session/close` now has a wire effect (2.28.0) — separate plan probe.
   - Exact `session/prompt` return timing when a workflow is active (does parent turn end before children complete, or stay open?). Affects whether ACP liveness gap is acute. Probe during Phase 3.
   - Whether `toolSearch` needs to also appear in `session/new` `_meta.kiro.settings` (currently only in `clientCapabilities`). Verify during Phase 1 implementation.
   - Which settings among `thinking`, `knowledge`, `codeIntelligence`, `_subagent`, `_delegate` produce new notification types vs are tool-availability-only. Audit in Phase 1.

---

## Resolved Decisions

- Q1: Enable workflows always (align with kiro-cli — the goal implies the answer). — A: Always-on. — Decision: `workflows: {enabled: true}` whenever `chat.enableWorkflows` is true in cli.json; no PA opt-in checkbox.
- Q2: `/compact` fix independent? — A: Yes, separate plan. — Decision: `/compact` broadcast-to-emit fix goes to `plans/261008_ACP_COMPACT_FIX.md`.
- Q3: Dynamic sync mechanism. — A: a+c — read from kiro-cli's `cli.json` at `session/new` time, no PA-specific settings. — Decision: extend `_build_kas_session_params` to read `cli.json` and construct `_meta.kiro.settings` dynamically.
- Q4: Settings scope. — A: Per-setting audit first; only enable settings with full PA productized support. — Decision: Phase 1 audits each setting; settings that only affect tool availability (no new notification types, no new UI states) enable immediately; others deferred with ROADMAP entries.
- Coupling: Items 2+5 ship together. — Decision: Phase 1 (settings) and Phase 2 (workflow notifications) are a single gate. Phase 1 alone cannot ship.

---

## Assumptions (unconfirmed)

- Settings with only tool-availability effects (no new notification types): `thinking`, `knowledge`, `codeIntelligence`, `largeToolOutputHandler`. These will be confirmed during Phase 1 audit before enabling.
- KAS reads most `chat.enable*` settings from the client's `session/new` body, not from `cli.json` directly. This is the reason the TUI forwards them explicitly via `w1()`.
- `workflowNotifications.delivery: "steer"` should be set alongside `workflows: true` to ensure the parent session stays open to steer input during workflow execution (KAS ignores it today, but may use it in future builds).

---

## Plan

### Phase 1 — Settings audit and dynamic `_meta.kiro.settings` [Standard]

**File scope**: `src/power_atlas/acp.py`, `docs/KNOWLEDGE.md`.

**Goal**: Implement a `_build_session_settings()` helper that reads `cli.json` and constructs `_meta.kiro.settings` dynamically. Audit each of the 10 settings in the TUI's `w1()` mapping to determine whether enabling it produces new notification types that PA doesn't handle. Enable only fully-supported settings; add ROADMAP entries for the rest.

**Steps**:
1. Read the KAS bundle (`acp-server.js`) and TUI bundle (`tui.js`) to determine what notification types each setting gates. For each setting in the mapping, answer: does enabling it produce notification types beyond what PA already handles in `_on_notification`? Settings that are purely tool-availability (model gets new tools, PA sees normal `tool_call` frames) are safe to enable; settings that produce new notification methods or new `session_info_update` kinds need investigation.
2. Implement `_build_session_settings(cli_settings: dict) -> dict` in `acp.py`:
   - For each `[cli_key, kas_key]` pair confirmed safe: read `cli_settings[cli_key]`, if boolean forward as `{kas_key: {"enabled": value}}`.
   - Apply defaults for `codeIntelligence`, `knowledge`, `thinking`, `largeToolOutputHandler` (TUI defaults these to `true` if absent).
   - Always include `workflows: {"enabled": bool}` and `goal: {"enabled": bool}` from `chat.enableWorkflows`.
   - Include `workflowNotifications: {"enabled": bool, "delivery": "steer"}` for forward-compat.
3. Call `_build_session_settings()` from `_build_kas_session_params()` and include the result under `_meta.kiro.settings`.
4. Also verify whether `session/new` should mirror the `toolSearch` settings that currently go in `clientCapabilities` only. If yes, add `toolSearch` to the settings block.
5. Document the full mapping (cli.json key → KAS key → shipped default → PA behavior) in `docs/KNOWLEDGE.md`.
6. For each setting not enabled: add a ROADMAP entry under Session Control & Integration naming what PA needs to build before it can enable that setting.

**Done when**: `session/new` from PA includes `_meta.kiro.settings` with all audited-safe settings; a TUI session and a PA session starting from the same `cli.json` receive the same tool inventory for all enabled settings; `run_workflow` appears in PA session's tool list (verify via `available_commands_update` notification).

**[QA]**

---

### Phase 2 — Workflow notification handler and crew panel [Standard]

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/templates/acp.html`, `src/power_atlas/static/composer-chrome.js`, `src/power_atlas/static/style.css`.

**Goal**: Handle the `_kiro/workflow/*` notification family in `_on_notification`. Populate `crews` and `subagent_sessions` from `node_start`. Display workflow step entries in the crew panel.

**Note**: This phase **gates Phase 1** — neither ships alone. Once Phase 1 enables workflows, the notification handler must be in place to process what arrives.

**Steps**:
1. Add `_on_workflow_notification(method: str, params: dict)` to `_Supervisor`:
   - On `_kiro/workflow/run_start`: extract `workflowId`, `parentSessionId`. Initialize `self._workflow_children.setdefault(parentSessionId, set())`. Store `workflowId → parentSessionId` for attribution.
   - On `_kiro/workflow/node_start`: extract `parentSessionId`, `sessionId`, `nodeId`, `nodePath`, `agentName`, `branchId`, `iteration`. Register `self.subagent_sessions[sessionId] = {"parent": parentSessionId}`. Create crew entry in `self.crews[parentSessionId][sessionId]`. Add `sessionId` to `self._workflow_children[parentSessionId]`. Call `_emit_subagents_frame`.
   - On `_kiro/workflow/node_complete`: mark crew entry done; remove `sessionId` from `_workflow_children[parentSessionId]`. Call `_emit_subagents_frame`.
   - On `_kiro/workflow/run_complete`: mark all remaining children done; sweep `_workflow_children[parentSessionId]`. Call `_emit_subagents_frame`.
   - On `_kiro/workflow/node_paused` / `_kiro/workflow/paused`: emit a workflow pause status frame if a UI exists; otherwise log and no-op.
   - On remaining methods (`loop_iteration`, `watch_poll`, `steps_queued`, `recipes_changed`, `run_start`): log at DEBUG and no-op (stub with comment).
2. Add `_workflow_children: dict[str, set[str]]` to `_Supervisor.__init__`. Add clearing in `close_session` and `_detach`.
3. Add `workflowId` tracking to correlate `node_start` events arriving before `run_start` (handle out-of-order delivery).
4. Crew panel entries for workflow nodes use the same `subagents` frame type. Ensure the crew panel renders `agentName` as the label and `nodePath` as the role (or similar mapping that makes workflow steps identifiable in the UI). Update `acp.html` and `composer-chrome.js` if the existing crew card shape needs extension.
5. On reconnect (`_handle_subscribe`): include active workflow crew entries in the crew snapshot, same as existing v2/v3 crew entries.
6. Decode `workflow-progress` JSON embedded in `user_message_chunk` frames (`_meta.kiro.kind == "workflow-progress"`): basic decode to update workflow state on session replay/reload. This gives minimal status display after a page reload without requiring child session ACP subscriptions.

**Done when**: A PA session that uses `run_workflow` shows running workflow steps in the crew panel with live status; step entries appear on reconnect; the parent session's crew panel is populated correctly for parallel branches.

**[QA]**

---

### Phase 3 — Workflow liveness [Standard]

**File scope**: `src/power_atlas/data_kiro_v3.py`, `src/power_atlas/presence.py` (or `src/power_atlas/web.py`).

**Goal**: Sessions running a workflow show as Working/Waiting rather than idle.

**Part A — ACP-held sessions**:
Factor `_workflow_children[session_id]` (populated by Phase 2) into `_publish_live()`. A parent session with non-empty `_workflow_children` should publish as live (Working) even after its own turn ends. Clear when `_workflow_children[session_id]` becomes empty.

**Part B — disk-based / terminal sessions** (independent of Phase 2):
1. Probe: read the parent session's `messages.jsonl` during an active workflow to determine what payload types KAS writes while children are running. If the parent's file is updated, `classify_kiro_v3` may already return Working correctly. If not, the 300-second `_LIVE_MTIME_WINDOW` in `web.py` will expire and the session appears not-live.
2. Add an inverse lookup to `data_kiro_v3.py`: given a parent session id and its hash directory, scan sibling `sess_*/session.json` files for `rootConversationId == parent_id` and `status in {"in_progress", "waiting_on_user"}`. Return any active child ids.
3. In the liveness pipeline (web.py `_session_is_live` or `_acp_status_for_held`), if the parent appears idle but has active children, propagate the highest-priority child status as the parent's effective status.
4. Bound the scan to the parent's hash directory (all children are in the same hash dir per `memory/topics/kiro-cli.md`).

**Done when**: A PA session (ACP-held or terminal) that launched a workflow shows Working in the session rail and Overview tiles while any child step is active; reverts to the parent's own status once all children complete.

**[QA]**

---

## Progress Tracker

- [ ] Phase 1 — Settings audit and dynamic `_meta.kiro.settings`
- [ ] Phase 2 — Workflow notification handler and crew panel
- [ ] Phase 3 — Workflow liveness

## Documentation Updates

| File | Required update |
|---|---|
| `docs/KNOWLEDGE.md` | Full cli.json → KAS settings mapping table; which settings PA enables and which are deferred |
| `plans/ROADMAP.md` | Replace "Full workflow support" and "Align PA ACP session settings with kiro-cli" items with links to this plan; add entries for deferred settings |
| `AGENTS.md` | Update ACP UI description to note workflow monitor panel once Phase 2 ships |

## Harness Improvement Opportunities

## Review Log
