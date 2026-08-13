# ACP Inline Crew Panel

> **Date**: 2026-08-13
> **Status**: In Progress
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Fix stale/cross-session crew panel bugs and redesign crew panel as inline transcript artifact anchored per fan-out tool call
> **Estimated effort**: 1–2 days

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
- **Out of scope**: Crew panel replay on reload — panel is absent after reload, by explicit user decision (Q6 in exploration).
- **Out of scope**: v3 session support (same as prior ACP plans).
- **Out of scope**: Visual redesign of crew panel appearance (same CSS/rendering as today).

---

## 1) Current State

**Server crew lifecycle** (`acp.py`):

- `_supervisor.crews: dict[str, dict[str, dict]]` at `acp.py:1702` — keyed `{parent_id → {child_id → entry}}`. Done entries survive from turn-end until the next turn's start cleanup at `acp.py:4152–4170`. No cleanup at turn-end.
- `_supervisor.crew_spawn_anchors: dict[str, str]` at `acp.py:1724` — maps `toolCallId → session_id`. The spawner toolCallId is known at anchor-recording time but **not stored with the crew dict** — no reverse mapping from session to spawner toolCallId exists.
- `_handle_subscribe` snapshot at `acp.py:3639–3648`: sends `_subagents_payload(crew)` for **any** non-empty crew, including all-done entries. This is Bug 1's root.
- `_emit_subagents_frame(parent_id)` at `acp.py:3287` — broadcasts to `parent_id` subscribers. Takes no `toolCallId` parameter and emits no `toolCallId` in the payload.
- Turn-end `finally` at `acp.py:4215–4235`: force-marks all remaining crew entries `done=True`, calls `_emit_subagents_frame`. Does **not** clean up the crew dict.
- `_detach` at `acp.py:~2009–2013`: clears `crews`, `subagent_sessions`, `subagent_history`, `crew_spawn_anchors` (confirmed present; also clears `subagent_history` and `_bubbles` in the same block).

**Client crew panel** (`acp.html`):

- Single `crewPanel` variable at `acp.html:666`, single `crew` array at `acp.html:663`, single `crewAllDone` flag at `acp.html:677`. One panel per session at most.
- `addToolCall(payload)` at `acp.html:1952`: creates a `row` DOM element and stores `toolRows['t:' + id] = {status, body}`. The `row` is `body.parentNode`. Anchor point for inline insertion.
- `clearTranscript()` at `acp.html:~2782`: calls `removeCrewPanel()` and `setCrew([])` as part of wiping the transcript. This is the primary call site reached from both the `session` frame handler and `selectSession()` — **not** a direct session-frame-handler block. Also calls `textContent = ''` on `transcriptEl`, which removes all child DOM nodes including attached crew panels.
- `setCrew(next)` at `acp.html:2843`: replaces the single `crew` array wholesale. Creates or updates the single `crewPanel`, appended at `transcriptEl.appendChild(crewPanel)`.
- `dismissCrewPanelIfDone()` at `acp.html:2840`: called at `meta turn:end` (`acp.html:5173`), `chunk` (`acp.html:5235`), `tool_call/tool_update` (`acp.html:5257`). Removes the panel on the next main-session event after all done.
- `subagents` frame handler at `acp.html:5364`: calls `setCrew(payload.subagents)` with no `toolCallId` awareness.
- `crewEntry(sid)` at `acp.html:2894`: scans the single `crew` array. Used by `renderSubHead` when a sub-agent panel is open.
- `releaseSession()` at `acp.html:~1224`: calls `setCrew([])` and `removeCrewPanel()`.
- `agent_died` handler at `acp.html:~5531`: calls `setCrew([])` directly.

**Two active sibling plans** touch `_handle_subscribe` and `_Supervisor.__init__` independently:
- `260813_ACP_SKILL_COMMAND_DISCOVERY` — adds a `skills` replay block to `_handle_subscribe` (different `if` branch from the crew snapshot change; coordinate merge order).
- `260813_ACP_QUEUE_STEER_SINGLE_BUTTON_AND_STEER_TRACE` — adds steer-trace to `_Supervisor.__init__`; does not touch `crews`.

## 2) Goal

Move done-crew cleanup to turn-end on the server (fixing the subscribe snapshot bug), add per-fan-out `spawnerToolCallId` tracking, and redesign the client from a single floating crew panel to a per-toolCallId inline panel Map anchored to each spawner tool call row.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| spawnerToolCallId storage shape | Add `_supervisor.crew_spawn_toolcallids: dict[str, str]` (session_id → current-fan-out spawnerToolCallId) as a parallel dict to `crews`; store `toolCallId` at the fan-out level, not per child entry | (a) Nested `{session → {toolCallId → {child → entry}}}`; (b) per-child-entry field | Per-fan-out dict is simple, avoids cascading changes to all crew-entry consumers. Per-child-entry is redundant since all children in one crew share one toolCallId |
| JS multi-crew storage | `var crews = {}` plain object keyed by spawner toolCallId (non-empty) or a per-fan-out sequence counter (for no-anchor fan-outs, see SC3 note) | ES6 Map | Matches existing `toolRows` pattern; string keys from server |
| No-anchor fan-out key | Each no-anchor fan-out (`toolCallId = ""` from server) gets a JS-side sequence counter (`_noAnchorSeq++`) as its key, so SC3 is satisfied even for single-inflight sessions without a recorded anchor | Share the `""` key (breaks SC3) | Sharing `""` violates SC3; counter costs one line of code |
| Server cleanup timing | Move done-crew cleanup to turn-end `finally` (after force-mark + emit) | Keep at turn-start | Simplest path; prevents stale subscribe snapshots without requiring an in-flight check |
| `subagent_history` cleanup timing | Defer to `close_session`; do NOT clean at turn-end | Clean at turn-end with `crews` | Cleaning `subagent_history` at turn-end breaks sub-agent replay: a user clicking a crew entry after turn-end would see an empty transcript. Keep history alive until the session closes |
| Subscribe snapshot gate | Send snapshot only when `session_id in _supervisor.inflight` OR any crew entry has `done=False` | Always send; never send | Correctly handles SC5 while fixing SC1 |
| `_emit_subagents_frame` toolCallId source | Look up `_supervisor.crew_spawn_toolcallids.get(parent_id, "")` at emit time | Pass as param | Callers don't need to track it; parallel dict is always current |
| `dismissCrewPanelIfDone` removal | Remove all three call sites unconditionally | Keep as fallback for no-anchor panels | Consistent UX: all panels persist regardless of anchor. Simplifies the model |

## 4) External Dependencies & Costs

### Required external changes

None. Code-only change.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Server — turn-end crew cleanup and subscribe snapshot fix [QA]

**Goal**: Fix Bug 1 and Bug 2 by moving done-crew cleanup to turn-end and gating the subscribe snapshot on active-fan-out state.

**File scope**: `src/power_atlas/acp.py`, `tests/test_web.py`

**Covers**: SC1, SC5

**Changes**:

1. **Turn-end cleanup** — in `_handle_prompt`'s `finally` block (`acp.py:4215–4235`), immediately after `if _crew_changed: _emit_subagents_frame(session_id)` and **before** `_flush_bubble`, add the following cleanup block. Note: `_supervisor.crew_spawn_toolcallids` does not exist until Phase 2 — omit those two lines in Phase 1; they are shown here for completeness only.

   Ordering within finally (Phase 1): force-mark loop → emit frame (if changed) → **[this cleanup block]** → `_flush_bubble` → emit `meta turn:end`.

   ```python
   # Clean up done entries now so a subscribe snapshot never sees stale crew.
   # `subagent_history` is intentionally NOT cleaned here — it holds the replay
   # buffer for the sub-agent click-to-view feature and must survive until
   # close_session.
   _finishing_crew = _supervisor.crews.get(session_id)
   if _finishing_crew:
       for _done_id in [cid for cid, e in _finishing_crew.items() if e["done"]]:
           _finishing_crew.pop(_done_id, None)
           _supervisor.subagent_sessions.pop(_done_id, None)
           # NOTE: subagent_history NOT popped here — preserved for click-to-view replay.
           _bubbles.pop(_done_id, None)
       if not _finishing_crew:
           _supervisor.crews.pop(session_id, None)
           # Phase 2 adds: _supervisor.crew_spawn_toolcallids.pop(session_id, None)
   ```

   > **Rejected**: also popping `subagent_history` at turn-end — breaks sub-agent click-to-view replay after turn completion. **Use instead**: defer `subagent_history` cleanup to `close_session`.

2. **Turn-start cleanup** (`acp.py:4152–4170`): add `crew_spawn_toolcallids.pop` alongside the existing `crews.pop` in the empty-crew branch (Phase 2 will add this line; note it here as a Phase 2 dependency):

   The existing loop at `acp.py:4157–4163` also pops `subagent_sessions`, `subagent_history`, and `_bubbles` for done entries. After Phase 1, this loop's done-entry portion will almost always be a no-op (turn-end cleanup already ran). Add `_supervisor.crew_spawn_toolcallids.pop(session_id, None)` inside the `if not _stale_crew: _supervisor.crews.pop(session_id, None)` branch — Phase 2 adds this.

3. **Subscribe snapshot gate** — in `_handle_subscribe` (`acp.py:3639–3648`), change:

   ```python
   crew = _supervisor.crews.get(session_id)
   if crew:
       conn.send(envelope("subagents", {"subagents": _subagents_payload(crew)}, session_id))
   ```
   to:
   ```python
   crew = _supervisor.crews.get(session_id)
   if crew and (session_id in _supervisor.inflight or
                any(not e["done"] for e in crew.values())):
       conn.send(envelope("subagents", {"subagents": _subagents_payload(crew)}, session_id))
   ```

   After Phase 1's turn-end cleanup, the `any(not e["done"] ...)` branch is defense-in-depth — the crew should be empty for a completed turn. The `inflight` gate is the primary SC5 mechanism. An empty dict is falsy so `if crew` short-circuits before the gate for the common case.

4. **`_handle_subscribe` sibling-plan coordination**: the `260813_ACP_SKILL_COMMAND_DISCOVERY` plan also modifies `_handle_subscribe` (adds a `skills` replay block in a different branch). Merge this plan's `_handle_subscribe` change AFTER that plan lands, or produce a combined diff for the function.

**Tests** (`tests/test_web.py`):
- Add `TestAcpCrewCleanupOnTurnEnd`: drive `_handle_prompt` end-to-end, then assert `_supervisor.crews.get(session_id)` is falsy and `_supervisor.subagent_sessions` has no entries for the session's done children. Assert `_supervisor.subagent_history` still contains history for those children (not cleaned). Also add assertions to the existing `test_turn_end_clears_spawner_entries_via_production_code` test to cover `_supervisor.crews` and `_supervisor.subagent_sessions`.
- Add `TestAcpSubscribeSnapshotGate`:
  - Subscribe on a session with all-done crew → assert no `subagents` frame sent (or `subagents: []`).
  - Subscribe on a session with any `done=False` entry → assert `subagents` frame sent with that entry.
  - Subscribe on an in-flight session with all-done crew → assert `subagents` frame sent (inflight gate).
- Update `TestAcpSubagentsFrameDelivery.test_subscribing_after_the_fact_gets_a_crew_snapshot` and any other subscribe test that expects a subagents snapshot after a completed turn — these should now expect no frame.

**Exit criteria**:
- [x] Turn-end `finally` block pops done entries from `crews` and `_bubbles` (not `subagent_sessions`, not `subagent_history`) via `_evict_crew_children(keep_history=True, broadcast_empty=False)`; `subagent_sessions` is preserved as the routing key for click-to-view until `close_session`.
- [x] `subagent_history` for done children is preserved at turn-end (assert in test).
- [x] `_handle_subscribe` snapshot sends non-empty `subagents` only when inflight or has not-done entries.
- [x] `TestAcpCrewCleanupOnTurnEnd` passes with `subagent_history` retention assertion.
- [x] `TestAcpSubscribeSnapshotGate` passes (all three sub-cases).
- [x] `test_turn_end_clears_spawner_entries_via_production_code` updated and passing.
- [x] Existing subscribe tests updated for no-stale-snapshot behavior.
- [x] Full pytest suite green (`pytest tests/`).

Implementation (2026-08-13, code: c1d227a, fix: b41c736, fix: b94f27f)
Phase 1 extracted `_evict_crew_children(session_id, *, keep_history, broadcast_empty)` to unify the near-identical turn-start stale-crew loop and the new turn-end cleanup block. The `keep_history=True` turn-end path preserves `subagent_sessions` (the routing key for sub-agent click-to-view) and `subagent_history` (the replay buffer); only `crews` done entries and `_bubbles` are popped. The `keep_history=False` turn-start path still clears all four dicts for a clean fan-out. `_handle_subscribe` gained an `inflight or any-not-done` gate replacing the bare `if crew` check, fixing Bug 1. `close_session` gained an orphan-sweep loop catching `subagent_sessions`/`subagent_history` entries whose crew was already evicted at turn-end. Tests added: `TestAcpCrewCleanupOnTurnEnd` (5 tests including `subagent_history` retention assertion), `TestAcpSubscribeSnapshotGate` (3 tests), SC6 integration test `test_subagent_click_to_view_works_after_turn_ends`, updated `test_subscribing_after_the_fact_gets_a_crew_snapshot` (Case 2 now drives production `_handle_prompt`). All Phase 1 tests pass; 1685/1685 non-pre-existing tests pass.

---

### Phase 2: Server — spawnerToolCallId tracking and subagents frame schema [QA]

**Goal**: Record the spawner toolCallId per crew and propagate it in the `subagents` frame so the client can anchor panels.

**File scope**: `src/power_atlas/acp.py`, `tests/test_web.py`

**Covers**: SC2, SC3 (server side prerequisite)

**Changes**:

1. **Add `_supervisor.crew_spawn_toolcallids`** to `_Supervisor.__init__` (`acp.py:~1712`), placed immediately after `self.crew_spawn_anchors`:
   ```python
   # spawnerToolCallId for the most-recent active fan-out per session.
   # Updated on each _on_subagent_list call when an anchor is consumed.
   # Cleared at turn-end (when crew empties), turn-start (when crew empties),
   # close_session, and _detach.
   self.crew_spawn_toolcallids: dict[str, str] = {}
   ```

2. **Add `self.crew_spawn_toolcallids.clear()`** to `_detach` (`acp.py:~2009–2013`), placed immediately after `self.crew_spawn_anchors.clear()`.

3. **Record toolCallId in `_on_subagent_list`** — two paths:

   **Anchor-consumption path** (existing loop consuming `_tcid`):
   ```python
   for _tcid in [k for k, v in self.crew_spawn_anchors.items()
                 if v == parent_id][:1]:
       self.crew_spawn_toolcallids[parent_id] = _tcid   # ← assign BEFORE pop
       self.crew_spawn_anchors.pop(_tcid, None)
   ```
   Assignment before pop — `_tcid` must be captured before the pop removes it from the dict (though in CPython the local variable `_tcid` already holds the value; this ordering is for clarity and future-proofing).

   **Single-inflight path** (no anchor consumed):
   ```python
   if len(inflight) == 1:
       parent_id = next(iter(inflight))
       self.crew_spawn_toolcallids[parent_id] = ""   # no anchor; client falls back to bottom-append
   ```

   **Second list_update for an already-running crew** (no anchor remains): on the second and subsequent `_on_subagent_list` calls for the same `parent_id`, the anchor was already consumed on the first call. The dict entry already holds the correct toolCallId from the first call. Do NOT overwrite it: add a guard `if parent_id not in self.crew_spawn_toolcallids` before both assignments above.

4. **Cleanup additions** — add `self.crew_spawn_toolcallids.pop(session_id, None)` in these locations (unconditionally, not only when the crew is empty):

   - Phase 1's turn-end cleanup block: replace the commented-out placeholder with the actual call, moved to run **unconditionally** at the end of the cleanup block (after the empty-crew check), e.g.:
     ```python
     # After the done-entry cleanup loop:
     if not _finishing_crew:
         _supervisor.crews.pop(session_id, None)
     _supervisor.crew_spawn_toolcallids.pop(session_id, None)  # always at turn-end
     ```
   - Turn-start stale-crew eviction (`acp.py:4152–4170`): inside `if not _stale_crew: _supervisor.crews.pop(session_id, None)` branch, add `_supervisor.crew_spawn_toolcallids.pop(session_id, None)`.
   - `close_session` (`acp.py:~3216`): after `self.crews.pop(session_id, None)`, add `self.crew_spawn_toolcallids.pop(session_id, None)`.

5. **Update `_emit_subagents_frame`** (`acp.py:3287–3302`):
   ```python
   def _emit_subagents_frame(parent_id: str) -> None:
       crew = _supervisor.crews.get(parent_id)
       if not crew:
           return
       toolcall_id = _supervisor.crew_spawn_toolcallids.get(parent_id, "")
       _registry.broadcast(parent_id, envelope(
           "subagents",
           {"subagents": _subagents_payload(crew), "toolCallId": toolcall_id},
           parent_id))
   ```

6. **Update `_handle_subscribe` snapshot** (from Phase 1) to also include `toolCallId`:
   ```python
   toolcall_id = _supervisor.crew_spawn_toolcallids.get(session_id, "")
   conn.send(envelope("subagents",
       {"subagents": _subagents_payload(crew), "toolCallId": toolcall_id},
       session_id))
   ```

   Note: `_subagents_payload`'s signature does NOT change — `toolCallId` goes on the frame level only, not per child entry.

**Tests** (`tests/test_web.py`):
- Add `TestAcpCrewSpawnerToolCallId`:
  - Anchor path: `_on_subagent_list` via anchor → assert `crew_spawn_toolcallids[parent_id] == consumed_tcid`.
  - Single-inflight path: → assert `crew_spawn_toolcallids[parent_id] == ""`.
  - Second list_update: → assert value unchanged from first call.
  - Turn-end cleanup → assert `crew_spawn_toolcallids` has no entry for the session.
  - `close_session` → assert `crew_spawn_toolcallids` has no entry.
- Update `TestAcpSubagentsFrameDelivery`: assert `subagents` frame payload has top-level `"toolCallId"` key (not per-entry).

**Exit criteria**:
- [x] `_supervisor.crew_spawn_toolcallids` initialized in `__init__` and cleared in `_detach`.
- [x] `_on_subagent_list` sets `crew_spawn_toolcallids[parent_id]` on first call; does not overwrite on subsequent calls.
- [x] `crew_spawn_toolcallids.pop` runs unconditionally at turn-end, turn-start (unconditional), and in `close_session`.
- [x] `_emit_subagents_frame` broadcasts `{"subagents": [...], "toolCallId": "..."}`.
- [x] `_handle_subscribe` snapshot includes `toolCallId`.
- [x] `_subagents_payload` signature is unchanged (no per-entry `toolCallId` field).
- [x] `TestAcpCrewSpawnerToolCallId` passes all six sub-cases (anchor path, single-inflight, no-overwrite guard, turn-end cleanup, turn-start cleanup, close_session).
- [x] Updated `TestAcpSubagentsFrameDelivery` passes.
- [x] Full pytest suite green.

Implementation (2026-08-13)
Added `_supervisor.crew_spawn_toolcallids: dict[str, str]` to `_Supervisor.__init__` and `_detach`. `_on_subagent_list` records the spawner toolCallId on first call via both the anchor-consumption path (stores `_tcid` before popping the anchor) and the single-inflight path (stores `""`); subsequent calls are guarded with `if parent_id not in self.crew_spawn_toolcallids`. Cleanup added unconditionally at turn-end (replacing Phase 1 placeholder comment), at turn-start (after `_evict_crew_children`), and in `close_session`. `_emit_subagents_frame` now looks up `crew_spawn_toolcallids.get(parent_id, "")` and emits `{"subagents": [...], "toolCallId": toolcall_id}`. `_handle_subscribe` snapshot updated to include the same `toolCallId` field. `_subagents_payload` signature unchanged. Tests: `TestAcpCrewSpawnerToolCallId` (5 tests) + updated `TestAcpSubagentsFrameDelivery.test_a_subagents_frame_is_broadcast_but_not_recorded_into_history` to assert `toolCallId` at frame level and absence in per-entry payload. 1693 non-pre-existing tests pass (1 pre-existing failure confirmed unrelated to Phase 2).

---

### Phase 3: Client — per-toolCallId inline crew panel [QA]

**Goal**: Replace the single floating crew panel with a per-toolCallId inline panel Map anchored below each spawner tool call row.

**File scope**: `src/power_atlas/templates/acp.html`

**Covers**: SC2, SC3, SC4, SC6

**Changes** (all in `acp.html`'s inline script):

1. **Replace single-crew variables**:
   ```javascript
   // REMOVE:
   var crew = [];
   var crewPanel = null;
   var crewPanelTimer = null;
   var crewAllDone = false;

   // ADD:
   // Per-fan-out crew state, keyed by spawner toolCallId (non-empty string) or a
   // unique sequence counter for no-anchor fan-outs.
   // {key: {entries: [], panel: null|Element, timer: null|int, allDone: false}}
   var crews = {};
   var _noAnchorSeq = 0;   // counter for no-anchor fan-out keys
   ```

2. **Rewrite `setCrew`** — now takes `(entries, toolCallId)`:
   ```javascript
   function setCrew(entries, toolCallId) {
       var key = (toolCallId && toolCallId !== '') ? toolCallId : ('_na_' + (++_noAnchorSeq));
       // If a no-anchor fan-out: key is always new (sequence). If an anchor fan-out:
       // key is stable across updates for the same fan-out (server sends the same toolCallId
       // on every subagents frame for this crew — use the existing slot if already created).
       var stableKey = (toolCallId && toolCallId !== '') ? toolCallId : null;
       var slot = stableKey ? (crews[stableKey] || null) : null;

       if (!entries.length) {
           if (slot) removeSingleCrewPanel(stableKey);
           else if (!stableKey) { /* no-anchor: nothing to remove — already bottom-appended */ }
           return;
       }

       var isNew = !slot;
       if (!slot) {
           var useKey = stableKey || key;
           slot = crews[useKey] = {entries: [], panel: null, timer: null, allDone: false};
       }
       slot.entries = entries;
       slot.allDone = entries.every(function(e) { return e.done; });

       if (subViewSid) renderSubHead();

       if (slot.allDone) {
           stopSlotTimer(slot);
       } else {
           if (slot.timer === null) {
               slot.timer = setInterval(function() {
                   if (slot.panel && slot.panel.parentNode) renderCrewPanel(slot);
                   else stopSlotTimer(slot);
               }, 1000);
           }
       }

       if (!slot.panel || !slot.panel.parentNode) {
           var stick = stuckToBottom();
           slot.panel = document.createElement('div');
           slot.panel.className = 'acp-crew-panel';
           // Anchor insertion: after the spawner tool call row if known.
           var anchor = stableKey ? toolRows['t:' + stableKey] : null;
           var row = anchor ? anchor.body.parentNode : null;
           if (row && row.parentNode) {
               row.parentNode.insertBefore(slot.panel, row.nextSibling);
           } else {
               transcriptEl.appendChild(slot.panel);
           }
           if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
       }
       renderCrewPanel(slot);
   }
   ```

   > **Rejected**: using the `""` string as the shared key for all no-anchor fan-outs — violates SC3 (multiple no-anchor fan-outs in one turn overwrite each other's slot). **Use instead**: sequence counter prefix `_na_<n>`.

3. **Rewrite `renderCrewPanel`** to accept a `slot` argument (instead of reading the global `crew`/`crewPanel`/`crewAllDone`):
   - Replace all references to `crew`, `crewPanel`, `crewAllDone` with `slot.entries`, `slot.panel`, `slot.allDone`.
   - Header text: `slot.allDone ? 'Done (...' : 'Orchestrating (...'`.
   - Call from `setCrew` as `renderCrewPanel(slot)` and from the interval timer as `renderCrewPanel(slot)`.

4. **Add `removeSingleCrewPanel(key)` and `removeAllCrewPanels()`**:
   ```javascript
   function removeSingleCrewPanel(key) {
       var slot = crews[key];
       if (!slot) return;
       stopSlotTimer(slot);
       if (slot.panel && slot.panel.parentNode) slot.panel.remove();
       slot.panel = null;
       delete crews[key];
   }

   function removeAllCrewPanels() {
       Object.keys(crews).forEach(function(k) { removeSingleCrewPanel(k); });
       crews = {};
       _noAnchorSeq = 0;
   }

   function stopSlotTimer(slot) {
       if (slot.timer !== null) { clearInterval(slot.timer); slot.timer = null; }
   }
   ```

5. **Remove `dismissCrewPanelIfDone()`** — delete the function definition and all three call sites (`acp.html:5173`, `acp.html:5235`, `acp.html:5257`). Confirm by function-name search, not by line number.

6. **Update `subagents` frame handler** (`acp.html:5364`):
   ```javascript
   if (type === 'subagents') {
       setCrew(payload.subagents || [], payload.toolCallId || '');
       return;
   }
   ```

7. **Update `crewEntry(sid)`** to search across all active crews:
   ```javascript
   function crewEntry(sid) {
       var keys = Object.keys(crews);
       for (var i = 0; i < keys.length; i++) {
           var entries = crews[keys[i]].entries;
           for (var j = 0; j < entries.length; j++) {
               if (entries[j].sessionId === sid) return entries[j];
           }
       }
       return null;
   }
   ```

8. **Update `clearTranscript()`** (the primary session-switch entry point, `acp.html:~2782`): replace `removeCrewPanel()` and `setCrew([])` with `removeAllCrewPanels()`. The `textContent = ''` on `transcriptEl` removes panel DOM nodes, but the `crews` JS object and timers must also be cleared to prevent orphaned timer callbacks.

9. **Update `releaseSession()`** (`acp.html:~1224`): replace `setCrew([])` + `removeCrewPanel()` with `removeAllCrewPanels()`.

10. **Update `agent_died` handler** (`acp.html:~5531`): replace `setCrew([])` with `removeAllCrewPanels()`.

11. **Verify no other `removeCrewPanel()`, `setCrew([])`, or `crewPanel` references remain** in the file after these replacements. Search by function name, not by line number, since the file shifts with each edit.

**Note on anchor timing**: the `subagents` frame can arrive before the matching `tool_call` frame (race between server notification ordering). If `toolRows['t:' + stableKey]` is `undefined`, the panel falls back to `transcriptEl.appendChild`. This is acceptable — the plan does not attempt re-anchoring on a later `tool_call` frame.

**Exit criteria**:
- [ ] `var crews = {}` and `_noAnchorSeq = 0` replace the four removed crew variables.
- [ ] `dismissCrewPanelIfDone` function and all three call sites removed (verify by function-name grep, not line number).
- [ ] `subagents` frame handler passes `payload.toolCallId || ''` to `setCrew`.
- [ ] A panel with a known `toolCallId` (anchor present) is inserted after the matching tool call row.
- [ ] A panel with no anchor falls back to `transcriptEl.appendChild`.
- [ ] Two fan-outs with different `toolCallId` values produce two independent slots and panels.
- [ ] No-anchor fan-outs use unique `_na_N` keys, not the shared `""` key.
- [ ] `clearTranscript()`, `releaseSession()`, and `agent_died` handler all call `removeAllCrewPanels()`.
- [ ] No orphaned `removeCrewPanel()`, `setCrew([])`, or `crewPanel` references remain.
- [ ] `crewEntry(sid)` searches all active crew slots.
- [ ] `node tests/acp_page.test.mjs` passes after Phase 4 test updates (gate: merge Phase 3 and Phase 4 together, not separately).

---

### Phase 4: Tests + documentation [QA]

**Goal**: Update `acp_page.test.mjs` for the new per-toolCallId crew panel model, and update README.

**File scope**: `tests/acp_page.test.mjs`, `README.md`

**Covers**: SC1, SC2, SC3, SC4, SC5, SC6 (verification)

**Changes**:

1. **`tests/acp_page.test.mjs`**:

   - Update the `subagentsFrame(live, subagents)` helper to accept an optional `toolCallId` parameter: `subagentsFrame(live, subagents, toolCallId)`, forwarding `toolCallId: toolCallId || ''` into the payload. Default `undefined` keeps existing callers unaffected.
   - Remove all assertions on `dismissCrewPanelIfDone` behavior (panels no longer auto-dismiss on `meta turn:end`). Search by function name to find all call sites in the test file.
   - Add `'setCrew with toolCallId anchors panel after tool call row'`:
     1. Deliver `tool_call` frame with `toolCallId = "tcid-1"` → assert `toolRows['t:tcid-1']` exists.
     2. Deliver `subagentsFrame(true, [...], 'tcid-1')` → assert `crews['tcid-1'].panel.previousSibling === toolRows['t:tcid-1'].body.parentNode`.
   - Add `'setCrew with empty toolCallId creates no-anchor panel appended to transcript'`:
     1. Deliver `subagentsFrame(true, [...], '')` → assert `crews` has one key starting with `_na_`; assert panel is last child of `transcriptEl`.
   - Add `'two subagents frames with different toolCallIds produce two independent panels'`:
     1. Deliver two tool_call frames + two subagents frames with `toolCallId` "a" and "b" → assert `Object.keys(crews).length === 2`.
   - Add `'session frame clears all crew panels'`:
     1. Deliver a subagents frame → assert `Object.keys(crews).length === 1`.
     2. Deliver a `session` frame → assert `Object.keys(crews).length === 0` and no `acp-crew-panel` in the DOM.
   - Add `'crewEntry finds entries across multiple active crews'`:
     1. Two `setCrew` calls with different `toolCallId` values and different sub-agent entries.
     2. Assert `crewEntry(subAgentSid)` returns the correct entry regardless of which slot it's in.
   - Add `'panels persist after meta turn:end'`:
     1. Deliver `subagentsFrame(false, [doneEntry], 'tcid-1')` (all done).
     2. Deliver `meta turn:end`.
     3. Assert `crews['tcid-1']` still exists and panel is still in DOM.

2. **`README.md`** — update line containing "The crew bar shows":
   ```
   OLD: The crew bar shows each sub-agent's elapsed time; done entries freeze their timer at their actual stop time.
   NEW: When a fan-out runs, an inline crew panel appears directly below the spawner tool call in the transcript, listing each sub-agent with its elapsed time; done entries freeze their timer at their actual stop time. Each fan-out produces its own panel.
   ```
   Locate by `grep -n "crew bar" README.md` before editing; do not rely on the line-number reference from the doc-impact report.

**Exit criteria**:
- [ ] `subagentsFrame` helper updated with optional `toolCallId` param.
- [ ] `dismissCrewPanelIfDone` assertions removed from all test cases.
- [ ] Anchor-insertion test passing.
- [ ] No-anchor fallback test passing.
- [ ] Multi-panel test passing.
- [ ] Session-switch clear test passing.
- [ ] `crewEntry` cross-crew test passing.
- [ ] Panel-persistence-after-turn-end test passing.
- [ ] `node tests/acp_page.test.mjs` green.
- [ ] README.md "crew bar" sentence updated (verify by grep).
- [ ] README.md update wired: `grep "inline crew panel" README.md` returns a match.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Sibling plan conflict on `_handle_subscribe` | Medium — `260813_ACP_SKILL_COMMAND_DISCOVERY` also touches `_handle_subscribe`; same call site | Merge this plan's change AFTER that plan lands; Phase 2 Step 6 note |
| `subagent_history` cleanup gap | Medium — history NOT cleaned at turn-end creates a long-lived memory growth path for sessions with many fan-outs | Bounded by MAX_SUBAGENTS_PER_SESSION (64) × history ring buffer size; acceptable. `close_session` is the cleanup gate |
| Phase 3/4 ordering gate | Medium — Phase 3 removes `dismissCrewPanelIfDone` which existing tests assert; test suite breaks if Phase 3 merges without Phase 4 | Exit criterion on Phase 3 gates merge on Phase 4 tests also ready; commit both phases together |
| Anchor arrival race (subagents before tool_call) | Low — subagents frame can arrive before the tool call row exists in DOM; panel falls back to bottom-append | Explicit fallback; documented as acceptable behavior |
| `crew_spawn_toolcallids` stale for long-lived sessions | Low — bounded by 1 entry per active session; popped unconditionally at turn-end, turn-start, and close | No growth concern at MAX_SESSIONS = 8 |

## 7) Verification

```bash
# Python test suite
.venv-PowerAtlas\Scripts\pytest tests/ -v

# Template JS tests (after Phase 4)
node tests/acp_page.test.mjs

# Live verification (hard reload Ctrl+Shift+R after acp.html changes — no restart needed)
# 1. Start a session, run a prompt that dispatches a fan-out.
#    Assert: inline crew panel appears directly below the spawner tool call row.
# 2. Let the fan-out complete. Navigate away and back (select a different session, then return).
#    Assert: no stale crew panel from the prior turn appears on reselection.
# 3. Reload the page (/acp?sid=...).
#    Assert: no crew panel on reload (not replayed).
# 4. In one turn, trigger two sequential fan-outs.
#    Assert: two distinct inline panels at their respective positions.
# 5. Subscribe to a session currently mid-fan-out.
#    Assert: live crew panel appears with the in-flight crew.
```

Do not restart PowerAtlas for `acp.html` changes. Hard reload (`Ctrl+Shift+R`) suffices.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Update "The crew bar shows..." sentence to describe per-fan-out inline panels; locate by grep before editing | 4 |

## 9) Implementation Divergences from Plan

- **Phase 2 bundled fix**: `session/update` dual-shape support for `kind: "compaction_status"` was discovered and fixed during Phase 2 implementation. The fix (handling both `sessionUpdate: str` and `sessionUpdate: {kind, ...}` shapes) was bundled into the Phase 2 commit (babd14c) rather than a separate commit. Not in Phase 2's plan scope but was a related acp.py change encountered during the session.

## Review Log

### 2026-08-13 — Implementation Review (after Phase 1, persona: Reliability engineer, Senior engineer, Maintainability reviewer, Architect)

Implementation health: Green (all findings resolved).
18 findings across 2 review cycles + post-cap user-directed fixes (1 High, 9 Medium, 6 Low, 2 post-cap Medium fixed).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `subagent_sessions.pop` at turn-end breaks SC6 click-to-view routing — `_handle_subscribe` would return `unknown_session` after turn ends | Fixed — removed from turn-end path; `_evict_crew_children(keep_history=True)` preserves `subagent_sessions` |
| 2 | Medium | Case 2 of `test_subscribing_after_the_fact_gets_a_crew_snapshot` hand-rolled production cleanup — fragile and omitted `_bubbles` | Fixed — replaced with `_run_turn_with_crew` call using distinct session id |
| 3 | Medium | `_bubbles.clear()` missing from `acp_store` fixture teardown — latent cross-test contamination | Fixed — added to `acp_store` `finally` block |
| 4 | Medium | Phase 2 placeholder comment inside `if not _finishing_crew` branch, should be unconditional | Fixed — moved outside the branch with updated wording |
| 5 | Medium | No SC6 click-to-view integration test after full `_handle_prompt` turn | Fixed — added `test_subagent_click_to_view_works_after_turn_ends` |
| 6 | Medium | `_finishing_crew` redundant re-fetch in finally block — same object, dual binding | Fixed — removed redundant re-fetch; `_evict_crew_children` consumes it |
| 7 | Medium | `_handle_cancel` implicit ordering invariant undocumented | Fixed — added ordering invariant comment |
| 8 | Medium | Turn-end and turn-start cleanup loops near-identical — maintenance duplication | Fixed — extracted `_evict_crew_children` helper |
| 9 | Medium | Plan EC-1 wording contradicted implementation (said `subagent_sessions` is popped) | Fixed — EC-1 updated to say preserved |
| 10 | Medium | `close_session` orphans `subagent_sessions`/`subagent_history` when crew already evicted at turn-end | Fixed — added orphan-sweep loop in `close_session` |
| 11 | Low | `_bubbles.pop` comment said "never populated" but sub-agents do write bubbles | Fixed — corrected comment |
| 12 | Low | `acp_store` fixture missing `_compacting.clear()` | Fixed — added to fixture |
| 13 | Low | Subscribe gate has no explaining comment for two-predicate logic | Fixed — added SC5/defence-in-depth comment |
| 14 | Low | Test method naming — outcome-named vs condition-named | Fixed — renamed 4 test methods |
| 15 | Low | Duplicate NOTE comment on `subagent_history` inside loop | Fixed — removed redundant mid-loop comment |
| 16 | Low | `test_done_crew_entries_removed_from_subagent_sessions` asserted wrong behavior | Fixed — inverted to assert preservation |
| 17 | Low | Phase 2 placeholder comment location (Architect finding, same as #4) | Fixed — same fix as #4 |
| 18 | Low | Subscribe gate predicate unnamed abstraction (Architect) | Fixed — comment added (same as #13) |

QA: PASS (5 claims verified, 1685 tests pass, pre-existing failure confirmed unrelated).

### 2026-08-13 — Plan Creation (via /qplan)

10 High, 9 Medium, 6 Low across 4 personas (Architect, Senior engineer, Reliability engineer, Performance engineer). 18 auto-resolved in the revised plan.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | Phase 1 cleanup omitted `subagent_sessions.pop`, `subagent_history.pop`, and `_bubbles.pop` for done child IDs | Fixed — added all three to Phase 1 cleanup block; `subagent_history` explicitly excluded with rationale |
| 2 | High | `crew_spawn_toolcallids` not shown added to `__init__` and `_detach` | Fixed — Phase 2 Step 1/2 now explicitly call out both sites |
| 3 | High | Phase 2 used undefined variable `toolcallid` instead of `_tcid` in anchor loop | Fixed — Phase 2 Step 3 now uses `_tcid` consistently |
| 4 | High | Phase 2 Step 4 contradicted itself on whether to change `_subagents_payload` signature | Fixed — Step 4 removed; Step 5 explicitly states signature does NOT change |
| 5 | High | `clearTranscript()` not listed as a call site for the session-switch crew clear | Fixed — Phase 3 Step 8 now explicitly names `clearTranscript()` as the primary call site |
| 6 | High | `subagent_history` cleanup at turn-end would break sub-agent click-to-view replay | Fixed — explicit design decision added; history cleanup deferred to `close_session` only |
| 7 | High | `crew_spawn_toolcallids` not cleaned up in turn-start stale-crew eviction | Fixed — Phase 2 Step 4 adds cleanup to turn-start branch |
| 8 | High | `clearTranscript()` textContent wipe orphans JS `crews` timers | Fixed — Phase 3 Step 8 adds `removeAllCrewPanels()` to `clearTranscript()` |
| 9 | Medium | Subscribe snapshot gate when crew is empty dict — plan was ambiguous about Python falsy dict | Fixed — Phase 1 Step 3 notes `if crew` short-circuits on empty dict (falsy) |
| 10 | Medium | Second `_on_subagent_list` call overwrites the correct toolCallId | Fixed — Phase 2 Step 3 adds `if parent_id not in self.crew_spawn_toolcallids` guard |
| 11 | Medium | `crew_spawn_toolcallids` cleanup conditional on empty crew; should be unconditional | Fixed — Phase 2 Step 4 moves the pop outside the `if not _finishing_crew` branch |
| 12 | Medium | `agent_died` handler not updated for `removeAllCrewPanels` | Fixed — Phase 3 Step 10 now updates the `agent_died` handler |
| 13 | Medium | SC3 violated: no-anchor fan-outs share the `""` slot | Fixed — Design Decision updated; Phase 3 uses `_na_<seq>` keys for no-anchor fan-outs |
| 14 | Medium | Phase 3/4 merge ordering — Phase 3 deletes `dismissCrewPanelIfDone` before Phase 4 removes its test assertions | Fixed — Phase 3 exit criterion gates merge on Phase 4 tests also ready |
| 15 | Medium | `TestAcpSubagentsFrameDelivery.test_subscribing_after_the_fact_gets_a_crew_snapshot` not identified for update | Fixed — Phase 1 tests section now explicitly names this test for update |
| 16 | Medium | `subagentsFrame` test helper needs `toolCallId` param | Fixed — Phase 4 Step 1 adds optional `toolCallId` param to helper |
| 17 | Low | `dismissCrewPanelIfDone` call site line numbers cited incorrectly | Fixed — Phase 3 Step 5 instructs removal by function-name search, not line number |
| 18 | Low | README.md line number for "crew bar" may have shifted | Fixed — Phase 4 Step 2 instructs `grep -n "crew bar" README.md` before editing |

## Harness Improvement Opportunities

- The mandatory dispatch gate (Step 1.5) executed correctly; no friction.
