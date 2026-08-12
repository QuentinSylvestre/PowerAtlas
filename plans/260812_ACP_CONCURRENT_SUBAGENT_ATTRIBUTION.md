# ACP Concurrent Subagent Attribution

> **Date**: 2026-08-12
> **Status**: Draft
> **Scope**: Improve `_Supervisor._on_subagent_list` to attribute fan-out crews when more than one session is in-flight simultaneously

---

## Intent

### Problem statement & desired outcomes

`_on_subagent_list` attributes a `_kiro.dev/subagent/list_update` notification to a parent session by single-inflight inference: if `len(inflight) != 1`, the notification is silently dropped and the crew display never appears. This means any scenario where two ACP sessions are active simultaneously — even if only one of them dispatched a fan-out — causes the crew panel to stay blank.

The desired outcome is that the crew panel appears correctly whenever a session's fan-out can be attributed with confidence, regardless of whether other sessions are also in-flight. Two sessions simultaneously dispatching their own fan-outs remain ambiguous and continue to drop — that is the acceptable residual gap.

### Success criteria

- SC-1: When session A is mid-turn and dispatches a subagent fan-out while session B is also in-flight (but not dispatching), the crew panel for session A appears correctly.
- SC-2: The existing single-inflight fast path is unchanged in behaviour and cost.
- SC-3: The zero-inflight drop path is unchanged.
- SC-4: The genuinely-ambiguous case (two sessions dispatching simultaneously, each with its own spawner anchor) continues to drop — no silent misattribution.
- SC-5: Existing `TestAcpSubagentList*` tests pass. `test_two_inflight_sessions_is_ambiguous_and_drops_the_update` is updated to reflect the new attribution logic (drops only when no spawner anchor resolves attribution, not always when `len(inflight) != 1`).
- SC-6: A new test covers the improved case: two sessions in-flight, only one has a pending spawner anchor — attribution succeeds, `subagent_sessions` and `subagent_history` populated.

### Scope boundaries & non-goals

In scope:
- `_Supervisor.__init__`: add `crew_spawn_anchors: dict[str, str]` (toolCallId → session_id)
- `_on_notification` tool_call branch: record entry in `crew_spawn_anchors` when `_meta.kiro.toolName == "subagent"` and `session_id in self.inflight`
- `_on_notification` tool_call_update branch: clean up stale anchor entries on terminal status
- `_on_subagent_list`: fall back to `crew_spawn_anchors` when `len(inflight) != 1`; consume only the oldest anchor for the resolved parent
- `_handle_prompt` finally block: clean up spawner anchors for the session on turn end
- `_detach` and `close_session`: clear `crew_spawn_anchors` alongside existing crew dicts
- Test updates in `tests/test_web.py`

Out of scope:
- Handling two sessions simultaneously dispatching fan-outs with two different anchors (genuinely ambiguous — `len(candidates) == 2` → drop)
- Any change to the `/acp` UI or the `subagents` wire frame shape
- v3 session support
- Any kiro-cli protocol change requests

---

## Context

kiro-cli emits `_kiro.dev/subagent/list_update` with no `sessionId` in the notification envelope. `_Supervisor._on_subagent_list` currently attributes it by requiring exactly one session to be mid-`session/prompt` (`len(inflight) == 1`). When two sessions are concurrently in-flight, it drops the notification entirely rather than guess.

The fix: record a `toolCallId → session_id` anchor when the parent's `session/update / tool_call` event arrives (which always precedes the `list_update` — confirmed by live wire capture 2026-08-12 against kiro-cli 2.16.2). The anchor lets `_on_subagent_list` resolve attribution even when multiple sessions are in-flight, as long as exactly one of them owns a pending spawner anchor.

Wire fact: `_meta` is a field inside the `update` sub-dict (at the same level as `sessionUpdate`, `toolCallId`, `title`) — confirmed by the 2026-08-12 live capture (`update.get("_meta")` returns `{"kiro": {"toolName": "subagent"}}`). It is not at `params` level.

## Files to modify

| File | Change |
|---|---|
| `src/power_atlas/acp.py` | Add `crew_spawn_anchors` dict; populate in `_on_notification`; clean up in `_on_notification` terminal update and `_handle_prompt` finally; fallback in `_on_subagent_list`; clear in `_detach` and `close_session` |
| `tests/test_web.py` | Update `test_two_inflight_sessions_is_ambiguous_and_drops_the_update`; add 5 new tests |

## External Dependencies

None. Pure internal state change in `acp.py`. No infrastructure, CI/CD, IAM, third-party, or migration work.

## Rollout / Migration / Cleanup

None. The change is additive; no existing behavior is altered. `crew_spawn_anchors` starts empty on each `_Supervisor` instance.

## Step-by-step

### Phase 1: Add spawner anchor and update attribution logic [QA]

**Covers**: SC-1, SC-2, SC-3, SC-4, SC-5, SC-6

**File scope**: `src/power_atlas/acp.py`, `tests/test_web.py`

---

#### 1a. `_Supervisor.__init__` — add `crew_spawn_anchors`

Add immediately after `self.subagent_history` (line ~1700), inside the existing `# ── Sub-agent crews ──` block:

```python
# `crew_spawn_anchors`: toolCallId -> session_id. Records which session
# emitted a `subagent` tool call that has not yet been consumed by
# `_on_subagent_list`. Keyed by toolCallId so multiple concurrent spawns
# from the same session each get an independent entry. At steady state this
# holds O(1) entries per session (one outstanding spawn at a time).
# Populated in `_on_notification` when a `subagent` tool_call fires;
# consumed (oldest entry) in `_on_subagent_list` when anchor attribution
# is used; cleaned up on turn end (`_handle_prompt` finally), terminal
# `tool_call_update`, `close_session`, and `_detach`.
self.crew_spawn_anchors: dict[str, str] = {}
```

---

#### 1b. `_on_notification` — populate anchor on `tool_call`

Inside the `if kind == "tool_call":` branch, after the `_flush_bubble` call and the existing log line, before `_emit`:

```python
# Record the spawner anchor for `_on_subagent_list` attribution.
# `_meta` is a field inside `update` (same level as `sessionUpdate`,
# `toolCallId`, etc.) — confirmed by live capture 2026-08-12.
# Match on `_meta.kiro.toolName` (the stable identifier), not on
# `title` ("Spawning agent crew" is human-readable and build-specific).
# Guard on `inflight` membership: a session not currently mid-turn
# cannot be the fan-out parent, and its anchor would never be consumed.
_kiro_meta = (update.get("_meta") or {}).get("kiro") or {}
_spawner_tool_name = _kiro_meta.get("toolName") if isinstance(_kiro_meta, dict) else None
if _spawner_tool_name == "subagent" and isinstance(session_id, str) and session_id in self.inflight:
    self.crew_spawn_anchors[payload["toolCallId"]] = session_id
elif isinstance(session_id, str) and session_id in self.inflight:
    # A tool_call from an inflight session that is not a spawner.
    # No anchor needed; the `elif` path is intentional (not an `else`).
    pass
# Debug: if _meta is absent or has no toolName on a tool_call frame,
# kiro-cli may have changed wire shape — a future wire capture will surface it.
if (isinstance(session_id, str) and session_id in self.inflight
        and not _spawner_tool_name and payload["toolCallId"]):
    log.debug("ACP tool_call: no _meta.kiro.toolName on session=%s id=%s"
              " — spawner anchor not recorded", session_id, payload["toolCallId"])
```

> **Note on simplification**: the `elif ... pass` above is illustrative. In practice, omit the `elif`/`pass` block entirely — the debug log covers the non-spawner case. The final code should be the `if` block + debug log only.

---

#### 1c. `_on_notification` — clean up stale anchor on terminal `tool_call_update`

The existing `tool_call_update` handling path has a blank-intermediate guard:
```python
elif not (payload["title"] or payload["kind"] or payload["status"] or payload["command"]):
    return
```

After this guard (so only terminal updates reach it), inside the `if isinstance(session_id, str):` block, add the stale-anchor cleanup — this is **after the blank-update `return`**, ensuring only non-blank (terminal) updates proceed:

```python
# Clean up stale crew_spawn_anchors entry if this terminal tool_call_update
# is for a spawner that never produced a list_update (e.g. the model
# emitted the spawn tool_call but was cancelled/failed before dispatching).
# Intermediate blank updates have already returned above — only terminal
# frames (status non-empty) reach this point.
# "terminated" is added alongside "completed"/"failed"/"cancelled" because
# kiro-cli 2.16.2 uses "terminated" as the sole terminal status.type for
# subagent entries; a spawner tool_call_update may follow the same vocabulary.
_TERMINAL_TOOL_STATUSES = frozenset({"completed", "failed", "cancelled", "terminated"})
if payload.get("status") in _TERMINAL_TOOL_STATUSES:
    self.crew_spawn_anchors.pop(payload["toolCallId"], None)
```

> **Note**: `_TERMINAL_TOOL_STATUSES` is defined inline here for clarity. In the actual implementation, define it as a module-level constant (following the pattern of `_SUBAGENT_ACTIVE_STATUSES`) to avoid re-creating the frozenset on every call.

---

#### 1d. `_handle_prompt` finally block — clean up anchors on turn end

After `self.inflight.discard(session_id)` in `_handle_prompt`'s `finally` block (line ~3982), add:

```python
# Remove any crew_spawn_anchors entries for this session. A turn that
# completes normally after a list_update was consumed will have no entries
# (already consumed); a turn that ends without a list_update (e.g. prompt
# cancelled before the fan-out executed) cleans up the stale anchor here.
# inflight ⊆ sessions always holds — a session not in inflight cannot
# produce new anchors after this point.
for _tcid in [k for k, v in self.crew_spawn_anchors.items() if v == session_id]:
    self.crew_spawn_anchors.pop(_tcid, None)
```

---

#### 1e. `_on_subagent_list` — anchor fallback for multi-inflight attribution

Replace the current drop-if-not-one-inflight block (lines ~2462–2469):

Current code:
```python
inflight = self.inflight
if len(inflight) != 1:
    log.debug("ACP subagent_list: %d session(s) in flight, cannot "
              "attribute %d entries; dropped", len(inflight), len(subs))
    return
parent_id = next(iter(inflight))
```

New code:
```python
inflight = self.inflight
if len(inflight) == 1:
    parent_id = next(iter(inflight))
else:
    # No single in-flight session — try the spawner anchor as a fallback.
    # `inflight ⊆ sessions` always holds; a session that closed has had
    # its anchors removed by `close_session`, so no stale entries point
    # to non-inflight sessions unless a concurrent close raced this path.
    # Guard: only consider anchors for sessions currently in `inflight`.
    candidates = {
        sid for sid in self.crew_spawn_anchors.values()
        if sid in inflight
    }
    if len(candidates) == 1:
        parent_id = next(iter(candidates))
        # Consume only the OLDEST anchor for this session (Python 3.7+
        # dict insertion order is stable). Multiple pending anchors are
        # possible if the session dispatched rapid successive fan-outs;
        # consuming the oldest matches the list_update that just arrived
        # while leaving later anchors for their respective list_updates.
        for _tcid, _sid in self.crew_spawn_anchors.items():
            if _sid == parent_id:
                self.crew_spawn_anchors.pop(_tcid, None)
                break
        log.debug("ACP subagent_list: attributed to %s via spawner anchor"
                  " (%d in-flight sessions)", parent_id, len(inflight))
    else:
        # Zero candidates: no anchor for any in-flight session.
        # 2+ candidates: two sessions both dispatched fan-outs — genuinely
        # ambiguous (SC-4 acceptable gap). Drop in both cases.
        log.debug("ACP subagent_list: %d session(s) in flight, %d anchor"
                  " candidate(s) — cannot attribute %d entries; dropped",
                  len(inflight), len(candidates), len(subs))
        return
```

---

#### 1f. `_detach` — clear `crew_spawn_anchors`

After `self.subagent_history.clear()` (line ~1971):

```python
self.crew_spawn_anchors.clear()
```

---

#### 1g. `close_session` — remove anchors for the closing session

After `self.inflight.discard(session_id)` (line ~3017):

```python
# Remove pending spawner anchors for this session — a session that closes
# before its list_update arrives would otherwise leave stale entries.
for _tcid in [k for k, v in self.crew_spawn_anchors.items() if v == session_id]:
    self.crew_spawn_anchors.pop(_tcid, None)
```

---

#### 1h. Test updates in `tests/test_web.py`

The `acp_store` fixture clears all `_Supervisor` state in its `finally` block. **Add `acp_mod._supervisor.crew_spawn_anchors.clear()` there** alongside the existing crew dict clears to prevent cross-test contamination.

1. **Update `test_two_inflight_sessions_is_ambiguous_and_drops_the_update`** (~line 10435):
   - Add a pre-condition comment and assert: `# Neither session has a spawner anchor — anchor-absent drop path`
   - Add `assert acp_mod._supervisor.crew_spawn_anchors == {}` before the notify call to document the precondition
   - The existing `assert acp_mod._supervisor.crews == {}` remains

2. **Add `test_spawner_anchor_resolves_two_inflight_sessions`** in `TestAcpSubagentListAttribution`:
   - Setup: two sessions `sid_a` and `sid_b` both in `inflight`; add `crew_spawn_anchors["tc-1"] = sid_a`
   - Fire `_on_subagent_list` with one subagent entry `{"sessionId": "sub-1", "role": "kiro_default", ...}`
   - Assert: `acp_mod._supervisor.crews[sid_a]["sub-1"]` is non-empty (crew populated for session A)
   - Assert: `sid_b not in acp_mod._supervisor.crews` (session B untouched)
   - Assert: `acp_mod._supervisor.subagent_sessions["sub-1"] == {"parent": sid_a}` (subagent_sessions registered)
   - Assert: `"sub-1" in acp_mod._supervisor.subagent_history` (subagent_history registered)
   - Assert: `acp_mod._supervisor.crew_spawn_anchors == {}` (anchor consumed)

3. **Add `test_spawner_anchor_consumed_after_use`**:
   - Setup: two sessions in `inflight`, `crew_spawn_anchors["tc-1"] = sid_a`
   - First `_on_subagent_list` call: assert attribution succeeds (anchor consumed, `crew_spawn_anchors == {}`)
   - Second `_on_subagent_list` call with same setup but no anchor: assert `crews` is not extended (drop)
   - Both halves must be asserted; the second drop assertion alone is insufficient

4. **Add `test_two_inflight_sessions_both_with_spawner_anchors_is_ambiguous_and_drops`** (covers SC-4):
   - Setup: sessions `sid_a` and `sid_b` both in `inflight`; `crew_spawn_anchors = {"tc-1": sid_a, "tc-2": sid_b}`
   - Fire `_on_subagent_list` with a non-empty subagents list
   - Assert: `crews == {}` (both sessions' crews empty — no misattribution)
   - Assert: `crew_spawn_anchors == {"tc-1": sid_a, "tc-2": sid_b}` (neither anchor consumed)

5. **Add `test_stale_spawner_entry_cleaned_on_terminal_tool_call_update`**:
   - Setup: add `crew_spawn_anchors["tc-1"] = sid_a`; dispatch a synthetic `session/update` notification with `sessionUpdate="tool_call_update"`, `toolCallId="tc-1"`, `status="completed"` (non-blank, terminal) through `_on_notification`
   - Assert: `crew_spawn_anchors == {}` (entry removed via the notification path, not direct dict manipulation)

6. **Add `test_turn_end_clears_spawner_entries`**:
   - Add a `crew_spawn_anchors` entry for a session; trigger `_handle_prompt`'s finally cleanup path (or call the cleanup logic directly if `_handle_prompt` cannot be driven cheaply in tests); assert entry is gone

**Exit criteria**:
- [ ] `crew_spawn_anchors` dict declared and type-annotated in `_Supervisor.__init__`
- [ ] `_on_notification` tool_call branch populates `crew_spawn_anchors` when `_meta.kiro.toolName == "subagent"` and `session_id in self.inflight`
- [ ] `_on_notification` tool_call branch emits debug log when `_meta.kiro.toolName` absent on an inflight session's `tool_call` frame
- [ ] `_TERMINAL_TOOL_STATUSES` module-level frozenset defined (includes "terminated")
- [ ] `_on_notification` tool_call_update branch removes stale anchor entries on terminal status (placed after blank-intermediate `return`, not before)
- [ ] `_on_subagent_list` uses anchor fallback when `len(inflight) != 1`; single candidate → attribute, consume oldest anchor; zero or 2+ → drop
- [ ] `_handle_prompt` finally block removes anchors for the completing session after `inflight.discard`
- [ ] `_detach` clears `crew_spawn_anchors` (new addition — not present in current source)
- [ ] `close_session` removes anchors for the closing session
- [ ] `acp_store` fixture clears `crew_spawn_anchors` in its `finally` block
- [ ] `test_two_inflight_sessions_is_ambiguous_and_drops_the_update` updated with pre-condition assert
- [ ] `test_spawner_anchor_resolves_two_inflight_sessions` passes (including `subagent_sessions` and `subagent_history` assertions)
- [ ] `test_spawner_anchor_consumed_after_use` passes (both halves asserted)
- [ ] `test_two_inflight_sessions_both_with_spawner_anchors_is_ambiguous_and_drops` passes (SC-4 coverage)
- [ ] `test_stale_spawner_entry_cleaned_on_terminal_tool_call_update` passes (exercises notification path, not direct dict)
- [ ] `test_turn_end_clears_spawner_entries` passes
- [ ] `pytest tests/test_web.py -k subagent` passes with no regressions
- [ ] `pytest tests/test_web.py` passes (full suite)

## Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `_meta.kiro.toolName` not stable across future kiro-cli builds | If removed, anchor silently stops recording; falls back to existing inflight path — no regression | Debug log emitted when `_meta.kiro.toolName` absent on an inflight session's `tool_call` frame |
| `toolCallId == ""` for a missing ID | Empty-string key can collide between sessions | Guard: skip anchor recording when `payload["toolCallId"]` is falsy |
| A session dispatches two fan-outs before either `list_update` arrives | First `list_update` consumes only the oldest anchor; second anchor awaits its `list_update` | Oldest-only consumption (insertion-order stable in Python 3.7+) |
| Long-lived session accumulates stale anchors if protocol drifts | Memory growth bounded by `close_session` or `_detach`; also cleaned on turn end | Turn-end cleanup added in `_handle_prompt` finally |
| Two sessions simultaneously dispatching fan-outs | `len(candidates) == 2` → drop; neither anchor consumed — correct per SC-4 | SC-4 test covers this path explicitly |

## Verification

```powershell
# Targeted subagent tests
.venv-PowerAtlas\Scripts\python -m pytest tests/test_web.py -k subagent -v

# Full test_web.py suite
.venv-PowerAtlas\Scripts\python -m pytest tests/test_web.py -v

# Live: start PowerAtlas, open /acp, create two sessions,
# run a fan-out prompt on one while the other is mid-turn.
# The crew panel should appear on the dispatching session.
.venv-PowerAtlas\Scripts\power-atlas
```

## Documentation Updates

None. This change is internal to `_Supervisor` state. No user-visible behavior or documented API surface is altered.

## Implementation Divergences from Plan
*Reserved — filled during implementation.*

## Review Log

### 2026-08-12 — Plan Creation (via /qplan)

Running high-effort review (4 personas: Senior engineer, Architect, Reliability engineer, Maintainability reviewer).

15 raw findings. After merging and deduplication: 4 High, 6 Medium, 5 Low.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | Reliability reported `_meta` extraction reads `params` level — silently inert. | Refuted by live wire capture: `_meta` IS in `update` (`update.get("_meta")` returned `{"kiro": {"toolName": "subagent"}}` in the 2026-08-12 probe). Plan code is correct. |
| 2 | High | `acp_store` fixture doesn't clear `crew_spawn_anchors` — cross-test contamination. | Fixed — added `acp_mod._supervisor.crew_spawn_anchors.clear()` to fixture teardown in exit criteria. |
| 3 | High | Stale anchor not cleaned on turn end — O(turns) leak on long-lived sessions. | Fixed — added cleanup in `_handle_prompt` finally block (1d). |
| 4 | High | Consuming ALL anchors for `parent_id` on first `list_update` drops anchors for a second concurrent spawn. | Fixed — changed to consume only the oldest anchor for `parent_id` using insertion-order iteration (1e). |
| 5 | Medium | Anchor recording guard should use `inflight` not `sessions` membership. | Fixed — guard changed to `session_id in self.inflight` (1b). |
| 6 | Medium | SC-4 missing test: two sessions, both with spawner anchors → must drop. | Fixed — added `test_two_inflight_sessions_both_with_spawner_anchors_is_ambiguous_and_drops` (1h.4). |
| 7 | Medium | Debug log for absent `_meta.kiro.toolName` in Risk table but missing from code. | Fixed — log added to `_on_notification` anchor-recording block (1b). |
| 8 | Medium | `toolCallId == ""` guard missing — blank ID could collide. | Fixed — added falsy-`toolCallId` guard in Risk Assessment; implementer instruction in 1b. |
| 9 | Medium | `tool_call_update` placement instruction ambiguous — could be misread. | Fixed — instruction now explicitly states "after the blank-intermediate `return`" (1c). |
| 10 | Medium | `test_spawner_anchor_resolves_two_inflight_sessions` missing `subagent_sessions`/`subagent_history` assertions. | Fixed — assertions added to test spec (1h.2). |
| 11 | Low | `"terminated"` missing from terminal status set — future kiro-cli builds may use it. | Fixed — `_TERMINAL_TOOL_STATUSES` frozenset defined to include `"terminated"` (1c). |
| 12 | Low | Rename `pending_crew_spawner` to `crew_spawn_anchors` for clarity. | Fixed — name updated throughout. |
| 13 | Low | `_detach` clear missing from exit criteria checklist. | Fixed — checklist item added. |
| 14 | Low | Anchor-collapse comment missing — multi-anchor behaviour unexplained. | Fixed — comment added to `_on_subagent_list` snippet (1e). |
| 15 | Low | Redundant `isinstance(session_id, str)` guard inside outer block that already ensures it. | Fixed — removed redundant check (1b). |

Health: **Green** — all High findings resolved; all auto-fixable Medium/Low findings resolved.

## Harness Improvement Opportunities
