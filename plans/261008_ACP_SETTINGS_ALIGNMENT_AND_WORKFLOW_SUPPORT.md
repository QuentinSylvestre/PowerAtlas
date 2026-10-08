# ACP Settings Alignment and Workflow Support

> **Date**: 2026-10-08
> **Status**: In Progress
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Dynamic kiro-cli settings sync + full workflow support (Layers 1–3) + workflow liveness
> **Estimated effort**: ~1 week (3 phases, ~2–3 days each)

---

## Intent

### Problem statement & desired outcomes

PowerAtlas ACP sessions are missing a large part of the kiro-cli capability surface because `_build_kas_session_params` (`acp.py:959`) never sends a `_meta.kiro.settings` block in `session/new`. KAS ships all feature flags as `"off"` by default and only activates them when the client explicitly enables them. The most acute consequence: PA sessions never receive the `run_workflow` tool, so the model falls back to `invoke_sub_agent` for fan-outs. TUI sessions with `chat.enableWorkflows: true` in `cli.json` dispatch multi-step parallel workflows with a dedicated workflow monitor; PA sessions produce old-style subagent fan-outs. The `_kiro/workflow/*` notification family (10 methods) has zero handlers in `acp.py` — all workflow frames are logged at INFO and discarded.

The desired outcome is PA sessions that behave identically to an equivalently-configured TUI session: dynamic settings forwarded from the user's `cli.json`, `run_workflow` available, running workflow steps visible in the crew panel, and the parent session's liveness reflecting its running children.

### Success criteria

- SC-1: A PA session created with `chat.enableWorkflows: true` in `cli.json` has `run_workflow`, `inspect_workflow`, `update_workflow`, and `send_message` in its tool inventory (verified via `available_commands_update` notification).
- SC-2: PA's `_meta.kiro.settings` block is built dynamically from `cli.json` — no hardcoded values; a `cli.json` with `chat.enableWorkflows: false` produces a settings block with `workflows: {enabled: false}`, not the KAS shipped default.
- SC-3: Settings in the "enable now" category (`thinking`, `knowledge`, `codeIntelligence`, `largeToolOutputHandler`) are forwarded with their `cli.json` values (or the TUI's defaults when the key is absent).
- SC-4: A PA session running a workflow via `run_workflow` shows running step entries in the crew panel with step name, status (running/done/failed), and elapsed time.
- SC-5: A PA session whose workflow is active shows Working status in the rail and Overview tiles while any child step is `in_progress` or `waiting_on_user`.
- SC-6: After all workflow steps complete, the parent session reverts to its own status.

### Scope boundaries & non-goals

- **In scope**: Dynamic `_meta.kiro.settings` forwarding (Phases 1); workflow notification handler + crew panel display (Phase 2, gates Phase 1); workflow liveness Part A (ACP-held, Phase 3) and Part B (disk-based, Phase 3).
- **Out of scope**: `/compact` fix (shipped separately, commit `7e28335`); session cap increase; clean close; Layer 4 (per-step child ACP subscriptions); deferred settings (`tangentMode`, `checkpoint`, `c2s`, `memory`, `disableAutoCompaction`) — each has a ROADMAP entry.
- **Source**: `/qexplore` 2026-10-08. Sub-agent reports: `.agents/tasks/explore-directed.md`, `.agents/tasks/explore-subsystem.md`, `.agents/tasks/explore-mutation-finder.md`.

---

## Exploration Discovery

1. **Files in scope**: `src/power_atlas/acp.py` (primary, ~7000+ lines), `src/power_atlas/data_kiro_v3.py`, `src/power_atlas/presence.py`, `src/power_atlas/web.py`, `src/power_atlas/templates/acp.html`, `src/power_atlas/static/composer-chrome.js`, `src/power_atlas/static/style.css`, `docs/KNOWLEDGE.md`, `plans/ROADMAP.md`.

2. **What was read**: Three sub-agent passes (directed, subsystem, mutation-finder). Live probes: KAS settings registry (all 24 `kp()` definitions from `acp-server.js` 2.28.0), TUI `w1()` function (full session/new settings builder), `cli.json` current state, TUI constants mapping. All 24 KAS setting keys have default `"off"` except `semanticReview = "on"`.

3. **Key constraints**:
   - Items "enable workflows" (Phase 1) and "workflow notification handler" (Phase 2) **must ship together**. Enabling `workflows: {enabled: true}` without `_kiro/workflow/*` handlers causes workflow notifications to be silently discarded. Both touch `acp.py` so no `[P:N]` annotation.
   - Only forward settings with full PA productized support. Each enabled setting must have known notification types and existing PA handler coverage.

4. **Step 1.5**: Code-tracing trio dispatched (brownfield). All three completed.

5. **Existing patterns**:
   - `_build_kas_session_params` (`acp.py:959`): currently emits only `modeId` and `steering`. No `settings` block.
   - `_kiro_client_capabilities` (`acp.py:887`): sends `toolSearch` in `clientCapabilities._meta.kiro.settings` — a distinct protocol level from `session/new`.
   - TUI `w1()` mapping (cli.json key → KAS setting key, confirmed):
     - `chat.enableThinking` → `thinking` (default: `true` if absent)
     - `chat.enableKnowledge` → `knowledge` (default: `true` if absent)
     - `chat.enableCodeIntelligence` → `codeIntelligence` (default: `true` if absent)
     - `chat.enableLargeToolOutputHandler` → `largeToolOutputHandler` (default: `true` if absent)
     - `chat.enableCheckpoint` → `checkpoint` (deferred — needs investigation)
     - `chat.enableTangentMode` → `tangentMode` (deferred — needs investigation)
     - `chat.enableSubagent` → `_subagent` (deferred — subagent + workflow suppression interaction unclear)
     - `chat.enableDelegate` → `_delegate` (deferred — same concern)
     - `chat.disableAutoCompaction` → `disableAutoCompaction` (deferred — decision needed)
     - `chat.enableWorkflows` → `workflows` + `goal` (both same value) — the primary enable
     - `chat.enableC2s` → `c2s` (deferred — unknown feature)
   - Workflow notification family (10 methods, all unhandled in `_on_notification`): `run_start`, `node_start`, `node_complete`, `node_paused`, `loop_iteration`, `watch_poll`, `paused`, `run_complete`, `steps_queued`, `recipes_changed`.
   - `node_start` payload shape (from `tui.js:341847`): `{ sessionId, nodeId, nodePath, branchId, iteration, agentName }`.
   - `rootConversationId` used only as boolean filter in `_is_subagent_session` (`data_kiro_v3.py:82`); no inverse parent→children lookup exists.
   - `_publish_live()` publishes `frozenset(self.sessions)` only — workflow children never in that set.

6. **Risks and mitigations**: See Section 6.

7. **Prior art**: No prior workflow or settings-alignment work in `plans/done/`. Compact fix shipped separately at `7e28335`.

8. **Resolved open items**:
   - Settings taxonomy: resolved by probe — full 24-key registry from `kp()` definitions in KAS 2.28.0 bundle.
   - `invoke_sub_agent` suppression: confirmed by `suppressChatDelegationTool` in `acp-server.js` — suppressed when `workflowsEnabled && !insideWorkflowStep && !subagentOrchestrationActive`.
   - Open items deferred to implementation: `session/close` wire effect on 2.28.0 (not blocking), `session/prompt` return timing during workflow execution (risk), `workflow-progress` chunk sufficiency for replay (Phase 2 decision).

---

## 1) Current State

**`_build_kas_session_params` (`acp.py:959–981`)** — the sole function that builds the `session/new` and `session/load` request bodies. Current output:

```python
{
    "_meta": {
        "kiro": {
            "modeId": mode_id,          # e.g. "kiro_default"
            "steering": [{...}],         # _OVERLAY_STEERING
        }
    }
}
```

No `settings` key exists. KAS applies shipped defaults for all 24 feature settings (all `"off"` except `semanticReview = "on"`).

**`_on_notification` (`acp.py:4703–5071`)** — the notification dispatcher. Handles: `METADATA_METHOD`, `COMPACTION_STATUS_METHOD`, `SUBAGENT_LIST_METHOD`, several `kind` values, `_kiro/mcp/status`, `_kiro.dev/clear/status`. Fallthrough: `log.info(...)`. No `_kiro/workflow/*` branch exists. Confirmed zero grep matches for `_kiro/workflow` in `acp.py`.

**`_publish_live()` (`acp.py:5932`)** — publishes `frozenset(self.sessions)` to `presence.py`. Workflow child sessions are never in `self.sessions`. `classify_kiro_v3` in `status_classifier.py` has `sub_agent_start/complete` handlers but no workflow payload types.

**`data_kiro_v3._is_subagent_session` (`data_kiro_v3.py:82`)** — reads `rootConversationId` as a boolean filter only. No code returns a parent's children by ID.

**Existing crew panel wiring** — `_on_subagent_list` (`acp.py:4190`) populates `crews[parent_id]` and `subagent_sessions[child_id]`. `_emit_subagents_frame` (`acp.py:6078`) broadcasts the `subagents` frame. On reconnect, `_handle_subscribe` (`acp.py:6797`) resends active crew from the `crews` dict. This path is unaffected by this plan.

## 2) Goal

Implement a `_build_session_settings()` helper that reads `cli.json` at `session/new` time and constructs `_meta.kiro.settings` dynamically; handle the `_kiro/workflow/*` notification family to populate the crew panel with running workflow steps; propagate child session liveness to the parent session's status.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Settings source | Read `cli.json` dynamically at `session/new` time | Hardcode specific keys; PA-specific settings UI | User decided a+c: sync with kiro-cli's own config, no PA-specific overrides. The user's `/settings` choices propagate automatically. |
| Settings enable scope | Only settings that are tool-availability-only (no new notification types, no new UI states) | Forward all 24 keys; forward only `workflows` | User constraint: "anything we enable in PA must have full productized support." Phase 1 settings audit confirms which keys qualify; others get ROADMAP entries. Initial "enable" list: `thinking`, `knowledge`, `codeIntelligence`, `largeToolOutputHandler`, `workflows`, `goal`. Deferred: `checkpoint`, `tangentMode`, `_subagent`, `_delegate`, `c2s`, `disableAutoCompaction`. |
| `invoke_sub_agent` suppression | Accept — enabling `workflows` suppresses `invoke_sub_agent` for PA sessions | Disable workflow suppression via a KAS flag (no such flag exists) | KAS hard-gates this via `suppressChatDelegationTool`. The old crew panel path (`_on_subagent_list`) still works for TUI sessions PA observes but did not create. PA-created sessions use `run_workflow` exclusively once Phase 2 ships. |
| Phase 1 + Phase 2 coupling | Both phases must ship in the same deployment | Ship Phase 1 first, accept silent discard during Phase 2 gap | Enabling `workflows` without the handler causes all workflow notifications to be silently discarded (logged at INFO). PA's existing session behavior becomes incorrect rather than just limited. |
| `workflowNotifications` field | Include `workflowNotifications: {enabled: bool, delivery: "steer"}` alongside `workflows` | Omit (KAS ignores it) | KAS ignores it today (0 occurrences in `acp-server.js`), but it is part of the TUI's canonical session/new body. Include for forward-compat, set `delivery: "steer"` to match TUI default. |
| `toolSearch` in session/new | Verify if `toolSearch` should also appear in `_meta.kiro.settings` for session/new (currently only in `clientCapabilities`) | Move from `clientCapabilities` to session/new | Open verification item for Phase 1 — check if KAS reads `toolSearch` from both locations or only `clientCapabilities`. No change until verified. |
| Workflow liveness Part B | Implement independent of Phase 2 | Wait for Phase 2's `_workflow_children` dict | Part B (disk-based `rootConversationId` scan) can ship before Part A (ACP-held sessions). Provides liveness for terminal sessions running workflows. |
| Notification fan-out for workflow steps | Reuse existing `subagents` frame type (both use `sessionId` as key) | New `workflow_step` frame type | Workflow step entries use real `sessionId` values like v2 subagents. The crew panel's `handleSub` path already routes by `sessionId`. No new frame type needed for Phase 2. |

## 4) External Dependencies & Costs

### Required external changes

| Category | Change needed | Owner | Status |
|---|---|---|---|
| CI/CD | None | — | N/A |
| IAM / Permissions | None | — | N/A |
| Cloud resources | None | — | N/A |
| Data migration / backfill | None | — | N/A |
| Rollout / cutover | PA restart required to pick up new settings forwarding (Python process, compiled bytecode) | User | On next PA restart |
| Secrets / Env vars | None | — | N/A |

### Cost impact

None. All changes are in PowerAtlas's own Python/JS codebase. No cloud resources, APIs, or third-party services.

## 5) Implementation Phases

### Phase 1: Dynamic `_meta.kiro.settings` [QA]

**Goal**: Implement `_build_session_settings()` that reads `cli.json` and constructs the session/new settings block, audits each setting for full PA support, and forwards the "enable now" set dynamically.

**Covers**: SC-1, SC-2, SC-3

**File scope**: `src/power_atlas/acp.py`, `docs/KNOWLEDGE.md`

**Step 1: Settings audit** (do this before writing code)

For each `cli.json` key in the deferred set (`checkpoint`, `tangentMode`, `_subagent`, `_delegate`, `c2s`, `disableAutoCompaction`), grep `acp.py` for any existing handler for the notification types that setting might enable. Report:
- Which settings are tool-availability-only (safe to add to the "enable now" list)
- Which produce new notification types PA has no handler for (stay deferred)
- Update the "enable now" / "deferred" split accordingly before writing `_build_session_settings()`

The "enable now" starting set is confirmed: `thinking`, `knowledge`, `codeIntelligence`, `largeToolOutputHandler`, `workflows`, `goal`. The audit may expand this.

**Step 2: Implement `_build_session_settings()`**

Add a module-level helper immediately before `_build_kas_session_params` (`acp.py:959`):

```python
def _build_session_settings(cli_settings: dict) -> dict:
    """Construct *_meta.kiro.settings* for session/new from the user's cli.json.

    Mirrors the TUI's ``w1()`` function, but only forwards settings for which
    PA has full productized support. Deferred settings (tangentMode, checkpoint,
    c2s, _subagent, _delegate, disableAutoCompaction) are excluded until PA
    builds their corresponding UI/UX support.

    The TUI defaults codeIntelligence, knowledge, thinking, and
    largeToolOutputHandler to ``true`` when the key is absent from cli.json;
    we apply the same defaults to avoid silently downgrading sessions.
    """
    _DEFAULTS: dict[str, bool] = {
        "codeIntelligence": True,
        "knowledge": True,
        "thinking": True,
        "largeToolOutputHandler": True,
    }
    # cli.json key → KAS setting name, "enable now" set only
    _MAPPING: list[tuple[str, str]] = [
        ("chat.enableThinking",              "thinking"),
        ("chat.enableKnowledge",             "knowledge"),
        ("chat.enableCodeIntelligence",      "codeIntelligence"),
        ("chat.enableLargeToolOutputHandler","largeToolOutputHandler"),
        # NOTE: any setting moved from deferred to enable-now by Phase 1
        # audit is added here; see plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md
    ]
    settings: dict[str, dict] = {}

    # Forward confirmed-safe settings from cli.json, applying TUI defaults
    for cli_key, kas_key in _MAPPING:
        value = cli_settings.get(cli_key)
        if isinstance(value, bool):
            settings[kas_key] = {"enabled": value}
        elif kas_key in _DEFAULTS:
            settings[kas_key] = {"enabled": _DEFAULTS[kas_key]}

    # Workflow + goal: both controlled by chat.enableWorkflows
    workflows_on = bool(cli_settings.get("chat.enableWorkflows", False))
    settings["workflows"] = {"enabled": workflows_on}
    settings["goal"] = {"enabled": workflows_on}

    # workflowNotifications: KAS ignores this today but TUI always sends it.
    # delivery:"steer" keeps the parent session open to user input while
    # workflow steps run (the kiro-cli default for an absent field is unknown).
    settings["workflowNotifications"] = {
        "enabled": workflows_on,
        "delivery": "steer",
    }

    return settings
```

**Step 3: Wire into `_build_kas_session_params`**

Modify `_build_kas_session_params` (`acp.py:959–981`) to call `_build_session_settings()`.

**First**, create `_load_cli_settings_dict()` in `acp.py` (no such helper exists yet — `data_kiro_v3.py` has no cli.json reader). Model it on `_kiro_tool_search_settings` (`acp.py:854`) which already reads `KIRO_CLI_SETTINGS_PATH`:

```python
def _load_cli_settings_dict() -> dict:
    """Return the full cli.json as a dict, or {} on read/parse failure."""
    try:
        return json.loads(KIRO_CLI_SETTINGS_PATH.read_bytes())
    except (OSError, ValueError):
        return {}
```

Then modify `_build_kas_session_params` to call it:

```python
def _build_kas_session_params(mode_id: str = "kiro_default") -> dict[str, Any]:
    cli_settings = _load_cli_settings_dict()
    return {
        "_meta": {
            "kiro": {
                "modeId": mode_id,
                "steering": [{**d} for d in _OVERLAY_STEERING],
                "settings": _build_session_settings(cli_settings),
            }
        },
    }
```

> **Rejected**: `from .data_kiro_v3 import _load_cli_json_settings` — this function does not exist; `data_kiro_v3.py` has no cli.json reader. **Use instead**: inline `_load_cli_settings_dict()` defined directly in `acp.py`.

**Step 4: Verify `toolSearch` placement and `session/load` settings behavior**

Two probes to run before shipping Phase 1:

**Probe A**: Does `session/new._meta.kiro.settings` also need `toolSearch`? The TUI's `w1()` appends `toolSearch` to the settings block. Confirm whether KAS reads `toolSearch` from `session/new` as well as from `clientCapabilities` (or only one). If both, add `toolSearch` to `_build_session_settings()`; if `clientCapabilities` is sufficient, no change.

**Probe B**: After Phase 1, `session/load` also calls `_build_kas_session_params` (`acp.py:5861`), so existing sessions resumed via `session/load` will receive `workflows.enabled: true` in their settings block. Probe whether KAS applies the settings block on `session/load` or ignores it in favor of the session's persisted configuration. Send a `session/load` request with `chat.enableWorkflows: true` for a session originally created without workflows; observe `available_commands_update` to see if `run_workflow` appears. If KAS applies it (likely — this is probably intended behavior), document the result. If KAS ignores it, note that existing sessions are unaffected.

Record probe results in `docs/KNOWLEDGE.md` under the "ACP session/new settings" section.

**Step 5: Update `docs/KNOWLEDGE.md`**

Add a section "ACP session/new settings" documenting:
- The full confirmed mapping table (cli.json key → KAS key → shipped default → PA behavior)
- Which settings are enabled and which are deferred (with reasons)
- Where `_build_session_settings()` lives

**Exit criteria**:
- [ ] `_build_session_settings()` exists and is covered by a unit test that asserts the correct settings dict for a sample `cli.json` (with `chat.enableWorkflows: true` and with `false`)
- [ ] `_load_cli_settings_dict()` exists in `acp.py`; returns `{}` on missing/malformed cli.json
- [ ] `_build_kas_session_params()` includes `"settings"` under `_meta.kiro`
- [ ] A live PA session with `chat.enableWorkflows: true` receives `run_workflow` in its `available_commands_update` notification
- [ ] A live PA session with `chat.enableWorkflows: false` does NOT receive `run_workflow`
- [ ] `docs/KNOWLEDGE.md` has the settings mapping table with probe results (toolSearch placement, `session/load` behavior from Step 4 probes)
- [ ] `test_web.py` or a new module covers `_build_session_settings()` for ≥2 `cli.json` shapes

---

### Phase 2: Workflow notification handler and crew panel [QA]

**Goal**: Handle `_kiro/workflow/*` notifications in `_on_notification`, populate `crews` and `subagent_sessions` from `node_start`/`run_complete`, display workflow step entries in the crew panel. **This phase must ship together with Phase 1.**

**Covers**: SC-4

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/templates/acp.html`, `src/power_atlas/static/composer-chrome.js`

**Step 1: Add `_workflow_children` dict to `_Supervisor.__init__`**

In `_Supervisor.__init__` (`acp.py:3239`), add:

```python
# workflow child sessions: parent_id → set of active child session IDs.
# Populated by _kiro/workflow/node_start; cleared on node_complete/run_complete
# and in close_session/_detach. Used for liveness tracking (Phase 3).
self._workflow_children: dict[str, set[str]] = {}
```

**Step 2: Implement `_on_workflow_notification()`**

**Mandatory pre-implementation probe (do this before writing Step 3's sub-handlers)**: Capture a live `_kiro/workflow/node_start` notification from `orchestrator.log` during a TUI workflow run to confirm which field in `params` carries the parent session ID. The documented payload shape (`{ sessionId, nodeId, nodePath, branchId, iteration, agentName }`) contains no `parentSessionId` field. If the parent ID lives in the outer `params.get("sessionId")` (standard ACP convention, consistent with every other notification type), the sub-handlers must extract it as `session_id` (already available in `_on_notification`'s own scope at line 4707, not from a nested payload field). Failing to run this probe before Step 3 risks a handler that silently no-ops on every notification.

Add a new method to `_Supervisor` near `_on_subagent_list` (`acp.py:4190`):

```python
def _on_workflow_notification(self, method: str, session_id: str, params: dict) -> None:
    """Route one *_kiro/workflow/** frame to the appropriate handler.

    ``session_id`` is the PARENT session ID extracted by ``_on_notification``
    at line 4707 — it is NOT re-extracted from params here.
    """
    if method == "_kiro/workflow/node_start":
        self._on_workflow_node_start(session_id, params)
    elif method == "_kiro/workflow/node_complete":
        self._on_workflow_node_done(session_id, params, terminal_status="done")
    elif method == "_kiro/workflow/node_paused":
        # node_paused = step is waiting for user input (send_message(severity='warning'))
        # NOT a failure — keep the node active in _workflow_children
        self._on_workflow_node_done(session_id, params, terminal_status="waiting")
    elif method in ("_kiro/workflow/run_complete",):
        self._on_workflow_run_done(session_id, params)
    elif method == "_kiro/workflow/paused":
        # run-level pause (whole workflow waiting for input) — do NOT clear children
        log.debug("ACP workflow run paused: session=%s", session_id)
    else:
        # stubs: run_start, loop_iteration, watch_poll, steps_queued, recipes_changed
        log.debug("ACP workflow notification %s: no handler (stub)", method)
```

> **Rejected**: mapping `node_paused` to `failed=True` — `node_paused` signals a step waiting for user input, not a failure. **Use instead**: `terminal_status="waiting"` with `done=False` to keep the step active. Similarly, `_kiro/workflow/paused` (run-level) must NOT route to `_on_workflow_run_done` — the run is suspended, not complete. Silently clearing all children would hide the waiting state from the user.

**Step 3: Implement the three sub-handlers**

`_on_workflow_node_start`:
- `parentSessionId` = the `session_id` argument (from `_on_notification`'s outer scope — confirmed by probe from Step 2)
- Extract `childSessionId` from params (field name confirmed by probe — tentatively `params.get("sessionId")` within the payload, but verify)
- Guard both: `if not isinstance(parentSessionId, str) or not isinstance(childSessionId, str): log.warning(...); return` (follows `_on_subagent_list`'s defensive `_as_text` pattern)
- If `parentSessionId not in self.sessions`: log warning, return
- Register `self.subagent_sessions[childSessionId] = {"parent": parentSessionId}`
- Initialize `self.subagent_history[childSessionId] = _History()`
- Call `self._evict_finished_subagents(parentSessionId)` (follows `_on_agent_subtask_open` pattern, prevents unbounded accumulation)
- Create crew entry (all required fields — omitting any causes `KeyError` in `_subagents_payload`):

```python
crew = self.crews.setdefault(parentSessionId, {})
crew[childSessionId] = {
    "sessionId":   childSessionId,
    "sessionName": "",                           # filled from agentName if present
    "role":        nodePath or nodeId,
    "task":        (agentName or nodeId)[:30],   # _subagents_payload truncates at 30
    "status":      "working",
    "done":        False,
    "error":       "",                           # REQUIRED: _subagents_payload["error"] is bare []
    "order":       len(crew),                    # REQUIRED: _subagents_payload sort key is bare []
    "action":      "running",
    "fan_out_id":  _NO_ANCHOR_TOOLCALLID,
    "startedAt":   time.time(),
    "stoppedAt":   None,
}
```

- Update `self._workflow_children.setdefault(parentSessionId, set()).add(childSessionId)`
- Call `_emit_subagents_frame(parentSessionId, self.crews, self._active_fan_out_wave)` — use `_active_fan_out_wave`, not `crew_spawn_toolcallids` (per established v3 fan-out convention, `acp.py:6094`)

> **Rejected**: passing `self.crew_spawn_toolcallids` as third arg to `_emit_subagents_frame` — all active v3 fan-out call sites use `self._active_fan_out_wave` per the function's own docstring. **Use instead**: `self._active_fan_out_wave`.
>
> **Rejected**: omitting `"order"`, `"error"` from the crew entry — `_subagents_payload` (`acp.py:6063`) uses `entry["order"]` as the sort key and `entry["error"]` as a payload field, both bare `[]` accesses. A `KeyError` fires the first time any workflow session emits a crew frame. **Use instead**: include both fields as shown above.

`_on_workflow_node_done(session_id, params, terminal_status)`:
- Extract `childSessionId` from params
- If crew entry exists: set `entry["done"] = (terminal_status != "waiting")`, `entry["status"] = terminal_status`, `entry["stoppedAt"] = time.time()`
- If `terminal_status in ("done", "failed")`: remove from `self._workflow_children.get(parentSessionId, set())`
- If `terminal_status == "waiting"`: **leave in `_workflow_children`** — the step is paused, not complete; liveness must continue reporting Working/Waiting
- Set `entry["error"]` only for `terminal_status == "failed"` (non-empty string)
- Call `_emit_subagents_frame(parentSessionId, self.crews, self._active_fan_out_wave)`

`_on_workflow_run_done(session_id, params)`:
- For each active child in `self._workflow_children.pop(parentSessionId, set())`: mark crew entry `done=True`, `status="done"`, `stoppedAt=time.time()`
- Call `_emit_subagents_frame`

**Step 4: Route in `_on_notification`**

Add the workflow routing block **BEFORE the SC-1 early-frame buffer check** at `_on_notification` (`acp.py:4718`). Workflow `node_start` notifications carry `params.sessionId` = the child step's session ID (never in `self.sessions`), so the SC-1 check (`session_id not in self.sessions AND _reserved > 0`) would buffer and orphan the notification under the child's ID. The SC-1 replay path only replays frames for the newly-created parent session, not for child IDs.

```python
# Route workflow notifications before SC-1: their sessionId is the child
# step's ID (never in self.sessions), so SC-1 would buffer them incorrectly.
# session_id here is the PARENT's ID (extracted from params.sessionId by
# the notification envelope — confirmed by probe in Step 2).
if isinstance(session_id, str) and method.startswith("_kiro/workflow/"):
    self._on_workflow_notification(method, session_id, params)
    return

# SC-1: buffer notifications for sessions not yet registered...
if (isinstance(session_id, str) and session_id not in self.sessions
        and self._reserved > 0):
    ...
```

> **Rejected**: placing the routing block after the `_kiro/mcp/status` handler (as in earlier drafts) — this leaves the SC-1 check in front of it, causing notification loss under concurrent session creation. **Use instead**: place before the SC-1 check as shown.

**Step 5: Clear `_workflow_children` in `close_session` and `_detach`**

In `close_session` (`acp.py:5879`), add:
```python
self._workflow_children.pop(session_id, None)
```
(Same pattern as `self.crews.pop(session_id, None)` already present there.)

In `_detach` (`acp.py:3662`), add:
```python
self._workflow_children.clear()
```
alongside the other `.clear()` calls at lines 3658–3668.

> **Rejected**: `self._workflow_children.pop(session_id, None)` in `_detach` — `_detach` has no `session_id` parameter (`def _detach(self, reason: str):`). This causes `NameError` on every agent teardown (crash, recycle, idle shutdown), which causes `_detach` to return abnormally, abandoning the `return proc, job` at line 3677, which means `_discard` never starts the background kill thread, and the kiro-cli process lingers indefinitely. **Use instead**: `.clear()` in `_detach`, `.pop(session_id, None)` only in `close_session`.

**Step 6: Crew panel rendering audit**

Verify that `acp.html`'s `setCrew` / `renderCrewPanel` can render an entry where `sessionId` is a real `sess_<uuid>`. The `openSubagent(entry.sessionId)` click path in `acp.html` calls `_handle_subagent_subscribe` which routes via `subagent_sessions` — this will work since we populate `subagent_sessions[childSessionId]` in Step 3. Confirm the `acp-crew-label` renders `agentName` (e.g., "wf-coder") cleanly under 30-char truncation.

**Apply the same rendering adjustments (if any) to `index.html`'s `dashRenderCrewPanel`** — it is a declared mirror of `renderCrewPanel` (`acp.html:1968`: `// mirrors index.html's dashSetCrew() — keep in sync`). The dashboard also receives `subagents` frames via `dashSetCrew`, and its click path (`dashOpenSubagent`) uses the same `_handle_subagent_subscribe` server route. Any rendering change made here must be reflected there. No change needed if Step 6 finds no rendering adjustments required.

**Step 7: Decode `workflow-progress` chunks for basic replay**

`user_message_chunk` frames with `_meta.kiro.kind == "workflow-progress"` embed workflow progress JSON. In the `user_message_chunk` branch of `_on_notification`, add:

```python
_kiro_kind = _kiro_meta.get("kind")
if _kiro_kind == "workflow-progress":
    try:
        progress = json.loads(chunk.get("text") or "{}")
        progress_method = "_kiro/workflow/" + (progress.get("type") or "")
        if progress_method != "_kiro/workflow/":   # only dispatch if type is non-empty
            self._on_workflow_notification(
                progress_method, session_id,
                progress.get("payload") or {})
    except Exception:
        log.debug("ACP workflow-progress chunk decode failed for session=%s", session_id)
    # forward the raw chunk to the transcript (existing path continues below)
```

> **Rejected**: bare `except Exception: pass` — `_on_workflow_notification` dispatches to state-mutating sub-handlers; an exception mid-mutation leaves dicts partially updated and produces no diagnostic output. **Use instead**: `log.debug(...)` to preserve the "never drop the chunk" guarantee while making failures visible in `orchestrator.log`.

**Exit criteria**:
- [ ] Grep `acp.py` for `_kiro/workflow/`: at minimum `_on_workflow_notification`, `_on_workflow_node_start`, `_on_workflow_node_done`, `_on_workflow_run_done` exist
- [ ] `_workflow_children` dict initialized in `__init__` and cleared in `close_session`/`_detach`
- [ ] A PA session that dispatches a multi-step workflow (requires Phase 1 to be live) shows crew panel entries for each running step
- [ ] Clicking a crew entry for a workflow step opens the step's transcript in the sub-panel (requires `subagent_sessions[childSessionId]` to be populated)
- [ ] `tests/test_web.py` has a test covering `_on_workflow_notification` with a `node_start` payload and asserting `crews` and `subagent_sessions` are populated correctly
- [ ] `tests/test_web.py` has a test covering `_on_workflow_notification` with an unknown (non-PA) parent session ID — verifies the guard-and-return path
- [ ] `tests/test_web.py` has a test covering `_workflow_children` cleared by `close_session` while children are still active (verifies SC-6 safety)
- [ ] `docs/KNOWLEDGE.md` has new `_kiro/workflow/*` notification family section (10 methods, payload shapes, PA handlers vs. stubs, `workflow-progress` chunk path)
- [ ] `memory/MEMORY.md` has new `_workflow_children` lifecycle entry (populated by `node_start`; cleared by `node_complete`/`run_complete`/`close_session`/`_detach`; distinct from `subagent_sessions` which persists turn-end for click-to-view routing)

---

### Phase 3: Workflow liveness [QA]

**Goal**: Sessions running a workflow show as Working/Waiting rather than idle. Two independent parts: Part A (ACP-held sessions, depends on Phase 2's `_workflow_children`) and Part B (disk-based / terminal sessions, independent).

**Covers**: SC-5, SC-6

**File scope**: `src/power_atlas/acp.py` (Part A), `src/power_atlas/data_kiro_v3.py` (Part B), `src/power_atlas/web.py` (Part B wiring)

**Part A — ACP-held sessions**

Expose a new method on `_Supervisor`:

```python
def has_active_workflow(self, session_id: str) -> bool:
    """True when session has at least one running workflow child."""
    return bool(self._workflow_children.get(session_id))
```

Wire it into **two** call sites in `web.py`:

**Call site 1 — `_acp_status_for_held`** (line ~3313): Before calling `get_semantic_status`, check `_supervisor.has_active_workflow(session_id)` and early-return `"working"` if true. This covers the dashboard session listing.

**Call site 2 — `api_session_availability`** (line ~6760, `GET /api/session-availability`): The `_compute` closure independently calls `_resolved_session_status` for a single session — bypassing `_acp_status_for_held`. Apply the same `has_active_workflow` guard before `get_semantic_status` in `_compute`. The supervisor reference is already accessed there (`supervisor = getattr(acp, "_supervisor", None)`), so the guard is symmetric.

> **Rejected**: modifying `_publish_live()` with a `frozenset(sessions) | live_with_children` union — `live_with_children ⊆ frozenset(self.sessions)` (it iterates `self.sessions`), so the union is always identical to `frozenset(self.sessions)`. This changes nothing. **Use instead**: the `has_active_workflow()` check in both status call sites above.

**Part B — disk-based / terminal sessions**

**Step 1: Run the probe before implementing Part B.**
Check whether the parent session's `messages.jsonl` mtime advances during workflow execution (KAS writes `workflow-progress` chunks to the parent's transcript per Phase 2 Step 7). If the mtime advances within `_LIVE_MTIME_WINDOW = 300s`, the parent already appears live and Part B may be partially or fully unnecessary.

**Step 2: Implement `active_workflow_children`** (in `data_kiro_v3.py`, only if probe confirms Part B is needed):

```python
def active_workflow_children(parent_id: str, hash_dir: Path) -> list[str]:
    """Return IDs of active workflow children of *parent_id* in *hash_dir*."""
    active: list[str] = []
    try:
        dir_mtime = hash_dir.stat().st_mtime
    except OSError:
        return active
    # Cache per (parent_id, hash_dir, dir_mtime) to avoid repeated reads
    # when the directory contents haven't changed
    cache_key = (parent_id, str(hash_dir), dir_mtime)
    if cache_key in _active_children_cache:
        return _active_children_cache[cache_key]
    for sess_dir in hash_dir.iterdir():
        if not sess_dir.is_dir() or not sess_dir.name.startswith("sess_"):
            continue
        try:
            data = json.loads((sess_dir / "session.json").read_bytes())
            if (data.get("rootConversationId") == parent_id
                    and data.get("status") in ("in_progress", "waiting_on_user")):
                active.append(data.get("id", sess_dir.name))
        except Exception:
            continue
    _active_children_cache[cache_key] = active
    return active
```

Add module-level `_active_children_cache: dict = {}`. The hash-dir mtime cache key means the scan only re-runs when a session is added/removed from the directory.

**Step 3: Wire into liveness pipeline.**
Obtain `hash_dir` via `_find_v3_session_dir(session_id).parent` (already present in `data_kiro_v3.py`) — no schema changes needed to the `Session` dataclass. Do NOT re-derive the hash from the workspace path string (forbidden by `memory-sources.md § The store key`).

In `web.py`'s `_session_is_live` or `_acp_status_for_held`: if the parent appears idle but `active_workflow_children(session_id, hash_dir)` returns non-empty, treat it as Working.

**Exit criteria**:
- [ ] Probe result documented: does the parent's `messages.jsonl` mtime advance during workflow execution? Answer recorded in `docs/KNOWLEDGE.md`.
- [ ] `_Supervisor.has_active_workflow(session_id)` method exists; ACP-held parent session shows Working in the rail when `_workflow_children[session_id]` is non-empty (Part A)
- [ ] Both `_acp_status_for_held` and `api_session_availability` call sites in `web.py` check `has_active_workflow` before calling `get_semantic_status`
- [ ] `active_workflow_children(parent_id, hash_dir)` function exists in `data_kiro_v3.py`; uses `_find_v3_session_dir(session_id).parent` for hash-dir acquisition; uses dir-mtime caching (Part B — required only if probe confirms mtime window doesn't keep parent live)
- [ ] After all children complete, parent reverts to its own status within one rail refresh cycle (≤30s)
- [ ] Unit test for `active_workflow_children` covering: child with matching `rootConversationId` + `in_progress`, child with non-matching `rootConversationId`, child already `idle`, missing `session.json`
- [ ] `docs/KNOWLEDGE.md` § "kiro-cli v3 sub-agent sessions" updated with a sentence describing the inverse lookup
- [ ] `memory/MEMORY.md` `_publish_live` entry has new Update note (follows the existing Update-note chain at MEMORY.md ~line 372)
- [ ] If Phase 3 modifies `classify_kiro_v3`: update the parenthetical note at MEMORY.md ~line 341
- [ ] If Phase 3 modifies `classify_kiro_v3`: update the parenthetical note in `memory/MEMORY.md` ~line 341 ("was deliberately left untouched" → state what changed)

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `invoke_sub_agent` suppression breaks existing crew panel for PA-created sessions | High — PA-created sessions using the old crew shape (e.g., `agent-subtask` tool_call) stop populating the crew panel | By design: when `workflows.enabled`, KAS routes all fan-outs via `run_workflow`. The old `_on_agent_subtask_open` path stays for TUI sessions PA observes but didn't create. Verify post-Phase-2 that a PA-dispatched workflow correctly populates the new crew panel. |
| Phase 1 ships without Phase 2 (deployment race) | High — workflow notifications silently discarded; sessions with active workflows show no crew | Controlled by the explicit coupling note on Phase 2's heading. Document in release notes. |
| Settings audit in Phase 1 under-evaluates a setting (e.g., `_subagent` enables an unknown notification type) | Medium — PA enables a setting without handling its notifications | Phase 1 explicitly requires the audit step before adding any setting to the mapping. Grep `acp.py` for each candidate setting's notification methods before enabling. |
| `session/prompt` return timing (open probe): parent turn ends before workflow children complete | Medium — ACP-held session exits `inflight` while children run; liveness gap | Part A of Phase 3 mitigates via `_workflow_children` check independent of `inflight`. Probe during Phase 3 to confirm. |
| `workflows.enabled` breaks a task-mode session (Spec, Plan, etc.) | Low — KAS docs say these modes are unaffected by session-new settings | Task modes (`spec`, `quick-spec`, etc.) use a different agent binding and are not derived from `poweratlas-acp`. Verify post-Phase-1 that at least one task-mode session behaves normally. |
| `workflowNotifications.delivery: "steer"` has a behavioral effect in a future KAS version | Low | KAS ignores this field today; the field is forward-compat scaffolding. If a future KAS uses it, `"steer"` is the correct value (parent session stays open to steer input). |

A risk whose mitigation is deferred: the `toolSearch` placement question (Phase 1 Step 4) is an open probe — if `toolSearch` must be in both `clientCapabilities` and `session/new`, and we miss this, sessions may lose tool-search in some protocol path. Mitigated by explicit verification step in Phase 1.

## 7) Verification

**Phase 1**:
```powershell
# 1. Create a PA session and capture the session/new body from orchestrator.log
#    Look for: _meta.kiro.settings.workflows.enabled = true
Get-Content "$env:LOCALAPPDATA\power-atlas\orchestrator.log" | Select-String "session/new"

# 2. Wait for available_commands_update notification and verify run_workflow present
# (Check via browser console or PA debug log panel)

# 3. Run unit tests
cd C:\Users\QSylvestre.POLESTAR\Documents\Perso\PowerAtlas
.venv-PowerAtlas\Scripts\python -m pytest tests/ -x -q --timeout=60 -k "settings or session_settings or build_kas"
```

**Phase 2**:
```powershell
# 1. Create a PA session, send a prompt that triggers run_workflow
#    (requires an agent prompt that uses run_workflow — e.g., dispatch a
#    workflow with a workflowPrompt brief)
# 2. Verify crew panel shows running step entries in the /acp page
# 3. Verify clicking a step entry opens its transcript in the sub-panel
# 4. Run unit tests
.venv-PowerAtlas\Scripts\python -m pytest tests/ -x -q --timeout=60 -k "workflow"
```

**Phase 3**:
```powershell
# 1. Observe a session with an active workflow in the rail
#    - Should show Working dot while children run
#    - Should revert to idle/waiting after run_complete
# 2. Run unit tests
.venv-PowerAtlas\Scripts\python -m pytest tests/ -x -q --timeout=60 -k "liveness or workflow_children"
```

**Full node test** (acp.html changes in Phase 2):
```powershell
node tests/acp_page.test.mjs
```

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `docs/KNOWLEDGE.md` | Add "ACP session/new settings" section: full cli.json → KAS mapping, enable/deferred split, `_build_session_settings()` location | 1 |
| `docs/KNOWLEDGE.md` | Add `_kiro/workflow/*` notification family section: 10 methods, payload shapes, PA handlers vs. stubs, `workflow-progress` chunk path | 2 |
| `memory/MEMORY.md` | Add `_workflow_children` lifecycle entry (purpose, populated/cleared lifecycle, distinction from `subagent_sessions`) | 2 |
| `docs/KNOWLEDGE.md` | Add "Workflow notification shapes": `node_start` payload structure, `run_complete` payload, `workflow-progress` chunk format | 2 |
| `docs/KNOWLEDGE.md` | Add probe result: does parent's `messages.jsonl` mtime advance during workflow execution? | 3 |
| `docs/KNOWLEDGE.md` | Update § "kiro-cli v3 sub-agent sessions" (rootConversationId section): add sentence describing the new inverse lookup for liveness | 3 |
| `memory/MEMORY.md` | Append Update note to `_publish_live` entry (~line 372) documenting workflow-children factoring in `_publish_live()` | 3 |
| `memory/MEMORY.md` | If `classify_kiro_v3` is modified in Phase 3: update parenthetical at ~line 341 ("was deliberately left untouched") | 3 (conditional) |
| `plans/ROADMAP.md` | Update "Full workflow support" and "Align PA ACP session settings" entries to note plan in progress / completed phases | 1 (started), 2+3 (as phases complete) |

---

## Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Dynamic `_meta.kiro.settings` | Not started | |
| 2 | Workflow notification handler and crew panel | Not started | Must ship with Phase 1 |
| 3 | Workflow liveness (Part A + Part B) | Not started | Part B independent of Phase 2 |

## 9) Implementation Divergences from Plan

<Reserved — filled during implementation>

## Follow-up Work (Deferred)

1. **Settings audit deferred items.** `checkpoint`, `tangentMode`, `_subagent`, `_delegate`, `c2s`, `disableAutoCompaction` — each has a ROADMAP entry under Session Control & Integration. Add to `_build_session_settings()` when their notification handling and UI are built in PA.
2. **Layer 4 (per-step child ACP subscriptions).** Showing per-step tool activity and transcript in real time requires subscribing to each child session's ACP stream (Phase 2's Step 7 decode of `workflow-progress` chunks covers the replay case). Depends on the process-model spike.
3. **`toolSearch` in session/new.** Phase 1 Step 4 verifies whether `toolSearch` must also appear in `session/new._meta.kiro.settings` (currently only in `clientCapabilities`). If yes, add to `_build_session_settings()`.

## Review Log

### 2026-10-08 — Plan Creation (via /qplan)

Full-effort review, 4 personas (Architect, Senior engineer, Reliability engineer, End-user advocate). 7 single-source MEDIUM findings verified by 2 verifier sub-agents.

18 total findings (6 High, 7 Medium, 5 Low). 17 auto-resolved. 1 Low (M4 probe outcome) pended on live measurement.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `_load_cli_json_settings` doesn't exist anywhere; Phase 1 Step 3 raises `ImportError` | Fixed — replaced with `_load_cli_settings_dict()` defined in `acp.py` |
| 2 | High | `_detach` has no `session_id` param; `.pop(session_id, None)` → `NameError` on every agent teardown, process never killed | Fixed — use `.clear()` in `_detach`, `.pop()` only in `close_session` |
| 3 | High | Crew entry missing `"order"` and `"error"` keys; `_subagents_payload` bare `[]` access → `KeyError` on every `_emit_subagents_frame` call | Fixed — added `"order"`, `"error"`, `"stoppedAt"`, `"fan_out_id"`, `"sessionName"` to entry |
| 4 | High | `parentSessionId` not in `node_start` payload shape; handler silently no-ops for every notification | Fixed — made pre-implementation probe mandatory before coding Step 3; pass parent `session_id` from `_on_notification` outer scope |
| 5 | High | Phase 3 Part A `_publish_live` code block is a no-op union; `publish_acp_sessions` doesn't exist | Fixed — removed wrong snippet; replaced with `has_active_workflow()` + two `web.py` call site pattern |
| 6 | High | SC-1 early-frame buffer intercepts workflow `node_start` (child `sessionId` never in `self.sessions`); notifications orphaned under concurrent `new_session` | Fixed — move `_kiro/workflow/` routing block before SC-1 check in `_on_notification` |
| 7 | Medium | `node_paused` mapped to `failed=True`; paused step signals `waiting_on_user`, not a failure | Fixed — separate handler, `terminal_status="waiting"`, `done=False`, keep in `_workflow_children` |
| 8 | Medium | Phase 3 Part A retracted `_publish_live` snippet left in text; implementer could copy it | Fixed — snippet removed; Rejected note added |
| 9 | Medium | Second `_resolved_session_status` call site at `api_session_availability` (line 6760) not patched; SC-5 gap | Fixed — added `api_session_availability` as explicit second wiring site in Phase 3 Part A |
| 10 | Medium | `session/load` also calls `_build_kas_session_params`; KAS behavior on settings block at load unverified | Fixed — added Probe B to Phase 1 Step 4; record result in KNOWLEDGE.md |
| 11 | Medium | `hash_dir` wiring for Part B unstated; `_find_v3_session_dir(session_id).parent` is the correct lookup | Fixed — explicit lookup named in Phase 3 Part B Step 3; schema change not needed |
| 12 | Medium | `_emit_subagents_frame` called with `crew_spawn_toolcallids`; established v3 convention uses `_active_fan_out_wave` | Fixed — changed to `self._active_fan_out_wave` in all crew handler calls |
| 13 | Medium | `dashSetCrew`/`dashRenderCrewPanel` in `index.html` not audited; declared mirror of `acp.html`'s crew panel | Fixed — added parallel audit requirement to Phase 2 Step 6 |
| 14 | Medium | Missing `sessionId` null guard (`_as_text` pattern); malformed payload could create `None`-keyed entries | Fixed — added null guard spec to Phase 2 Step 3 |
| 15 | Low | Phase 1 exit criteria included Phase 2 doc items (`_kiro/workflow/*` shapes, `_workflow_children` entry) | Fixed — moved to Phase 2 exit criteria |
| 16 | Low | `_evict_finished_subagents` not called in `_on_workflow_node_start`; long sessions accumulate unbounded crew entries | Fixed — added call to Phase 2 Step 3 spec |
| 17 | Low | Phase 2 Step 7 bare `except Exception: pass` on state-mutating call; silent on mid-mutation failure | Fixed — replaced with `log.debug(...)` |
| 18 | Low | `active_workflow_children` O(n) reads per liveness check; no caching | Fixed — added hash-dir mtime caching to Phase 3 Part B spec |

## Harness Improvement Opportunities

<Reserved>

## REVISIT — Deferred kiro-cli settings (from Phase 1 audit)

The following `_meta.kiro.settings` keys are present in the TUI's `w1()` mapping but were deferred because enabling them requires PA UI/UX work not scoped here. Phase 1's settings audit will confirm the classification. Each deferred setting has a ROADMAP entry; verify they are all present and tracked when archiving.

| cli.json key | KAS key | Why deferred |
|---|---|---|
| `chat.enableTangentMode` | `tangentMode` | Tangent mode likely creates a sub-session or visual indicator in the TUI. PA needs to handle whatever notification types it produces. |
| `chat.enableCheckpoint` | `checkpoint` | Checkpoints may create save-point/rollback UI in the TUI that PA would need to surface. |
| `chat.enableC2s` | `c2s` | Unknown feature. Needs investigation of what it does and what UI PA would need. |
| `memory.enabled` | `userMemoryOptIn` | Memory tools need a memory panel in PA before forwarding. |
| `chat.disableAutoCompaction` | `disableAutoCompaction` | Decide whether to forward user preference or always allow compaction in PA. Separate from the /compact replay bug fix (commit `7e28335`). |
| `chat.enableSubagent` | `_subagent` | `invoke_sub_agent` is suppressed when workflows are enabled; interaction between `_subagent` setting and suppression needs investigation. |
| `chat.enableDelegate` | `_delegate` | Same investigation needed as `_subagent`. |

ROADMAP entries: search `plans/ROADMAP.md` for "kiro-cli setting:" to find them.
