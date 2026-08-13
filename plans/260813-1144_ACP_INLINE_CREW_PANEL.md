# ACP Inline Crew Panel

> **Date**: 2026-08-13
> **Status**: Exploring
> **Scope**: Fix stale/cross-session crew panel bugs and redesign crew panel as inline transcript artifact anchored per fan-out tool call

---

## Intent

### Problem statement & desired outcomes

The `/acp` crew panel has two bugs and a design gap:

**Bug 1 — Stale crew after reload/resubscribe**: When a session ran a fan-out in a prior turn, navigating to it (or reloading) causes `_handle_subscribe` to send a snapshot of all done crew entries still in `_supervisor.crews`. The client receives `setCrew(done_entries)` and the crew panel reappears, showing agents from a past turn as if they were current.

**Bug 2 — Apparent cross-session contamination**: Navigating from session A to session B may show session B's crew from a previous turn. Because all sessions' past fan-out crew accumulate in `_supervisor.crews` until the next turn's start cleanup, any subscribe to a session with prior crew shows stale agents — making it appear as if another session's agents are present.

**Design gap — single floating panel**: The crew panel is a single `div` appended at the bottom of the transcript, replaced wholesale by each fan-out. The intended design is: each fan-out produces its own inline panel anchored directly below its spawner tool call row, exactly like a custom tool call renderer. Multiple fan-outs per turn or per session each have their own panel, at the right spot in the conversation. The panel persists for the page-session lifetime (no auto-dismiss on turn:end) but is not replayed on reload.

### Success criteria

- SC1: No stale crew panel appears when subscribing to a session whose prior turn had a fan-out — `_handle_subscribe` never sends done crew.
- SC2: Each fan-out produces its own inline crew panel in the transcript, anchored below the `subagent` tool call row that spawned it.
- SC3: Multiple fan-outs in one turn produce multiple distinct inline panels, each at the correct position.
- SC4: The panel persists until the session is navigated away from or the socket closes (no `dismissCrewPanelIfDone` auto-removal on `meta turn:end`).
- SC5: A subscribe while a fan-out is actively in-flight still delivers the live crew panel.
- SC6: All existing sub-agent click-to-view behavior (opening the read-only sub-agent panel) continues to work.

### Scope boundaries & non-goals

- **In scope**: `acp.py` server crew lifecycle + snapshot logic; `acp.html` crew panel DOM model and `subagents` frame handling.
- **Out of scope**: Crew panel replay on reload (panel is absent after reload — no reconstruction needed, by explicit user decision Q6).
- **Out of scope**: v3 session support (same as prior ACP plans).
- **Out of scope**: Visual redesign of crew panel appearance (same CSS/rendering as today).

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### Existing patterns & constraints

- `acp.py:1712` — `_supervisor.subagent_sessions: dict[str, dict]` flat global dict `{child_id → {"parent": parent_id}}`. No parent-isolation; child IDs are kiro-cli UUIDs so collision is astronomically unlikely in practice.
- `acp.py:1702` — `_supervisor.crews: dict[str, dict[str, dict]]` keyed `{parent_id → {child_id → entry}}`. Entries survive with `done=True` from turn-end until next turn's start cleanup at `acp.py:4152–4170`.
- `acp.py:1724–1733` — `_supervisor.crew_spawn_anchors: dict[str, str]` maps `toolCallId → session_id`. The spawner tool call's `toolCallId` is already recorded here but NOT stored on the crew dict itself — the reverse mapping (session → spawner toolCallId) must be added.
- `acp.py:3287–3302` — `_emit_subagents_frame(parent_id)` broadcasts only to sockets attached to `parent_id`. No cross-session broadcast path. Correctly scoped server-side.
- `acp.py:3639–3648` — `_handle_subscribe` snapshot: sends `_subagents_payload(crew)` for ANY non-empty crew, including all-done entries. This is the subscribe bug.
- `acp.py:4152–4170` — Turn-start cleanup: evicts `done=True` entries, broadcasts `setCrew([])` or trimmed snapshot. No cleanup at turn END — done entries linger until next turn start.
- `acp.py:4215–4235` — Turn-end `finally`: force-marks not-done entries `done=True`, calls `_emit_subagents_frame`. Does NOT clean up `crews` dict.
- `acp.html:657–668` — Single `crewPanel` variable, single `crew` array, single `crewAllDone` flag. One panel at a time — must expand to a per-toolCallId map.
- `acp.html:1952–2020` — `addToolCall(payload)`: creates `row` DOM element, stores `toolRows['t:' + id] = {status, body}`. The `row` is `body.parentNode`. Crew panel can be inserted via `row.after(crewPanel)`.
- `acp.html:2840–2891` — `dismissCrewPanelIfDone()` called at `meta turn:end` (L5173), `chunk` (L5235), `tool_call/tool_update` (L5257). All three call sites must be removed.
- `acp.html:5364–5369` — `subagents` frame handler calls `setCrew(payload.subagents)` unconditionally. No `toolCallId` field today.
- `acp.py:382–391` — `SUBAGENT_LIST_METHOD = "_kiro.dev/subagent/list_update"`. Notification carries no `sessionId` — parent attribution via `inflight` or `crew_spawn_anchors`. Confirmed 2026-08-11.
- `AGENTS.md` constraint: never restart PowerAtlas autonomously. ACP HTML changes need only a hard reload (no restart).
- `tests/acp_page.test.mjs` covers template JS; must be updated for crew panel changes. Not part of pytest.

### Risks & mitigations

- **R1 — `subagents` frame schema change**: Adding `toolCallId` to the `subagents` frame is additive — the client can treat `null`/absent as "no anchor, fall back to bottom-append." No breaking change. Existing tests assert the frame payload shape and may need updating.
- **R2 — Multiple crews per session in JS**: Expanding from a single `crew` array to a `Map(toolCallId → {crew, panel, timer, allDone})` touches `setCrew`, `renderCrewPanel`, `dismissCrewPanelIfDone`, `crewEntry`, `crewLabel`, `subagentState`, `openSubagent` and possibly more. The sub-agent panel's `renderSubHead` uses `crewEntry(subViewSid)` which scans the single `crew` array — this must search across all active crews or be given the right crew context.
- **R3 — Server-side spawner toolCallId tracking**: `_supervisor.crews` currently has no field for the spawner `toolCallId`. It must be added: either as a field on the crew dict or as a parallel `crew_spawn_toolcall: dict[str, str]` (session → spawner toolCallId). The latter is simpler and doesn't change the per-entry schema.
- **R4 — Multiple fan-outs per turn**: `crew_spawn_anchors` already supports multiple pending anchors per session (insertion-ordered). `_on_subagent_list` consumes the oldest anchor. The spawner toolCallId stored per-crew must track per-fan-out, not per-session. A `crews_by_spawn: dict[str, dict[str, dict[str, dict]]]` keyed `{session_id → {spawner_toolCallId → {child_id → entry}}}` or a per-crew `"spawnerToolCallId"` field on the crew dict both work.
- **R5 — acp_page.test.mjs coverage**: Current tests for `setCrew`/`dismissCrewPanelIfDone` interaction will need rewriting. The "dismiss on turn:end" path being removed means any test asserting that behavior will fail.
- **R6 — Subscribe snapshot for in-flight fan-out**: SC5 requires the snapshot to include active (not-done) crew. The fix must send crew only when `session_id in _supervisor.inflight` OR when any crew entry is not done — not suppress all crew snapshots unconditionally.

### Resolved decisions

- Q1: Both "prior turn on same session" and "different session's agents" occur. — A: both — Decision: fix covers both patterns.
- Q2: Whether persistence-without-navigation is a bug. — A: not sure — Decision: determined from code; `meta turn:end` fires after the final `subagents` frame in the same turn, so `dismissCrewPanelIfDone` removes the panel correctly in-session without navigation. The persistence bug is specifically the reload/resubscribe path.
- Q3: Multiple fan-outs per turn — show last only or all. — A: all, inline, each anchored to its tool call — Decision: each fan-out produces its own independent inline panel at the correct transcript position.
- Q4: Bug fix only vs inline redesign. — A: both — Decision: A+B together.
- Q5: Panel lifecycle after done. — A: panel persists inline permanently (no auto-dismiss); no reload reconstruction — Decision: drop `dismissCrewPanelIfDone` call sites; panel survives until session switch or page reload.
- Q6: Reload reconstruction. — A: not needed — Decision: `_handle_subscribe` sends crew snapshot only for in-flight/active fan-outs; done crew is never sent.

### Open items

- O1: **Server data structure for per-fan-out spawner toolCallId** — two valid shapes: (a) `crews` changes from `{session → {child → entry}}` to `{session → {spawner_toolCallId → {child → entry}}}` (nested by spawn), or (b) keep current flat shape and add a parallel `crew_spawn_toolcallids: dict[str, str]` (session → most-recent spawner toolCallId, insufficient for multiple fan-outs). Shape (a) is more correct for multiple fan-outs but is a larger refactor of `_on_subagent_list`, `_evict_finished_subagents`, `_note_subagent_action`, `close_session`, `_handle_prompt`, `_subagents_payload`. Shape (b) plus a `toolCallId` field on the per-entry dict is a middle ground. `/qplan` to decide and propose.
- O2: **`crewEntry(subViewSid)` search scope** — currently scans the single `crew` array. With per-toolCallId crews, must search across all active panels or the sub-agent panel header goes stale. Approach to be decided at `/qplan`.

### Recommended approach

**Phase 1 — Server bug fix (SC1, SC5, SC6)**:
- Change `_handle_subscribe` snapshot to send crew only when the session is in-flight (`session_id in _supervisor.inflight`) or any entry is `done=False`. If all entries are done, send `{"subagents": []}` instead.
- Move done-crew cleanup from turn-start to turn-end: after the `finally` force-mark, pop all done entries from `crews`. This keeps the server state clean and makes the subscribe check reliable.

**Phase 2 — Server toolCallId tracking (prerequisite for SC2/SC3)**:
- Record the spawner `toolCallId` when consuming a `crew_spawn_anchors` entry in `_on_subagent_list`. Store it alongside the crew dict (per R4 shape decision from O1).
- Add `toolCallId` field to `_subagents_payload` output and to `_emit_subagents_frame` broadcasts and subscribe snapshots.

**Phase 3 — Client inline panel (SC2, SC3, SC4)**:
- Replace single `crewPanel`/`crew`/`crewAllDone` with a `Map` keyed by `toolCallId`.
- `setCrew(entries, toolCallId)`: find the anchor tool call row (`toolRows['t:' + toolCallId]`), insert panel after it. Fall back to bottom-append if toolCallId absent or row not found.
- Remove all three `dismissCrewPanelIfDone()` call sites.
- On `session` frame (session switch): clear all crew panels.
- `crewEntry(subViewSid)`: search all active crews.

**Phase 4 — Tests**:
- `test_web.py`: update `TestAcpSubagentListAttribution` for new subscribe snapshot behavior; add test that subscribe with all-done crew returns empty `subagents`.
- `acp_page.test.mjs`: rewrite crew panel tests for per-toolCallId map; remove `dismissCrewPanelIfDone` assertions.

### QA environment

- PowerAtlas running on `.venv-PowerAtlas` — `power-atlas` console script or `python -m power_atlas`.
- ACP surface at `http://127.0.0.1:<port>/acp` — hard reload (`Ctrl+Shift+R`) after `acp.html` changes.
- Test suite: `.venv-PowerAtlas\Scripts\pytest tests/` (Python) + `node tests/acp_page.test.mjs` (template JS).
- Live verification: run a session with a fan-out, observe inline panel; navigate away and back, confirm no stale panel; run two sequential fan-outs in one turn, confirm two panels at correct positions.
- AGENTS.md constraint: do not restart PowerAtlas; hard reload suffices for `acp.html` changes.

## Harness Improvement Opportunities

- The mandatory dispatch gate (Step 1.5) executed correctly; no friction.
