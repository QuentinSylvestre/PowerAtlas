# ACP Concurrent Subagent Attribution

> **Date**: 2026-08-12
> **Status**: Exploring
> **Scope**: Improve `_Supervisor._on_subagent_list` to attribute fan-out crews when more than one session is in-flight simultaneously

---

## Intent

### Problem statement & desired outcomes

`_on_subagent_list` attributes a `_kiro.dev/subagent/list_update` notification to a parent session by single-inflight inference: if `len(inflight) != 1`, the notification is silently dropped and the crew display never appears. This means any scenario where two ACP sessions are active simultaneously — even if only one of them dispatched a fan-out — causes the crew panel to stay blank.

The desired outcome is that the crew panel appears correctly whenever a session's fan-out can be attributed with confidence, regardless of whether other sessions are also in-flight. Two sessions simultaneously dispatching their own fan-outs remain ambiguous and continue to drop — that is the acceptable residual gap.

### Success criteria

1. When session A is mid-turn and dispatches a subagent fan-out while session B is also in-flight (but not dispatching), the crew panel for session A appears correctly.
2. The existing single-inflight fast path is unchanged in behaviour and cost.
3. The zero-inflight drop path is unchanged.
4. The genuinely-ambiguous case (two sessions dispatching simultaneously) continues to drop — no silent misattribution.
5. Existing `TestAcpSubagentList*` tests pass. `test_two_inflight_sessions_is_ambiguous_and_drops_the_update` is updated to reflect the new attribution logic (it previously asserted the update was always dropped with two sessions in-flight; the new assertion is: drops only when no spawner anchor resolves the attribution).
6. A new test covers the improved case: two sessions in-flight, only one has a pending spawner anchor — attribution succeeds.

### Scope boundaries & non-goals

In scope:
- `_Supervisor.__init__`: add `pending_crew_spawner: dict[str, str]` (toolCallId → session_id)
- `_on_notification` tool_call branch: record entry in `pending_crew_spawner` when `_meta.kiro.toolName == "subagent"`
- `_on_subagent_list`: fall back to `pending_crew_spawner` when `len(inflight) != 1`
- `_detach` and `close_session`: clear `pending_crew_spawner` alongside existing crew dicts
- Test updates in `tests/test_web.py`

Out of scope:
- Handling two sessions simultaneously dispatching fan-outs (genuinely ambiguous on this protocol — the `list_update` carries no parent sessionId)
- Any change to the `/acp` UI or the `subagents` wire frame shape
- v3 session support (this change is in `acp.py`, which drives v2 ACP)
- Any kiro-cli protocol change requests

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

- **`_Supervisor` crew dict pattern** (`acp.py:1654–1700`): three parallel dicts (`crews`, `subagent_sessions`, `subagent_history`) declared together in a named block. New `pending_crew_spawner` goes immediately after `subagent_history` in the same block. Must be cleared in both `_detach` (`acp.py:~1957`) and `close_session` (`acp.py:~3019`), following the identical pattern of those two teardown paths.
- **`_on_notification` dispatch is `sessionUpdate`-kind-gated, not method-gated** (`acp.py:376–379`): `SUBAGENT_ACTIVITY_METHOD` is never compared against `method` anywhere — all routing is driven by `update.get("sessionUpdate")`. The tool_call branch at line 2672 fires on `kind in ("tool_call", "tool_call_update")` and already has `session_id` (the parent) and the full `update` dict including `_meta`.
- **`_meta.kiro.toolName` is the stable identifier** (measured 2026-08-12, kiro-cli 2.16.2): the parent's `session/update / tool_call` frame carries `_meta: {"kiro": {"toolName": "subagent"}}`. The `title` field on the same frame is `'Spawning agent crew'` — a human-readable label that may change across builds; do not match on it.
- **Wire ordering confirmed** (2026-08-12 live capture): `_kiro.dev/session/update / tool_call_chunk` (same toolCallId, `title='subagent'`) fires before `session/update / tool_call`, which fires before the first non-empty `list_update`. The anchor is always set before `_on_subagent_list` needs it.
- **No title-based branching exists in `acp.py`** — this is the first `_meta`-based branch. Precedent for reading `_meta` does not exist in the file; the dict is available in `update` but currently unused.
- **`inflight` lifecycle** (`acp.py:3943, 3982, 3019, 1957`): `inflight.add` before the first await in `_handle_prompt`; `inflight.discard` in `finally`. The anchor window is: inflight.add → tool_call event → list_update → inflight.discard.
- **Test coverage** (`tests/test_web.py`): `TestAcpSubagentListAttribution` has three tests directly covering the attribution logic. `test_two_inflight_sessions_is_ambiguous_and_drops_the_update` tests the exact case being improved and asserts `crews == {}` — this assertion must be updated.

### 5. Risks & mitigations

- **R1 — `_meta.kiro.toolName` not stable across builds**: if a future kiro-cli removes or renames it, the anchor silently stops recording and `_on_subagent_list` falls back to the existing inflight path. No regression — just no improvement. Mitigation: log at DEBUG when a `tool_call` frame is seen with no `_meta.kiro.toolName` so a future wire capture notices the drift.
- **R2 — `toolCallId` uniqueness**: the anchor dict is keyed by toolCallId. If two concurrent sessions produce the same toolCallId, one would be misattributed. toolCallIds appear to be `tooluse_`-prefixed random strings; assumed globally unique but not formally verified. Mitigation: the collision would produce a wrong attribution, not a crash. Same risk profile as the existing inflight approach, which is wrong on N≥2 with no anchor at all.
- **R3 — `test_two_inflight_sessions_is_ambiguous_and_drops_the_update` asserts the old behaviour**: must update to assert crews == {} only when no spawner anchor is present. Test is at `tests/test_web.py:~10435`.
- **R4 — `close_session` teardown**: must also clear `pending_crew_spawner` entries whose value equals `session_id` (not just those keyed by the session's own toolCallIds). A session that was in-flight when closed may have entries in `pending_crew_spawner` whose key is a toolCallId that never got consumed.
- **R5 — Multi-batch within one turn**: one turn may call `subagent` multiple times. The dict handles this correctly since it's keyed by toolCallId, not by session_id — multiple open entries are fine. Each is consumed when `_on_subagent_list` fires for its crew.

### 6. Resolved decisions

- Q1: What `title` does kiro-cli use for the parent's `tool_call` frame when dispatching a subagent? — A: `'Spawning agent crew'` (human-readable, not stable) — Decision: do not match on `title`; match on `_meta.kiro.toolName == "subagent"` instead. Confirmed by live wire capture 2026-08-12 against kiro-cli 2.16.2.
- Q2 (probe result): What is the ordering of `tool_call` vs `list_update`? — A: `tool_call` always arrives before the first non-empty `list_update` for the same spawn. — Decision: anchor-first approach is safe; the dict entry will always exist when `_on_subagent_list` fires.
- Q3 (probe result): Does `rawInput` carry child session ids? — A: No. `rawInput.keys = ['__tool_use_purpose', 'task', 'stages', 'mode']` — no child sessionId pre-announced in rawInput. — Decision: anchor keyed by toolCallId (not by child sessionId) is correct; child sessionIds are only available in `list_update`.

### 7. Open items

- The `pending_crew_spawner` dict should be cleared of stale entries when a `tool_call_update` with `status='completed'` or `status='failed'` arrives for a toolCallId that is still in the dict (meaning no `list_update` ever consumed it — e.g. the model decided not to actually spawn after emitting the spawn tool_call). This is a cleanup concern, not a correctness concern (a stale entry cannot cause a misattribution for a different spawn, since each has a different toolCallId). Defer to `/qplan` to decide whether to include a `tool_call_update` cleanup path or accept the minor memory leak for the rare "spawn tool_call with no list_update" case.

### 8. Recommended approach

**Anchor dict approach** — add `pending_crew_spawner: dict[str, str]` to `_Supervisor`, keyed `toolCallId → session_id`. Populate it in the `tool_call` branch of `_on_notification` when `update.get("_meta", {}).get("kiro", {}).get("toolName") == "subagent"`. In `_on_subagent_list`, after the existing single-inflight fast path fails (len != 1), try: iterate `pending_crew_spawner.values()` and pick the unique session_id if exactly one entry maps to a session currently in `inflight`; if unique, use it and remove the entry; if ambiguous (two entries for two different in-flight sessions), drop as before.

Clear `pending_crew_spawner` in `_detach` (alongside the existing crew dict clears). In `close_session`, remove all entries whose value equals the closing `session_id`.

Fallback ordering:
1. `len(inflight) == 1` → parent is `next(iter(inflight))` (unchanged fast path)
2. `len(inflight) != 1` and exactly one `pending_crew_spawner` entry resolves to an in-flight session → use that session as parent, consume the entry
3. Otherwise → drop (unchanged)

This is additive: step 1 is identical to the current code, steps 2–3 are new. No existing behaviour changes.

### 9. QA environment

- Run `pytest tests/test_web.py -k subagent -v` — covers all `TestAcpSubagentList*` and `TestAcpSubagentActivity` classes. Under ~5 s on this machine.
- Full suite: `pytest tests/test_web.py` — ~30 s. Run after the change to catch any cross-class regressions.
- Live verification: start PowerAtlas, open `/acp`, create two sessions, run a fan-out prompt on one while the other is mid-turn. The crew panel should appear on the dispatching session.
- PowerAtlas start command: `.venv-PowerAtlas\Scripts\power-atlas` from the repo root.
