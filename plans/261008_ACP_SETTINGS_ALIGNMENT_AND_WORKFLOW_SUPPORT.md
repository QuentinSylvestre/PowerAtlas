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
- SC-3: Settings in the "enable now" category (`thinking`, `knowledge`, `codeIntelligence`; `largeToolOutputHandler` dropped 2026-10-08, see Section 9) are forwarded with their `cli.json` values (or the TUI's defaults when the key is absent).
- SC-4: A PA session running a workflow via `run_workflow` shows running step entries in the crew panel with step name, status (running/done/failed), and elapsed time.
- SC-5: A PA session whose workflow is active shows Working status in the rail and Overview tiles while any child step is `in_progress`, and Waiting when the only remaining steps are `waiting_on_user`.
- SC-6: After all workflow steps complete, are cancelled, or go stale (no workflow frame for the staleness bound), the parent session reverts to its own status.

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
| Settings enable scope | Only settings that are tool-availability-only (no new notification types, no new UI states) | Forward all 24 keys; forward only `workflows` | User constraint: "anything we enable in PA must have full productized support." Phase 1 settings audit confirms which keys qualify; others get ROADMAP entries. Initial "enable" list: `thinking`, `knowledge`, `codeIntelligence`, `workflows`, `goal` (`largeToolOutputHandler` removed 2026-10-08 by user decision: not a KAS setting in any installed bundle 2.24.0-2.28.0). Deferred: `checkpoint`, `tangentMode`, `_subagent`, `_delegate`, `c2s`, `disableAutoCompaction`. |
| `invoke_sub_agent` suppression | Accept — enabling `workflows` suppresses `invoke_sub_agent` for PA sessions | Disable workflow suppression via a KAS flag (no such flag exists) | KAS hard-gates this via `suppressChatDelegationTool`. The old crew panel path (`_on_subagent_list`) still works for TUI sessions PA observes but did not create. PA-created sessions use `run_workflow` exclusively once Phase 2 ships. |
| Phase 1 + Phase 2 coupling | Both phases must ship in the same deployment | Ship Phase 1 first, accept silent discard during Phase 2 gap | Enabling `workflows` without the handler causes all workflow notifications to be silently discarded (logged at INFO). PA's existing session behavior becomes incorrect rather than just limited. |
| `workflowNotifications` field | Omit | Include `workflowNotifications: {enabled, delivery: "steer"}` as forward-compat | KAS ignores it today (0 occurrences in `acp-server.js`); the plan's own rule is to forward only settings with productized PA support. Decided by the user 2026-10-08. Revisit if a KAS version starts reading it. |
| Phase 1/2 gating | `_WORKFLOWS_FORWARD_ENABLED` module constant: `False` in Phase 1, `True` in Phase 2 | Commit-body "do not restart" note; merge Phases 1 and 2 | Property: no deployable build forwards `workflows.enabled: true` without the handlers. A restart between phases is likely and the user performs it. Decided by the user 2026-10-08 (followed the recommendation). |
| Default-on settings | Forward `thinking`, `knowledge`, `codeIntelligence` as TUI defaults (true when absent); `workflows` sends `false` when `chat.enableWorkflows` is absent | Forward only keys explicit in `cli.json` | User accepted the default-on behavior change and its token/indexing cost 2026-10-08. |
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

No cloud resources, APIs, or third-party services. One recurring-usage change, approved by the user 2026-10-08: sessions now enable `thinking`, `knowledge` and `codeIntelligence` by default (the TUI's defaults), which raises token use and local indexing per session compared with the KAS shipped default of off.

## 5) Implementation Phases

### Phase 1: Dynamic `_meta.kiro.settings` [QA]

**Goal**: Implement `_build_session_settings()` that reads `cli.json` and constructs the session/new settings block, audits each setting for full PA support, and forwards the "enable now" set dynamically.

**Covers**: SC-1, SC-2, SC-3

**File scope**: `src/power_atlas/acp.py`, `tests/test_web.py`, `docs/KNOWLEDGE.md`, `plans/ROADMAP.md`

**Step 1: Settings audit** (do this before writing code)

For each `cli.json` key in the deferred set (`checkpoint`, `tangentMode`, `_subagent`, `_delegate`, `c2s`, `disableAutoCompaction`), grep `acp.py` for any existing handler for the notification types that setting might enable. Report:
- Which settings are tool-availability-only (safe to add to the "enable now" list)
- Which produce new notification types PA has no handler for (stay deferred)
- Update the "enable now" / "deferred" split accordingly before writing `_build_session_settings()`

The "enable now" set is `thinking`, `knowledge`, `codeIntelligence`, `workflows`, `goal` (the audit expanded nothing; `largeToolOutputHandler` was dropped). **The code listing below is the original sketch; the shipped code is the contract (`_build_session_settings` in `acp.py`), and it omits `largeToolOutputHandler`.**

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

    # Workflow + goal: both controlled by chat.enableWorkflows.
    # `is True`, not bool(): a string "false" must not enable workflows.
    # Gated by _WORKFLOWS_FORWARD_ENABLED (module constant, False in Phase 1,
    # flipped to True by Phase 2 in the same commit that adds the handlers):
    # no deployable build may forward workflows.enabled=true without them.
    if _WORKFLOWS_FORWARD_ENABLED:
        workflows_on = cli_settings.get("chat.enableWorkflows") is True
        settings["workflows"] = {"enabled": workflows_on}
        settings["goal"] = {"enabled": workflows_on}

    return settings
```

**Step 3: Wire into `_build_kas_session_params`**

Modify `_build_kas_session_params` (`acp.py:959–981`) to call `_build_session_settings()`.

**First**, create `_load_cli_settings_dict()` in `acp.py` (no such helper exists yet — `data_kiro_v3.py` has no cli.json reader). Model it on `_kiro_tool_search_settings` (`acp.py:854`) which already reads `KIRO_CLI_SETTINGS_PATH`:

```python
def _load_cli_settings_dict() -> dict:
    """Return the full cli.json as a dict, or {} on read/parse failure
    or when the top-level JSON value is not an object."""
    try:
        with open(KIRO_CLI_SETTINGS_PATH, encoding="utf-8-sig") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("ACP cli.json unreadable or malformed; using TUI defaults")
        return {}
    if not isinstance(raw, dict):
        log.warning("ACP cli.json top level is not an object; using TUI defaults")
        return {}
    return raw
```

Match the encoding and `isinstance(raw, dict)` guard of `_kiro_tool_search_settings` (`acp.py:854`); a non-dict return would make `_build_session_settings` raise `AttributeError` inside `session/new` and `session/load`, failing every session create. Refactoring `_kiro_tool_search_settings` onto this reader is optional and out of scope unless trivial.

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
- [x] `_build_session_settings()` exists and is covered by a unit test that asserts the correct settings dict for a sample `cli.json` (with `chat.enableWorkflows: true` and with `false`)
- [x] `_load_cli_settings_dict()` exists in `acp.py`; returns `{}` on missing/malformed cli.json
- [x] `_build_kas_session_params()` includes `"settings"` under `_meta.kiro`
- [ ] (deferred to Phase 2) Probe C (run in Phase 2 together with the temporary-constant probe, since the `workflows` keys are gated off in Phase 1; before ticking the two live criteria below): establish the observable that shows `run_workflow` is in a session's tool inventory. `available_commands_update` (`_on_notification`) carries slash commands and skills, not necessarily the model's tool list, so it may not show `run_workflow`. Candidates: the `session/new` response, `orchestrator.log` INFO frames, or a prompt asking the agent to list its tools. Record the chosen observable in `docs/KNOWLEDGE.md`.
- [ ] (deferred to Phase 2) A live PA session with `chat.enableWorkflows: true` shows `run_workflow` via the observable from Probe C (run in a throwaway session; the real workflow run belongs to Phase 2)
- [ ] (deferred to Phase 2) A live PA session with `chat.enableWorkflows: false` does NOT show `run_workflow`
- [ ] (deferred to Phase 2: table, Probe A and Probe B are recorded; Probe C and the unmeasured resumed-session effect are not) `docs/KNOWLEDGE.md` has the settings mapping table with probe results (toolSearch placement, `session/load` behavior, Probe C observable)
- [x] Tests live in the existing `tests/test_web.py` (no new test file). They cover `_build_session_settings()` for ≥3 `cli.json` shapes (workflows `true`, `false`/absent, string `"false"`) and `_load_cli_settings_dict()` for a missing file, a malformed file and a list-valued file. Name the functions `test_build_session_settings_*` and `test_load_cli_settings_dict_*` so `-k "build_session_settings or load_cli_settings_dict"` selects exactly them.
- [x] `_WORKFLOWS_FORWARD_ENABLED = False` in Phase 1, and a test asserts the settings block has no `workflows`/`goal` key while it is `False`. Consequently the two live `run_workflow` criteria above cannot be met in Phase 1: tick them in Phase 2 after the constant flips to `True`, and mark them `deferred to Phase 2` here.

#### Implementation notes

Implementation (2026-10-08, code: 26e66c0; review fixes c1c882f, 080241e; docs fix bcf434e)

Phase 1 is committed in `26e66c0`, with review fixes in `c1c882f` and `080241e` and a docs and roadmap accuracy fix in `bcf434e`. In `src/power_atlas/acp.py` the work added `_load_cli_settings_dict` (utf-8-sig; a missing file gives `{}` silently; malformed, unreadable, deeply nested or non-object content gives `{}` with a warning naming the path and the error), the module constant `_WORKFLOWS_FORWARD_ENABLED = False` and `_build_session_settings`. The settings builder maps `thinking`, `knowledge` and `codeIntelligence` with TUI defaults (on when absent, an explicit false wins, a non-bool counts as absent). `workflows` and `goal` are emitted only when the gate is `True` and only for a JSON `true`. `workflowNotifications`, `largeToolOutputHandler` and the deferred keys are never sent. `_build_kas_session_params` now carries the block under `_meta.kiro.settings` for both `session/new` and `session/load`, and a new `_log_session_settings` logs `{key: enabled}` at INFO before each request. Tests in `tests/test_web.py`: `TestBuildSessionSettings`, `TestSessionSettingsOnTheWire` (captures the real `session/new` and `session/load` requests), and a module-wide autouse redirect of `KIRO_CLI_SETTINGS_PATH` so no test reads the developer's real `cli.json`; targeted selection 43 passed, `tests/test_web.py` 2789 passed with 3 known pre-existing failures (proven on a pristine export), `tests/test_data.py` 722 passed. `docs/KNOWLEDGE.md` has the "ACP session/new settings" section (mapping, deferrals, gate, probes) and `plans/ROADMAP.md` entries were added or corrected. Probes on a second kiro-cli 2.28.0 process: the shipped block opens a session cleanly; `session/new` honours `workflows` (`_meta.workflowsEnabled`); `session/load` leaves the persisted `workflowsEnabled` unchanged; `toolSearch` placement and the effect of the three default-on keys on a resumed session's tool inventory are not determined (deferred to Phase 2).

Per-phase QA (Step 5b): not run separately. The only observable change with the gate off is the new settings log line, and seeing it needs a PowerAtlas restart; Phase 2 needs a restart for its own probe, so the live check is carried there as a deferred criterion (user granted restarts for this plan 2026-10-08).

---

### Phase 2: Workflow notification handler and crew panel [QA]

**Goal**: Handle `_kiro/workflow/*` notifications in `_on_notification`, populate `crews` and `subagent_sessions` from `node_start`/`run_complete`, display workflow step entries in the crew panel. **This phase must ship together with Phase 1.**

**Covers**: SC-4

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/templates/acp.html`, `src/power_atlas/templates/index.html` (Step 6 mirror), `src/power_atlas/static/style.css` and `src/power_atlas/static/composer-chrome.js` (only if Step 6 finds a rendering change), `tests/test_web.py`, `tests/acp_page.test.mjs`, `docs/KNOWLEDGE.md`, `memory/MEMORY.md`, `plans/ROADMAP.md`

**Step 1: Add `_workflow_children` dict to `_Supervisor.__init__`**

In `_Supervisor.__init__` (`acp.py:3239`), add:

```python
# workflow child sessions: parent_id → {child_id: (state, last_seen)}.
# state is "running" or "waiting"; last_seen is time.monotonic() of the last
# workflow frame that touched the child. Populated by _kiro/workflow/node_start;
# removed on node_complete/run_complete and in close_session/_detach. Entries
# older than _WORKFLOW_CHILD_STALE_S (module constant, 600 s) are ignored by
# has_active_workflow and pruned lazily, so a lost run_complete (cancel, crash,
# dropped frame) cannot pin a session to Working forever.
self._workflow_children: dict[str, dict[str, tuple[str, float]]] = {}
```

Add a helper `_workflow_set(parent_id, child_id, state)` that writes `(state, time.monotonic())` and is the only writer; every `_kiro/workflow/*` frame for a known child refreshes `last_seen`.

**Line numbers in this plan drift** (the code moved about 35 lines between exploration and review). Anchor every edit by function or class name, not by the cited line.

**Step 2: Implement `_on_workflow_notification()`**

**Mandatory pre-implementation probe (do this before writing Step 3's sub-handlers)**: determine what the outer `params.sessionId` carries on `_kiro/workflow/*` frames, and where the parent session ID lives. The documented payload (`{ sessionId, nodeId, nodePath, branchId, iteration, agentName }`) has no `parentSessionId`, and `sessionId` there looks like the node's own (child) ID. This plan previously assumed both "outer id = parent" (Steps 2-3) and "outer id = child" (Step 4); at most one is true.

A TUI workflow run never appears in PA's `orchestrator.log` (it records only frames from PA-spawned kiro-cli), so the probe cannot use one. Instead: with Phase 1 code built and `_WORKFLOWS_FORWARD_ENABLED` temporarily set to `True` in the working tree only (never committed in that state; the Phase 1 gate otherwise omits the `workflows` keys), run one throwaway PA session with `chat.enableWorkflows: true`, dispatch a trivial two-step workflow, and read the first `_kiro/workflow/node_start`, `node_complete`, `node_paused` (if reachable) and `run_complete` frames from the existing INFO fallthrough log (`ACP notification ... (...)`, which logs full params). This requires a PA restart: ask the user for a restart grant first (project AGENTS.md). Record the four payloads in `docs/KNOWLEDGE.md`.

Then implement exactly one **parent resolution** helper, `_resolve_workflow_parent(outer_session_id, params) -> str | None`, and write its ladder into the plan's Divergences section after the probe:
1. If the outer id is in `self.sessions`, it is the parent.
2. Otherwise, if the outer id is a known child (in `subagent_sessions`), use its recorded parent.
3. Otherwise, if exactly one session is in `inflight` (the ladder `_on_subagent_list` uses), use it.
4. Otherwise log at INFO (with the frame) and return `None`; the handler returns without mutating state.

Add a test where the outer id is a child id and a test where it is the parent id. Also record from the probe: the `node_complete`/`run_complete` payload fields that signal failure or cancellation (needed for `failed`, see Step 3), whether `loop_iteration` and `branchId` reuse or mint child session IDs, and whether KAS emits `run_complete` on cancel.

Add a new method to `_Supervisor` near `_on_subagent_list` (`acp.py:4190`):

```python
def _on_workflow_notification(self, method: str, session_id: str, params: dict) -> None:
    """Route one *_kiro/workflow/** frame to the appropriate handler.

    ``session_id`` is the OUTER ``params.sessionId`` extracted by
    ``_on_notification``. Whether that is the parent or the child is settled by
    the Step 2 probe; resolve the parent with ``_resolve_workflow_parent``
    before any mutation.
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
- `parentSessionId` = `_resolve_workflow_parent(session_id, params)` (Step 2 ladder); `childSessionId` = the field the probe confirms
- Guard both: `if not isinstance(parentSessionId, str) or not isinstance(childSessionId, str): log.warning(...); return` (follows `_on_subagent_list`'s defensive `_as_text` pattern)
- If `parentSessionId not in self.sessions`: log warning, return
- **Idempotent and terminal-sticky** (mirrors `_on_subagent_list`'s "terminal is sticky" guard): if `childSessionId` is already in `self.crews[parentSessionId]`, refresh `last_seen` and return without resetting `startedAt`, `order` or history; if the existing entry is `done`, ignore the frame. A repeated `node_start` (live frame plus replayed chunk, `loop_iteration`) must never re-open a finished step or wipe `subagent_history`.
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
    "order":       self._next_crew_order(parentSessionId),  # REQUIRED; monotonic counter, NOT len(crew) (eviction shrinks len and collides)
    "action":      "running",
    "fan_out_id":  self._active_fan_out_wave.get(parentSessionId, _NO_ANCHOR_TOOLCALLID),  # must equal the wave the frame filters on
    "startedAt":   time.time(),
    "stoppedAt":   None,
}
```

- Update `_workflow_children` via `_workflow_set(parentSessionId, childSessionId, "running")`
- Call `_emit_subagents_frame(parentSessionId, self.crews, self._active_fan_out_wave)` — use `_active_fan_out_wave`, not `crew_spawn_toolcallids` (per established v3 fan-out convention, `_emit_subagents_frame`'s docstring)

> **Rejected**: tagging workflow entries with a fixed `_NO_ANCHOR_TOOLCALLID` — `_subagents_payload` drops entries whose `fan_out_id` differs from the parent's current wave (`_active_fan_out_wave[parent]`, set by `_on_agent_subtask_open` and cleared only at `close_session`/`_detach`). After any earlier `invoke_sub_agent` fan-out in the same session, every workflow entry would be filtered out and the crew panel empty. **Use instead**: tag with the current wave id (`_active_fan_out_wave.get(parent, _NO_ANCHOR_TOOLCALLID)`) as shown. Add a test that runs an old-style fan-out first, then a workflow, and asserts the emitted payload contains the workflow entries.
>
> **Rejected**: passing `self.crew_spawn_toolcallids` as third arg to `_emit_subagents_frame` — all active v3 fan-out call sites use `self._active_fan_out_wave` per the function's own docstring. **Use instead**: `self._active_fan_out_wave`.
>
> **Rejected**: omitting `"order"`, `"error"` from the crew entry — `_subagents_payload` (`acp.py:6063`) uses `entry["order"]` as the sort key and `entry["error"]` as a payload field, both bare `[]` accesses. A `KeyError` fires the first time any workflow session emits a crew frame. **Use instead**: include both fields as shown above.

`_on_workflow_node_done(session_id, params, terminal_status)`:
- Resolve parent via `_resolve_workflow_parent`; extract `childSessionId` from params
- `terminal_status` is `"done"`, `"failed"` or `"waiting"`. **`failed` source**: the dispatcher passes `"failed"` when the `node_complete` payload carries the error/status field the Step 2 probe identifies (record the field name in `docs/KNOWLEDGE.md`); if the probe finds no failure signal, delete the `failed` branch and the `error` population, and reduce SC-4's status set to running/done/waiting. Do not ship an unreachable branch.
- If crew entry exists: for `done`/`failed` set `entry["done"] = True`, `entry["status"] = terminal_status`, `entry["stoppedAt"] = time.time()`. For `waiting` set `entry["status"] = "waiting"` and leave `done=False` and `stoppedAt=None` (a waiting step is not stopped; setting `stoppedAt` freezes the elapsed timer).
- **Removal from `_workflow_children` is unconditional** for `done`/`failed`, even when no crew entry exists (the turn-end `finally` marks every not-done crew entry done and `_evict_crew_children` drops them at the next turn start, so the crew entry may be gone while the child is still tracked). An unknown child on `node_complete` is logged at INFO and otherwise ignored.
- For `waiting`: set state `"waiting"` via `_workflow_set` — **keep in `_workflow_children`** (the step is paused, not complete); Phase 3 reports it as waiting, not working. Verify the crew panel's status vocabulary (`acp.html` `setCrew`) renders `"waiting"`; if it does not, map it to an existing label in Step 6.
- Set `entry["error"]` only for `terminal_status == "failed"` (non-empty string)
- Call `_emit_subagents_frame(parentSessionId, self.crews, self._active_fan_out_wave)`

`_on_workflow_run_done(session_id, params)`:
- For each child in `self._workflow_children.pop(parentSessionId, {})`: mark crew entry (if present) `done=True`, `status="done"` (or `"failed"` if the probe-identified field says so), `stoppedAt=time.time()`
- Call `_emit_subagents_frame`

**Turn-end interaction.** The turn-end `finally` in the prompt path marks every not-done crew entry done, including live workflow children, and `session/prompt` may return before the workflow ends (open risk in Section 6). Decision: workflow children are exempt from that sweep while they remain in `_workflow_children` (so the crew panel keeps showing them); a turn ending with `stopReason` `cancelled` or `error` clears `_workflow_children[session_id]` and marks the entries done. Add a test for each branch.

**Idle sweeper.** `_sweepable` requires only idle-past-TTL, no subscriber, not in `inflight`, not closing/loading. A parent whose prompt returned while children run would be closed mid-workflow (children and crew popped, later frames dropped by the unknown-parent guard while kiro-cli keeps running). `_sweepable` is a module-level function (`_sweepable(session_id, meta, now)`), so pass it the active-workflow answer from its caller (`_sweep_once`, which has the supervisor) rather than referencing `self`; add `and not has_active_workflow` to its conditions. The staleness bound on `_workflow_children` keeps this from becoming a permanent pin. Add a test.

**Observability.** The new router returns before the existing INFO fallthrough, which AGENTS.md names as the evidence path. Log `node_start`, `node_complete`, `node_paused`, `run_complete` and every stub method at INFO with the same truncated params (`%.600s`), plus the parent-resolution outcome and `_workflow_children` size after each mutation. Log the `settings` keys sent at `session/new` (keys only) in Phase 1.

**Step 4: Route in `_on_notification`**

Add the workflow routing block **conditionally before the SC-1 early-frame buffer check** in `_on_notification` (the `session_id not in self.sessions AND _reserved > 0` check). Placement depends on the Step 2 probe result:
- If the outer `sessionId` is the **child** (never in `self.sessions`): route before SC-1, otherwise SC-1 buffers the frame under the child's ID and orphans it; the SC-1 replay path only replays frames for the newly created parent.
- If the outer `sessionId` is the **parent**: the parent is in `self.sessions`, SC-1 does not fire for it, and the block may sit after SC-1. Placing it before SC-1 is still harmless, so keep it before.

```python
# Route workflow notifications before SC-1: if the outer sessionId is a
# child step's ID (never in self.sessions), SC-1 would buffer it incorrectly.
# Parent resolution happens inside _on_workflow_notification.
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

`user_message_chunk` frames with `_meta.kiro.kind == "workflow-progress"` embed workflow progress JSON. **Decode for display only; never drive liveness state from it.** The same chunks arrive on `session/load` replay and on reconnect, so a historic `node_start` chunk with no persisted `run_complete` (killed or cancelled workflow) would re-register children and pin the session to Working on every resume; live frames also arrive twice (real notification plus chunk). In the `user_message_chunk` branch of `_on_notification` (variables there are `update`, `content`, `text`, `_kiro_meta` — there is no `chunk`), add:

```python
if _kiro_meta.get("kind") == "workflow-progress":
    try:
        progress = json.loads(text or "{}")
    except ValueError:
        log.debug("ACP workflow-progress chunk not JSON: session=%s", session_id)
        progress = None
    if isinstance(progress, dict):
        # Display-only: decode the step names for the transcript rendering.
        # Do NOT call _on_workflow_notification here (see note above).
        ...
    # the raw chunk continues down the existing transcript path unchanged
```

The exact display behaviour (what, if anything, is rendered from the decoded progress) is a Phase 2 decision after the probe shows the chunk shape; the default is no rendering change and a `log.debug` of the decoded `type`. Whether chunks suffice to rebuild crew state on resume stays an open item (Layer 4). Add a test that feeds a `workflow-progress` chunk through `_on_notification` and asserts `crews` and `_workflow_children` are untouched and the chunk is still emitted.

> **Rejected**: dispatching `_on_workflow_notification` from the chunk — replay re-populates `_workflow_children` for finished workflows and double-applies live frames. **Use instead**: display-only decode.
>
> **Rejected**: referencing `chunk.get("text")` — `chunk` is not defined in that branch (`NameError`, which a broad `except` then hides). **Use instead**: `text` and `_kiro_meta`, with the `except` narrowed to `ValueError`.
>
> **Rejected**: bare `except Exception: pass` — `_on_workflow_notification` dispatches to state-mutating sub-handlers; an exception mid-mutation leaves dicts partially updated and produces no diagnostic output. **Use instead**: `log.debug(...)` to preserve the "never drop the chunk" guarantee while making failures visible in `orchestrator.log`.

**Exit criteria**:
- [ ] Grep `acp.py` for `_kiro/workflow/`: at minimum `_on_workflow_notification`, `_on_workflow_node_start`, `_on_workflow_node_done`, `_on_workflow_run_done` exist
- [ ] `_workflow_children` dict initialized in `__init__` and cleared in `close_session`/`_detach`
- [ ] A PA session that dispatches a multi-step workflow (requires Phase 1 to be live) shows crew panel entries for each running step
- [ ] Clicking a crew entry for a workflow step opens the step's transcript in the sub-panel (requires `subagent_sessions[childSessionId]` to be populated)
- [ ] Probe from Step 2 done: outer-`sessionId` meaning, `node_complete`/`run_complete` failure field, loop/branch child-id behaviour, run_complete-on-cancel recorded in `docs/KNOWLEDGE.md`; the parent-resolution ladder written into Section 9
- [ ] `_WORKFLOWS_FORWARD_ENABLED` flipped to `True` in the same commit that adds the handlers; the two deferred Phase 1 live `run_workflow` criteria (present with `true`, absent with `false`) are ticked here
- [ ] A live PA session with `chat.enableWorkflows: true` dispatches a real workflow via `run_workflow` (the Phase 1 live check is only the tool-inventory observable)
- [ ] `tests/test_web.py` tests (existing file; names containing `workflow` so `-k workflow` selects them): `node_start` populates `crews` and `subagent_sessions`; outer id is a child id and outer id is the parent id (both resolve); unknown parent id returns without mutation; duplicate `node_start` is idempotent and does not re-open a done step; `node_paused` keeps the child as `waiting` with `stoppedAt` unset; `node_complete` for an unregistered child is ignored; a workflow run after an earlier `invoke_sub_agent` fan-out still emits its entries; `close_session` and `_detach` clear `_workflow_children`; turn-end with `cancelled`/`error` clears it and a normal turn end does not; `_sweepable` refuses a session with active children; a `workflow-progress` chunk leaves `crews`/`_workflow_children` untouched
- [ ] `node tests/acp_page.test.mjs` passes, with a case rendering a workflow crew row (`status: "waiting"` included) in `acp.html` and, if mirrored, `index.html`
- [ ] `docs/KNOWLEDGE.md` has new `_kiro/workflow/*` notification family section (10 methods, payload shapes, PA handlers vs. stubs, `workflow-progress` chunk path)
- [ ] `memory/MEMORY.md` has new `_workflow_children` lifecycle entry (populated by `node_start`; cleared by `node_complete`/`run_complete`/`close_session`/`_detach`/cancelled-turn end/staleness bound; distinct from `subagent_sessions` which persists turn-end for click-to-view routing)
- [ ] `plans/ROADMAP.md` workflow entries updated for Phase 2
- [ ] (deferred from Phase 1) Update `test_build_session_settings_workflows_keys_absent_while_gated_off` (asserts `_WORKFLOWS_FORWARD_ENABLED is False`) in the same commit that flips the constant; it fails by design once the gate is on
- [ ] (deferred from Phase 1) Probe A (does `toolSearch` in `session/new` settings have any observable effect) and the resumed-session effect: with the workflows probe session live, check the model's tool inventory with and without the `thinking`/`knowledge`/`codeIntelligence` block on `session/new` and on `session/load`; record in `docs/KNOWLEDGE.md` (user decision 2026-10-08: `session/load` keeps sending the block)
- [ ] (deferred from Phase 1) Live check that a real PA `session/new` logs `ACP session/new sending settings` with the expected keys (covers Phase 1's QA step, which was not run separately to avoid an extra PowerAtlas restart)

---

### Phase 3: Workflow liveness [QA]

**Goal**: Sessions running a workflow show as Working/Waiting rather than idle. Two independent parts: Part A (ACP-held sessions, depends on Phase 2's `_workflow_children`) and Part B (disk-based / terminal sessions, independent).

**Covers**: SC-5, SC-6

**File scope**: `src/power_atlas/acp.py` (Part A), `src/power_atlas/data_kiro_v3.py` (Part B), `src/power_atlas/web.py` (Parts A and B wiring), `src/power_atlas/overview.py` (only if its `LiveDeps` closure needs the snapshot), `tests/test_web.py`, `tests/test_data_kiro_v3.py`, `docs/KNOWLEDGE.md`, `memory/MEMORY.md`, `plans/ROADMAP.md`

**Part A — ACP-held sessions**

Expose a tri-state method on `_Supervisor` that ignores entries older than `_WORKFLOW_CHILD_STALE_S` (Phase 2 Step 1):

```python
def workflow_state(self, session_id: str) -> str | None:
    """"working" if any fresh child is running, "waiting" if only waiting
    children remain, else None."""
```

**Threading.** `_acp_status_for_held` runs in an `asyncio.to_thread` hop (listing routes, `overview.LiveDeps.acp_status_for_held`, and `api_session_availability`'s `_compute`), and `_supervisor.sessions` state is loop-owned and unlocked — the existing code snapshots `held` on the loop first. Follow the same pattern: on the loop, build `workflow_states = {sid: st for sid in held if (st := sup.workflow_state(sid))}` once per request and pass it into `_acp_status_for_held`, the `LiveDeps` closure and `_compute` as a parameter; do not call the supervisor from the worker thread. Plain dict snapshot is enough; no lock.

**Ordering.** Apply the workflow override **after** the existing verdict from `_resolved_session_status`, and only upgrade an idle verdict: `errored` (which `_resolved_session_status` deliberately preserves) and the parent's own permission-pending `waiting` must not be masked. Map: `workflow_states[sid] == "working"` and verdict idle → `working`; `"waiting"` and verdict idle → `waiting`.

Wire it into **two** families of call sites in `web.py` (anchor by function name):

**Call site 1 — `_acp_status_for_held`**: covers the dashboard session listing and the Overview tiles (via `LiveDeps`).

**Call site 2 — `api_session_availability`** (`GET /api/session-availability`): its `_compute` closure independently calls `_resolved_session_status` for a single session, bypassing `_acp_status_for_held`. Apply the same override there using the snapshot.

> **Rejected**: modifying `_publish_live()` with a `frozenset(sessions) | live_with_children` union — `live_with_children ⊆ frozenset(self.sessions)` (it iterates `self.sessions`), so the union is always identical to `frozenset(self.sessions)`. This changes nothing. **Use instead**: the tri-state `workflow_state()` override in both status call sites above.

**Part B — disk-based / terminal sessions**

Part B serves **non-held** (terminal) rows only; for held sessions it would duplicate Part A. `_acp_status_for_held` handles only held sessions, so it is not a Part B site.

**Step 1: Run the probe before implementing Part B.**
Check whether the parent session's `messages.jsonl` mtime advances during workflow execution (KAS writes `workflow-progress` chunks to the parent's transcript per Phase 2 Step 7). The live window `_LIVE_MTIME_WINDOW = 300s` is defined in `web.py` (not `data_kiro_v3.py`). If the mtime advances within it, the parent already appears live and Part B is unnecessary; in that case record the result, mark the Part B exit criteria `n/a: probe shows parent stays live` and skip Steps 2-3. Also record in the probe: the real child `status` literals in `session.json`, and whether child `rootConversationId` equals the bare parent id or the `sess_`-prefixed form (`_is_subagent_session` reads it only as a boolean today).

**Step 2: Implement `active_workflow_children`** (in `data_kiro_v3.py`, only if the probe confirms Part B is needed). Contract, not code:
- Input: parent id and its hash dir. Output: IDs of child sessions whose `rootConversationId` matches the parent (in the form the probe found) and whose `status` is one of the probe-confirmed active literals (expected `in_progress`, `waiting_on_user`).
- A child counts as active only if its `session.json` mtime is within `_LIVE_MTIME_WINDOW`; a crashed child leaves `status: in_progress` on disk forever and must not pin the parent.
- **Cache per child on that child's `session.json` mtime**, the way the existing session index in `data_kiro_v3.py` does (it indexes on per-`session.json` mtimes). The cache is bounded (evict on size).
- An unparseable `session.json` is skipped and logged at DEBUG.

> **Rejected**: caching on `hash_dir.stat().st_mtime`. A directory's mtime changes only when entries are added, removed or renamed; a child's `session.json` flipping `in_progress` to idle is an in-place write, so the cached "active" answer would persist until an unrelated session folder appeared (parent stuck Working, violating SC-6), and every directory mtime change would add a never-evicted key. **Use instead**: per-child `session.json` mtime, bounded cache.

**Step 3: Wire into liveness pipeline.**
Obtain `hash_dir` via `_find_v3_session_dir(session_id)`; it returns `Path | None`, so guard `None` before `.parent`. It is an uncached scan of hash dirs, so call it only for non-held rows whose own verdict is idle, and measure the cost over the ~376-store case (`docs/KNOWLEDGE.md` § sessions) before wiring it into the per-row rail path; cache the resolved hash dir per session id if the measurement warrants. No schema change to the `Session` dataclass. Do NOT re-derive the hash from the workspace path string (forbidden by `memory-sources.md § The store key`).

Wire into the non-held branch of the status resolution in `web.py` (the function that produces the row verdict for terminal sessions; the implementer names it after reading `_resolved_session_status`'s callers). Do not use `_session_is_live`: it returns a process-liveness bool, not a Working/idle verdict. Apply the same only-upgrade-idle ordering as Part A.

**Exit criteria**:
- [ ] Probe result documented: does the parent's `messages.jsonl` mtime advance during workflow execution? Answer recorded in `docs/KNOWLEDGE.md`.
- [ ] `_Supervisor.workflow_state(session_id)` exists and ignores entries older than `_WORKFLOW_CHILD_STALE_S`; an ACP-held idle parent shows Working in the rail while a child runs and Waiting when only waiting children remain (Part A)
- [ ] Both `_acp_status_for_held` (and the Overview `LiveDeps` path) and `api_session_availability` apply the override from a loop-side snapshot, after the existing verdict, upgrading only an idle verdict (an `errored` or permission-pending parent is not masked)
- [ ] `active_workflow_children` exists in `data_kiro_v3.py`, uses per-child `session.json` mtime caching (bounded) and the live-window check, guards a `None` from `_find_v3_session_dir` (Part B; `n/a: probe shows parent stays live` if Step 1 says so)
- [ ] After all children complete (or the staleness bound passes), the parent reverts to its own status within one rail refresh cycle (≤30s)
- [ ] Tests in existing files, names containing `workflow_state` / `workflow_children` / `liveness`: `tests/test_web.py` for tri-state, staleness expiry, errored-not-masked and both call sites; `tests/test_data_kiro_v3.py` for `active_workflow_children` (matching root + active status, non-matching root, already idle, stale mtime, missing `session.json`, status flip invalidates the cache)
- [ ] `docs/KNOWLEDGE.md` § "kiro-cli v3 sub-agent sessions" updated with a sentence describing the inverse lookup
- [ ] `memory/MEMORY.md`: append a note to the `_publish_live` Update chain (the latest entry, not mid-chain; locate it by heading) stating that `_publish_live` is intentionally unchanged and workflow liveness is overlaid at the status call sites
- [ ] If Phase 3 modifies `classify_kiro_v3`: update the parenthetical in `memory/MEMORY.md` that says it "was deliberately left untouched" to state what changed (locate by that phrase, not by line)
- [ ] `plans/ROADMAP.md` workflow entries updated for Phase 3

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `invoke_sub_agent` suppression breaks existing crew panel for PA-created sessions | High — PA-created sessions using the old crew shape (e.g., `agent-subtask` tool_call) stop populating the crew panel | By design: when `workflows.enabled`, KAS routes all fan-outs via `run_workflow`. The old `_on_agent_subtask_open` path stays for TUI sessions PA observes but didn't create. Verify post-Phase-2 that a PA-dispatched workflow correctly populates the new crew panel. |
| Phase 1 ships without Phase 2 (deployment race) | High — workflow notifications silently discarded; sessions with active workflows show no crew | Mitigated in code: `_WORKFLOWS_FORWARD_ENABLED` stays `False` until Phase 2 flips it with the handlers. |
| Settings audit in Phase 1 under-evaluates a setting (e.g., `_subagent` enables an unknown notification type) | Medium — PA enables a setting without handling its notifications | Phase 1 explicitly requires the audit step before adding any setting to the mapping. Grep `acp.py` for each candidate setting's notification methods before enabling. |
| `session/prompt` return timing (open probe): parent turn ends before workflow children complete | Medium — ACP-held session exits `inflight` while children run; liveness gap | Part A of Phase 3 mitigates via `_workflow_children` check independent of `inflight`. Probe during Phase 3 to confirm. |
| `workflows.enabled` breaks a task-mode session (Spec, Plan, etc.) | Low — KAS docs say these modes are unaffected by session-new settings | Task modes (`spec`, `quick-spec`, etc.) use a different agent binding and are not derived from `poweratlas-acp`. Verify post-Phase-1 that at least one task-mode session behaves normally. |

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
.venv-PowerAtlas\Scripts\python -m pytest tests/ -x -q --timeout=60 -k "build_session_settings or load_cli_settings_dict"
```

**Phase 2**:
```powershell
# 1. Create a PA session, send a prompt that triggers run_workflow
#    (requires an agent prompt that uses run_workflow — e.g., dispatch a
#    workflow with a workflowPrompt brief)
# 2. Verify crew panel shows running step entries in the /acp page
# 3. Verify clicking a step entry opens its transcript in the sub-panel
# 4. Run unit tests
.venv-PowerAtlas\Scripts\python -m pytest tests/ -x -q --timeout=60 -k "workflow"   # drop -x here: select by the test names fixed in the exit criteria
```

**Phase 3**:
```powershell
# 1. Observe a session with an active workflow in the rail
#    - Should show Working dot while children run
#    - Should revert to idle/waiting after run_complete
# 2. Run unit tests
.venv-PowerAtlas\Scripts\python -m pytest tests/ -x -q --timeout=60 -k "workflow_state or workflow_children or liveness"
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
| `memory/MEMORY.md` | Append Update note at the end of the `_publish_live` Update chain: `_publish_live` intentionally unchanged; workflow liveness overlaid at the status call sites | 3 |
| `memory/MEMORY.md` | If `classify_kiro_v3` is modified in Phase 3: update the parenthetical containing "was deliberately left untouched" | 3 (conditional) |
| `docs/KNOWLEDGE.md` | Probe C observable and Phase 2 probe payloads (outer `sessionId` meaning, failure field, loop/branch ids) | 1, 2 |
| `plans/ROADMAP.md` | Update "Full workflow support" and "Align PA ACP session settings" entries to note plan in progress / completed phases | 1 (started), 2+3 (as phases complete) |

---

## Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Dynamic `_meta.kiro.settings` | Complete (code: 26e66c0) | Live `run_workflow` checks, Probe C, Probe A and resumed-session probe deferred to Phase 2 |
| 2 | Workflow notification handler and crew panel | Not started | Flips `_WORKFLOWS_FORWARD_ENABLED`; needs a PowerAtlas restart for its probe (granted) |
| 3 | Workflow liveness (Part A + Part B) | Not started | Part B independent of Phase 2 |

## 9) Implementation Divergences from Plan

- **Phase 1: `largeToolOutputHandler` dropped.** The plan's original sketch mapped `chat.enableLargeToolOutputHandler` to `largeToolOutputHandler` (default on). Review found it is not a KAS setting (0 occurrences in all seven installed bundles, 2.24.0 to 2.28.0), so sending it did nothing. The user chose on 2026-10-08 to stop forwarding it; plan text updated in SC-3, Section 3 and the Phase 1 intro. The listing in Phase 1 Step 2 is kept as the original sketch.
- **Phase 1: Probe B method.** The plan said to observe `available_commands_update`; the implementer used `_meta.workflowsEnabled` on the `session/new` result and the persisted `session.json` instead, which is more direct and reproducible.
- **Phase 1: existing test expectation changed.** `test_new_session_params_include_meta` now expects the settings block (three default-on keys); source: the plan's Step 3 spec plus the user's key-drop decision.
- **Phase 1: Probe A not determined.** `toolSearch` in `session/new` settings gave no observable difference; recorded as not determined and carried to Phase 2.

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

### 2026-10-08 -- Plan Revision (via /qplan, review mode)

Standard-effort review, 3 personas (Architect with gap-critic lens, Senior engineer, Reliability engineer), run in parallel; the earlier Review Log findings were re-verified against the code, not trusted. 29 distinct findings after dedup (9 High, 15 Medium, 5 Low) of which the table lists the merged ones. 26 auto-resolved in the plan body; 3 escalated (Open decisions below). Corroboration: #1, #4, #5, #6 and #8 were raised by all three personas, #2 and #7 by two.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | Plan assumed both "outer sessionId = parent" (Steps 2-3) and "= child" (Step 4); handler could still no-op on every frame. | Fixed -- probe rewritten (PA log, not TUI run); `_resolve_workflow_parent` ladder; SC-1 placement made conditional. |
| 2 | High | `_workflow_children` had no staleness bound or cancel/turn-end reconciliation; lost `run_complete` pins session to Working. | Fixed -- `(state, last_seen)` entries with 600 s bound; cancelled/error turn end clears; test added. |
| 3 | High | Step 7 snippet referenced undefined `chunk` (NameError hidden by broad except) and replay would re-register finished children. | Fixed -- display-only decode using `text`/`_kiro_meta`; no state mutation; idempotent `node_start`. |
| 4 | High | Part B cache keyed on directory mtime cannot see in-place `session.json` status changes; unbounded growth. | Fixed -- per-child `session.json` mtime, bounded cache, live-window check; Rejected note added. |
| 5 | High | Workflow entries tagged `_NO_ANCHOR_TOOLCALLID` are filtered out after any earlier `invoke_sub_agent` fan-out. | Fixed -- tag with current `_active_fan_out_wave`; Rejected note and test added. |
| 6 | High | Phase 1 deploys `workflows: true` before Phase 2 handlers exist; coupling was prose only. | Escalated -- see Open decision A; interim "do not deploy alone" exit criterion added. |
| 7 | Medium | `failed` status unreachable; SC-4 requires it; loop/branch child-id behaviour unspecified. | Fixed -- `failed` sourced from probed field or branch deleted; probe records loop/branch behaviour. |
| 8 | Medium | Waiting children reported as Working; override masked `errored`; web.py sites run in a worker thread. | Fixed -- tri-state `workflow_state`, loop-side snapshot, override applied after verdict, idle-only upgrade. |
| 9 | Medium | Idle sweeper (`_sweepable`) can close a parent mid-workflow. | Fixed -- `_sweepable` refuses sessions with fresh children; test added. |
| 10 | Medium | Turn-end `finally` marks live workflow children done; `_workflow_children` removal was conditional on crew entry. | Fixed -- children exempt from sweep; removal unconditional. |
| 11 | Medium | `_load_cli_settings_dict` lacks `utf-8-sig` and dict guard; non-dict JSON fails every session create; `bool("false")` is True. | Fixed -- guarded reader mirroring `_kiro_tool_search_settings`; `is True` for workflows; tests. |
| 12 | Medium | Part B wiring site wrong (`_session_is_live` is a bool; `_acp_status_for_held` is held-only); `_LIVE_MTIME_WINDOW` lives in web.py; `_find_v3_session_dir` may return None. | Fixed -- non-held branch named by contract; None guard; cost measurement required. |
| 13 | Medium | Router returns before the INFO fallthrough, removing the documented evidence log. | Fixed -- INFO logging requirement for all workflow frames. |
| 14 | Medium | SC-1 observable (`available_commands_update`) may not carry the tool inventory. | Fixed -- Probe C added before the live criteria. |
| 15 | Medium | Test-file location and `-k` selectors ambiguous; node test not in exit criteria. | Fixed -- existing test files, fixed name stems, node-test criterion. |
| 16 | Medium | Section 8, File scopes and exit criteria disagreed; `_publish_live` note documented a change the plan rejects. | Fixed -- scopes aligned; note rewritten as "intentionally unchanged". |
| 17 | Low | Duplicate classify_kiro_v3 bullet; stale line numbers (~35 lines drift); `order=len(crew)` collides after eviction. | Fixed -- deduped; anchor by name; monotonic order counter. |
| 18 | Low | `workflowNotifications` forwarded though KAS ignores it (YAGNI against the plan's own "productized support" rule). | Escalated -- see Open decision C. |

**Decisions (resolved by the user 2026-10-08, folded into the Design Decisions table and Phase 1)**: A = option 2 (constant gate); B = accept default-on settings and the absent-key `workflows: false`; C = drop `workflowNotifications`. Original options kept below for the record.

- **A. Phase 1 / Phase 2 gating.** Options: (1) keep as now, "do not restart onto Phase 1 alone" as a commit-body note; (2) emit `workflows`/`goal`/`workflowNotifications` only behind a module constant flipped in Phase 2; (3) merge Phases 1 and 2 into one phase. Property the choice must preserve: no deployable build forwards `workflows.enabled: true` without the handlers. Recommendation: option 2, because a restart between phases is likely and the user, not the plan, performs it.
- **B. Default-on settings cost.** `thinking`, `knowledge`, `codeIntelligence`, `largeToolOutputHandler` change from KAS-off to on (TUI defaults) for every PA session, which affects token use and indexing; Section 4 says "Cost impact: None". Confirm this is wanted, or forward only keys explicitly present in `cli.json`. Also confirm the absent-`chat.enableWorkflows` default (this plan sends `enabled: false`; verify against the TUI's `w1()`).
- **C. `workflowNotifications`.** Keep as forward-compat scaffolding, or drop it (KAS ignores it today).

Also noted, unresolved by design: `session/load` applying the settings block (Probe B) can flip an existing session's tool inventory mid-life; the Probe B outcome decides whether `session/load` passes settings at all. No PA-side kill switch for workflows exists beyond setting `chat.enableWorkflows: false` in `cli.json`.

### 2026-10-08 -- Implementation Review (after Phase 1, persona: Senior engineer, Reliability engineer)

Implementation health: Yellow (no High; cycle 2 left 3 Medium resolved by fix or user decision, and 4 Low awaiting a user response).
Cycle 1: 6 Medium, 9 Low (0 High). Cycle 2: 3 Medium, 10 Low (0 High). Both reviewers returned "no blocking issues" in cycle 2; the cycle cap (2) was reached and the user chose the post-cap actions below. qvalidate `phase-count`: 5 ticked, expected 5, pass.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | `largeToolOutputHandler` is not a KAS setting (0 hits in seven bundles) yet was forwarded and documented as a KAS key. | Fixed -- dropped by user decision 2026-10-08; code, tests, docs, plan text updated. |
| 2 | Medium | KNOWLEDGE.md stated inferred probe results as measured; audit grep claim false for `_subagent`. | Fixed -- claims re-scoped to what was measured; audit wording corrected (`080241e`). |
| 3 | Medium | `session/load` sends default-on keys to resumed sessions; effect unmeasured. | User: accepted -- keep sending on load, probe in Phase 2 (user decision 2026-10-08, post-cap prompt). |
| 4 | Medium | Probe A (`toolSearch` placement) not run in the first pass. | Fixed -- run; no observable effect, recorded "not determined", carried to Phase 2. |
| 5 | Medium | KNOWLEDGE.md claimed every deferred key has a ROADMAP entry; false for `_subagent`/`_delegate`; ROADMAP said "Phase 1 done" with criteria deferred. | Fixed -- entries added, wording corrected, stale `workflowNotifications` instruction removed (`bcf434e`). |
| 6 | Medium | The shipped block was never sent to a real agent. | Fixed -- throwaway `session/new` with the shipped block opened cleanly. |
| 7 | Low | Weak logging; no log on load; "sent" before the request. | Fixed -- `{key: enabled}` at INFO on both paths, worded "sending". |
| 8 | Low | Tests read the developer's real `cli.json`; no wire-level test; no directory/UTF-8/recursion cases. | Fixed -- module-wide redirect, `TestSessionSettingsOnTheWire`, new loader cases. |
| 9 | Low | `RecursionError` uncaught in the new reader. | Fixed -- added to the except tuple. |
| 10 | Low | Transient read failure turns an explicit opt-out into "on" (fallback is TUI defaults). | User: accepted -- "1. ok" (user reply, 2026-10-08), documented fallback direction. |
| 11 | Low | Synchronous cli.json read on the event loop per session create/load, unlike neighbouring `to_thread` reads. | User: accepted -- "2. ok" (user reply, 2026-10-08). |
| 12 | Low | `_kiro_tool_search_settings` still catches only `(OSError, ValueError)` (pre-existing, not in this diff). | User: accepted -- "accept" (user reply, 2026-10-08), out of scope, reported only. |
| 13 | Low | No guard test that the `KIRO_CLI_SETTINGS_PATH` redirect is active. | Fixed -- guard test added in `719f010` (user asked "4. fix?"). |
| 14 | Low | Phase 2 must update the gated-off tripwire test when it flips the constant. | Fixed -- deferred criterion added to Phase 2. |

Cycle-2 reviewer claim "c2s is not in the registry" (cycle 1) was wrong: `kp("c2s")` is in the 2.28.0 registry (25 `kp` keys, 24 off plus `semanticReview` on); docs corrected.

## Harness Improvement Opportunities

- `/qplan` review sub-agents cannot see the TUI-only evidence a plan's probe depends on (a TUI workflow run is absent from PA's own log) — cost: the first-round "mandatory probe" was infeasible as written and survived a full review — suggested change: add to the Spawn brief contract a check that every probe in a plan names an evidence source the implementer can actually reach.

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
