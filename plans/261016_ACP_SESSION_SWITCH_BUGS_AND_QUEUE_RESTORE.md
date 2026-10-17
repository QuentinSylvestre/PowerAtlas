# ACP session-switch state bugs and queue-status restore on reconnect

> **Date**: 2026-10-16
> **Status**: Complete
> **Scope**: Fix three session-switch state bugs in acp.html; restore queue-waiting message on reconnect; add visualization handler to the dashboard
> **Memory relied on**: none
> **Estimated effort**: ~4–6 hours

---

## Intent

### Problem statement & desired outcomes

Five concrete bugs, all found by code analysis (no reproduction steps needed):

1. **Steer-pending lock** (`acp.html`): when a Steer send is in-flight and the user clicks a different session in the rail, `_steerPending` is not cleared and `promptInput.disabled`, `sendModeBtn.disabled`, `modeToggle.disabled` are not restored. The new session opens with the composer locked indefinitely — the only recovery is a page reload.

2. **Queued prompt silently discarded** (`acp.html`): `queuedPrompt` and `queuedPromptSession` are not cleared in the rail-click path (`selectSession()`). If the user queued a prompt on session A then switches to session B, the queued text survives the switch. When the user eventually returns to session A and the `meta turn:end` fires during transcript replay, the queued text is discarded silently (the `!replaying` guard at `meta turn:end` suppresses the "session changed" note during replay).

3. **Stale `_transcriptLive`** (`transcript-renderer.js`): `setTranscriptLive()` is not called during `selectSession()` or `clearTranscript()`. If the user switches from a live session (mid-turn) to a new empty session (no history), `_transcriptLive` remains `true`. Any tool calls arriving before the new session's first `meta turn:start` are rendered with the live incremental grouping path instead of the replay path.

4. **Queue-waiting state not restored on reconnect** (`acp.py`): `meta["queued_behind"]` persists server-side but `_handle_subscribe` does not re-send `queue_status`. A client that reconnects while a session is queued behind another session gets `turnActive: true` with no indication of the waiting state. The steer-status line stays blank.

5. **Dashboard never renders visualization cards** (`index.html`): `dashHandle()` has no `visualization` case. Visualization frames reach the dashboard WebSocket (broadcast fans to all subscribers) but are silently discarded. Live dashboard sessions never show viz cards.

Desired outcome: switching sessions in /acp is fully clean; the queue-waiting message survives a page reload; the dashboard renders viz cards for sessions it subscribes to.

### Success criteria

- SC1: Switching sessions in /acp when a Steer is in-flight: `_steerPending` is cleared, `promptInput`, `sendModeBtn`, and `modeToggle` are re-enabled on the new session. The steer text is discarded (not restored — the steer was for the previous session). If a `steer_ack` subsequently arrives for the old session, it is ignored or handled gracefully.
- SC2: Switching sessions in /acp: `queuedPrompt` and `queuedPromptSession` are cleared. The queued text is discarded. No silent loss later.
- SC3: Switching sessions in /acp from a live (mid-turn) session to any other session: `_transcriptLive` is `false` after `clearTranscript()` / `selectSession()` completes.
- SC4: After a page reload or WebSocket reconnect, if the session is queued behind another session, the "Waiting for another session's turn to finish…" message is shown on the steer-status line. Implemented by adding `queued: bool(meta.get("queued_behind"))` to the session frame payload in `_handle_subscribe`, and restoring the message in the session frame handler on both pages.
- SC5: The dashboard `dashHandle()` renders visualization iframe cards (`addVisualizationCard()`) when a `visualization` frame arrives for a live subscribed session.

### Invariants

- `releaseSession()` already clears `_steerPending`, `queuedPrompt`, and `queuedPromptSession` — its behavior must remain unchanged; the Phase 1 fixes add those clears only to `selectSession()`.
- Queued prompts continue to auto-send at `meta turn:end` when the session has not changed — the `selectSession()` clear only discards state for the cross-session case.
- `_transcriptLive` continues to be correctly set by `meta turn:start` (true) and `meta turn:end` / `history` handlers (false) in the normal no-switch path — the `clearTranscript()` addition is a reset guard, not a replacement.
- The live `queue_status` broadcast path (`_set_queued_behind` broadcasting to all subscribers) remains unchanged; Phase 2 adds a one-time restore at subscribe only.
- `addVisualizationCard()` in `acp.html`'s existing visualization handler remains unchanged; Phase 3 adds a new call site in `index.html`'s `dashHandle()` only.

### Scope boundaries & non-goals

**In scope**: the five bugs above.

**Out of scope**:
- Rail or Needs Attention indicator for queued-behind sessions (SC4 is steer-status line only)
- `acp.html` `releaseSession()` path (already clears all these correctly; only `selectSession()` is broken)
- The `_steerPending` fix in `ws.onclose` (stale restore on reconnect — separate from the session-switch path; deferred)
- Anything related to the dashboard session-switch path (`dashCloseIfAbandoned()` already clears all equivalents correctly; no dashboard fix needed for SC1–SC3)

---

## 1) Current State

### Bug 1 — steer-pending lock (`acp.html`, function `selectSession`)

`_steerPending` is declared near the top of the IIFE. It is set in the Steer send handler, cleared in `releaseSession()` and in the `steer_ack` / `steer_sent` handlers. The three disabled flags (`promptInput.disabled`, `sendModeBtn.disabled`, `modeToggle.disabled`) are set alongside it and restored in `releaseSession()` and the steer-ack paths.

`selectSession()` calls `clearTranscript()`, `setTurn(false)`, `setContext(null)`, and a few others — but does NOT touch `_steerPending` or the three disabled fields. The `session` frame handler also calls `clearTranscript()` and does not restore them.

`releaseSession()` (function `releaseSession` in `acp.html`) correctly clears `queuedPrompt`, `queuedPromptSession`, `_steerPending`, and restores all three disabled flags. The bug is confined to the rail-click path through `selectSession()`.

### Bug 2 — queued prompt not cleared (`acp.html`, function `selectSession`)

`queuedPrompt` and `queuedPromptSession` are declared at the top of the IIFE. They are set by the Queue button handler and cleared in `releaseSession()` and in the `meta turn:end` handler.

`selectSession()` does NOT clear them. The `meta turn:end` handler clears them unconditionally (no `!replaying` guard on the clear itself). So if the user switches back to session A and its turn-end was already recorded, the queued text is cleared during replay — silently, because the "session changed" note in that same handler IS guarded by `!replaying`.

Dashboard parity is already correct: `dashCloseIfAbandoned()` in `index.html` already clears `_dashQueuedPrompt` and `_dashQueuedPromptSession`.

### Bug 3 — stale `_transcriptLive` (`transcript-renderer.js`, function `clearTranscript`)

`setTranscriptLive(v)` is defined in `transcript-renderer.js`. It is called from the `meta turn:start` handler (sets true), `meta turn:end` handler (sets false), `history` frame handler (sets false), and `agent_died` handler (sets false). It is NOT called from `clearTranscript()` or `selectSession()`.

`clearTranscript()` resets `agentBody`, `toolRows`, `_toolTimers`, `toolGroup`, `openGroup`, `_autoExpandEdits`, `thinkingRow`, and crew panels — but not `_transcriptLive`. Since `clearTranscript()` is called at session switch (via the `session` frame handler in `acp.html`), at `releaseSession()`, and at history replay time, adding the call there covers all paths.

Adding `setTranscriptLive(false)` to `clearTranscript()` is safe during the normal reconnect-replay sequence: the `session` frame handler calls `clearTranscript()` first, then the `history` frame handler calls `setTranscriptLive(false)` independently — the extra call in `clearTranscript()` is idempotent in that sequence. The call is necessary for the session-switch path where no `history` frame follows (e.g., switching to a session that has already sent all its history).

### Bug 4 — queue_status not restored on reconnect (`acp.py`, function `_handle_subscribe`)

`_set_queued_behind()` stores `meta["queued_behind"]` and broadcasts `queue_status{"waiting": bool}`. `_handle_subscribe` sends a `session` envelope with `turnActive`, `contextPercent`, `cwd`, `created` — but not `queued_behind`. The client's `queue_status` handler on both pages is already guarded against replay (`if (!replaying)`), so it cannot be reached through the normal history replay; the fix requires adding `queued` to the session frame.

### Bug 5 — dashboard viz handler missing (`index.html`, function `dashHandle`)

`dashHandle()` dispatches on `type`. There is no `visualization` case. `addVisualizationCard(url, title)` is defined in `transcript-renderer.js` and loaded by both pages. The function is already called in `acp.html`'s own frame handler; the dashboard needs only the same one-line call.

## 2) Goal

Fix five state-management bugs across three components: three session-switch cleanup gaps in `acp.html` / `transcript-renderer.js` (Phase 1), a reconnect-state omission in `acp.py` (Phase 2), and a missing frame handler in `index.html` (Phase 3).

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Bug decomposition scope | Cover SC1–SC5 in this plan; larger session-fidelity features in sibling plan `261016_SESSION_FIDELITY_WORKFLOW_UI_AND_WF_INTERACTION.md` | Single merged plan | Keeps this plan as a focused bug-fix drop with a shorter review cycle |
| SC4 queue-UI scope | Restore steer-status line text only; no rail indicator or Needs Attention entry | Add rail indicator | Rail indicator is scoped to the sibling plan; steer-status is the live indicator the user sees while queued |
| `_steerPending` clear on session switch | Discard stale steer text silently (no user note) | Restore text to new session's textarea | The steer was authored for the old session; restoring to a different session would be more confusing than discarding. The silent behavior differs from `releaseSession()` intentionally: `releaseSession()` is triggered by an explicit close where the user may not expect failure; `selectSession()` is a deliberate switch. |
| `setTranscriptLive(false)` placement | Add to `clearTranscript()` in `transcript-renderer.js` | Add to `selectSession()` in `acp.html` | `clearTranscript()` is the single canonical reset called by `selectSession()`, `releaseSession()`, and the `session` frame handler — one edit covers all paths, including the dashboard's history-replay path |
| `"queued"` key in `_handle_subscribe` only (not `_handle_new`) | Omit from `_handle_new` | Add to `_handle_new` as well | Newly created sessions cannot be `queued_behind` at creation time; the key would always be `false` and is unnecessary. `_handle_new` should add a comment stating this invariant so a future reader does not add it unnecessarily. |

## 4) External Dependencies & Costs

None. Code-only change; no infrastructure, CI/CD, IAM, data migration, DNS, or third-party service changes.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Three session-switch state fixes [QA]

**Goal**: Clear `_steerPending`, `queuedPrompt`/`queuedPromptSession`, and `_transcriptLive` on session switch.

**Covers**: SC1, SC2, SC3

**File scope**:
- `src/power_atlas/templates/acp.html`
- `src/power_atlas/static/transcript-renderer.js`
- `tests/acp_page.test.mjs`

**Changes**:

In `selectSession()` (function `selectSession` in `acp.html`), before the `clearTranscript()` call, add the following state resets:

```javascript
// SC1: release the in-flight steer lock so the new session's composer is
// interactive. The in-flight steer to the previous session is irrecoverable —
// text is discarded, not restored (the steer was for a different session).
// Note: unlike releaseSession(), selectSession() discards silently (no user
// note) — the steer was abandoned, not failed; releaseSession()'s note is
// for an explicit close where the user may not have expected the steer to fail.
if (_steerPending) {
  promptInput.disabled = false;
  sendModeBtn.disabled = false;
  modeToggle.disabled = false;
}
_steerPending = null;
// SC2: discard any queued prompt from the previous session. Carrying it
// silently to a new session would risk an unintended auto-send.
queuedPrompt = null;
queuedPromptSession = null;
```

> **Sketch** — place these lines after the session-capacity early-return guards and before `clearTranscript()`, mirroring the equivalent clears in `releaseSession()`.

In the `steer_ack` handler (the `if (type === 'steer_ack')` block in `acp.html`'s main frame dispatch), add a guard at the very top of the block, mirroring the dashboard's existing guard (`if (!_dashSteerPending) return;`):

```javascript
// SC1: a late steer_ack for the previous session arrives after
// _steerPending was cleared by selectSession(). Guard so neither path
// (rejection: promptInput.value = _steerPending, or success: disable-clear)
// runs against the new session's composer. Mirrors dashHandle()'s own guard.
if (!_steerPending) return;
```

> **Sketch** — add as the first statement inside the `if (type === 'steer_ack')` block, before the `if (payload && payload.queued === false)` branch.

In `clearTranscript()` (function `clearTranscript` in `transcript-renderer.js`), add at the end of the function body, after `thinkingRow = null` and the crew-panel cleanup calls:

```javascript
// SC3: entering a cleared transcript is never a live state — reset
// so a live session's flag never carries into the next session's first frames.
// Dashboard note: this file is loaded by index.html too; setTranscriptLive(false)
// during dashboard session-switch history replay is correct — same reasoning applies.
setTranscriptLive(false);
```

> **Sketch** — `setTranscriptLive` is defined in the same file; no cross-file import issue. The call is idempotent when `_transcriptLive` is already false. Dashboard path: `clearTranscript()` is called from `renderTranscriptHistory()` before any `meta turn:start` can arrive for the new session, so `setTranscriptLive(false)` is always correct at that call site.

**Tests** in `tests/acp_page.test.mjs`:

- **T1 (SC1)**: Simulate a Steer send in-flight on session A (compositor locked: `promptInput.disabled = true`, Steer mode active). Trigger a rail-click to session B (call through the session-selection path). Assert: (a) `promptInput.disabled === false` on session B's load; (b) subsequently receiving a `steer_ack{queued: false}` frame appends no error message and does not alter `promptInput.value`. Use the existing send-call recorder to verify no unexpected side-effects.
- **T2 (SC2)**: Queue mode: register a queued prompt on session A (simulate Queue button press). Switch to session B. Return to session A WITHOUT any intervening turn:end (so queuedPrompt would still survive if the fix were absent). Deliver a live `meta turn:end` for session A. Assert that `send()` is NOT called with `'prompt'`. Without the fix, queuedPrompt is still session A's text at this point, the session-identity guard does not fire (queuedPromptSession === sessionId === A), and the prompt IS auto-sent; with the fix, the text was cleared at switch time and nothing fires.
- **T3 (SC3)**: Call `setTranscriptLive(true)` on the page. Call `clearTranscript()`. Assert that the most recent entry in the existing `setLiveCalls` recorder (populated by the test harness's `setTranscriptLive` instrumentation, e.g., at `acp_page.test.mjs` around the `setLiveCalls` recorder setup) records `false`.

> **Harness note for T1/T2**: Both tests use behavioral outcomes observable through the page's public DOM state and the test harness's `send()` spy, not direct access to IIFE-scoped variables (`_steerPending`, `queuedPrompt`). T3 uses the existing `setLiveCalls` recorder already present in the harness.

**Exit criteria**:
- [x] `selectSession()` resets `_steerPending`, `promptInput.disabled`, `sendModeBtn.disabled`, `modeToggle.disabled` before calling `clearTranscript()` — verify by reading the function body (SC1)
- [x] `selectSession()` clears `queuedPrompt` and `queuedPromptSession` before calling `clearTranscript()` — verify by reading the function body (SC2)
- [x] `steer_ack` handler has `if (!_steerPending) return;` guard as its first statement — verify by reading the handler (SC1)
- [x] `clearTranscript()` calls `setTranscriptLive(false)` as its last step — verify by reading the function body (SC3)
- [x] Tests T1–T3 exist in `acp_page.test.mjs` and pass: `node tests/acp_page.test.mjs` exits 0
- [x] `releaseSession()` is unmodified — its existing `_steerPending`, `queuedPrompt`, and `queuedPromptSession` resets are still present
- [x] Dashboard path verified: `clearTranscript()` is also called by the dashboard's history replay; `setTranscriptLive(false)` in `clearTranscript()` is correct for that path (no dedicated dashboard test needed — identical reasoning applies)

#### Implementation (2026-10-16, code: 69e916d)
Added SC1/SC2 state resets to `selectSession()` in `acp.html`: clears `_steerPending` and re-enables `promptInput`/`sendModeBtn`/`modeToggle`, clears `queuedPrompt`/`queuedPromptSession`. Added `if (!_steerPending) return;` guard as the first statement of the `steer_ack` handler (SC1). Added `setTranscriptLive(false)` as the last step of `clearTranscript()` in `transcript-renderer.js` (SC3). Added tests T1–T3 covering all three SCs with behavioral assertions; mutation tests confirmed each test catches exactly its target mutation with no collateral failures.

QA (Step 5b): PASS — /acp page loads, no JS console errors, composer buttons enabled. Mutation tests by reviewer verified per-test coverage.

Auto-fixes (cycle 1, Low): dropped inaccurate `sess-w1-s0` session-index label from T1/T2 test comments; removed misplaced "Reset prompt navigation state" comment from bottom of `clearTranscript()`. Applied in-session (purely mechanical comment fixes). Cycle 2 skipped — cycle 1 findings all Low + auto-fixes purely mechanical. Fix commit: 3ebf572.

### Phase 2: Queue-status restore on reconnect [QA]

**Goal**: Re-emit `queued_behind` state in the session frame so a reconnecting client sees the "Waiting…" steer-status message without waiting for a new live `queue_status` broadcast.

**Covers**: SC4

**File scope**:
- `src/power_atlas/acp.py`
- `src/power_atlas/templates/acp.html`
- `src/power_atlas/templates/index.html`
- `tests/test_web.py`
- `tests/acp_page.test.mjs`

**Changes**:

In `_handle_subscribe` (function `_handle_subscribe` in `acp.py`), add `"queued"` to the session-frame dict. The current envelope call sends `sessionId`, `cwd`, `created`, `turnActive`, `contextPercent`; add:

```python
"queued": bool(meta.get("queued_behind")),
```

> **Sketch** — add this key to the existing dict literal in the `conn.send(envelope("session", {...}, session_id))` call inside `_handle_subscribe`, before the closing brace.

In `acp.html`, in the `session` frame handler (the `if (type === 'session')` block in the main frame dispatch function), after the `setSteerStatus('')` call, add:

```javascript
// SC4: restore the waiting indicator if this session was queued when we
// subscribed. Live queue_status frames keep it current afterward.
if (payload.queued) {
  setSteerStatus('Waiting for another session\u2019s turn to finish\u2026');
}
```

> **Sketch** — place immediately after `setSteerStatus('')`, before the tab-title reset that follows in the same `session` block.

In `index.html`, in `dashHandle()`, in the `session` frame arm (the code that runs when `type === 'session'` and `sid === _viewingSid`), after the `setDashSteerStatus('')` call, add:

```javascript
// SC4: restore the waiting indicator on reconnect.
if (payload.queued) {
  setDashSteerStatus('Waiting for another session\u2019s turn to finish\u2026');
}
```

> **Sketch** — place immediately after `setDashSteerStatus('')`, before the `dashSetComposerNote('')` call that follows in the same arm.

**Tests**:

- `tests/test_web.py`: add a test (in `TestAcpReplayOnSubscribe` or a new `TestHandleSubscribeQueuedState` class) that sets `meta["queued_behind"]` on a supervised session, calls `_handle_subscribe`, and asserts the emitted `session` frame payload has `queued == True`. Add a second test with no `queued_behind` set to assert `queued == False`.
- `tests/acp_page.test.mjs`: add tests that fire a `session` frame payload `{turnActive: false, queued: true}` through `acp.html`'s frame handler and assert the steer-status element shows the "Waiting…" string. Add a parallel test through `index.html`'s `dashHandle()`.

**Exit criteria**:
- [x] `_handle_subscribe` in `acp.py` emits `"queued": True` in the `session` frame when `meta["queued_behind"]` is set (SC4)
- [x] `_handle_subscribe` emits `"queued": False` when `meta["queued_behind"]` is absent or `None` (SC4)
- [x] `_handle_new` in `acp.py` has a comment noting that it omits `"queued"` because new sessions cannot be `queued_behind` (documentation only; no code change in `_handle_new`) (SC4)
- [x] `acp.html` session frame handler shows "Waiting…" steer-status text when `payload.queued` is `true` (SC4)
- [x] `index.html` `dashHandle()` session frame arm shows "Waiting…" `setDashSteerStatus` text when `payload.queued` is `true` (SC4)
- [x] New tests in `test_web.py` pass: `pytest tests/test_web.py --timeout=300` exits 0
- [x] New tests in `acp_page.test.mjs` pass: `node tests/acp_page.test.mjs` exits 0
- [x] `_set_queued_behind` and the live `queue_status` broadcast path are unmodified

#### Implementation (2026-10-16, code: 638994b)
Added `"queued": bool(meta.get("queued_behind"))` to the session frame in `_handle_subscribe` in `acp.py` (SC4). Added comment to `_handle_new`. Added SC4 block in `acp.html`'s session frame handler (after `setSteerStatus('')`, restores "Waiting…" when `payload.queued` is true). Added matching SC4 block in `index.html`'s `dashHandle()` session arm. Added `TestHandleSubscribeQueuedState` (2 pytest) + 4 JS tests. Auto-fix (cycle 1): 2 JS `queued:false` tests (commit 2669630). Cycle-2: Green. QA: PowerAtlas restarted; template content verified; 3518 pytest + 1129 JS pass.

### Phase 3: Dashboard visualization frame handler [QA]

**Goal**: Add the missing `visualization` case to `dashHandle()` so the dashboard renders viz iframe cards for live subscribed sessions.

**Covers**: SC5

**File scope**:
- `src/power_atlas/templates/index.html`
- `tests/acp_page.test.mjs`

**Changes**:

In `dashHandle()` (function `dashHandle` in `index.html`), add a `visualization` case. `addVisualizationCard(url, title)` is already defined in `transcript-renderer.js` and loaded by both pages. Place the new case after the `mcp_servers` handler and before `commands_execute_result` (or at any convenient location in the if-chain, matching the adjacent handlers' style):

```javascript
if (type === 'visualization') {
  if (!payload.url) return;
  addVisualizationCard(payload.url, payload.title || 'Visualization');
  return;
}
```

> **Sketch** — the guard `if (!payload.url) return` mirrors `addVisualizationCard`'s own internal guard and is an explicit gate for malformed frames. Note: `visualization` frames are broadcast-only (not recorded in the replay buffer), so viz cards appear only while live-subscribed, never on page reload or reconnect. This is consistent with acp.html's existing behavior.

**Test** in `tests/acp_page.test.mjs`: add a test that fires a `visualization` frame payload `{url: 'https://localhost/test.html', title: 'Test Viz'}` through the dashboard page's `dashHandle()` and asserts that `addVisualizationCard` was called with those arguments (spy or verify a `.acp-viz-card` element was inserted into the transcript container).

**Exit criteria**:
- [x] `dashHandle()` handles `type === 'visualization'` by calling `addVisualizationCard(payload.url, payload.title || 'Visualization')` (SC5)
- [x] `visualization` frames with a missing or falsy `payload.url` return without calling `addVisualizationCard` (SC5)
- [x] New test in `acp_page.test.mjs` passes: `node tests/acp_page.test.mjs` exits 0
- [x] `acp.html`'s existing visualization handler is unmodified

#### Implementation (2026-10-16, code: fb6ebf8)
Added `visualization` case to `dashHandle()` in `index.html` between `mcp_servers` and `commands_execute_result` handlers. Guards on `payload.url` (returns early if falsy). Calls `addVisualizationCard(payload.url, payload.title || 'Visualization')`. Added `addVisualizationCard` spy to `loadDashPicker` sandbox (`_vizCalls`). Added 3 JS tests: valid frame calls spy with correct args; no-url frame does not call spy; no-title frame uses 'Visualization' fallback. Auto-fix (cycle 1 Finding 1): title-fallback test (commit 8704e6b). Cycle-2: Green. QA: PASS — dashboard loads, no JS errors, visualization handler present in served template.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| R1: SC4 change in `acp.py` overlaps with sibling plan `261016_SESSION_FIDELITY_WORKFLOW_UI_AND_WF_INTERACTION.md` — both plans would add `"queued"` to the session frame | Medium: a second apply causes a duplicate key in the dict or a merge conflict | Land this plan first; the sibling plan notes SC4 as already done and does not re-apply the key. Coordinate before merging the sibling plan. |
| R2: Discarding `_steerPending` text on session switch leaves the user unable to recover the steer text | Low: acceptable trade-off | The steer was authored for the old session; restoring it to a new session would be more confusing than discarding it. Behavior is silent, same as existing behavior for other state cleared on switch. |
| R3: `setTranscriptLive(false)` added to `clearTranscript()` fires during the reconnect-replay sequence | Low: no-op in practice | In the reconnect flow, the `session` frame handler calls `clearTranscript()` (setting `_transcriptLive = false`); the `history` frame handler then calls `setTranscriptLive(false)` independently — idempotent. The fix is necessary for the session-switch path where the `session` frame triggers `clearTranscript()` but no `history` frame follows. |

## 7) Verification

**Python tests**: `.venv-PowerAtlas\Scripts\pytest tests\test_web.py --timeout=300`

**Node tests**: `node tests\acp_page.test.mjs`

**Manual live QA** (PowerAtlas running at `http://127.0.0.1:<port>`; sign in via `pa_local` cookie — see `AGENTS.md § Verification Setup`):

- **SC1**: Open two sessions. Start a Steer send on session A (Steer mode active, text in composer). Switch to session B via the rail before `steer_ack` arrives. Verify the composer on session B is fully interactive (not locked).
- **SC2**: Queue a prompt on session A (Queue button). Switch to session B. Switch back to session A. Verify the queued prompt text is gone and no prompt fires unexpectedly at turn-end.
- **SC3**: Start a turn on session A (mid-turn, `_transcriptLive = true`). Switch to session B (new empty session). Trigger a tool call for session B. Verify it renders via the replay / group path, not as a live incremental stream with no preceding `turn:start`.
- **SC4**: With two sessions open and session A queued behind session B's turn, reload the /acp page. Verify the "Waiting for another session's turn to finish…" message appears on session A's steer-status line immediately on load.
- **SC5**: Open the dashboard (`/`) and subscribe to a live session. Have the agent emit a visualization (`emit_visualization` tool). Verify a viz iframe card appears in the dashboard transcript.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|

No documentation updates needed. All five changes are internal implementation corrections to existing, already-documented features. No user-visible API, CLI surface, configuration, or observable feature behavior changes are introduced.

## Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Three session-switch state fixes | Complete | code: 69e916d |
| 2 | Queue-status restore on reconnect | Complete | code: 638994b |
| 3 | Dashboard visualization frame handler | Complete | code: fb6ebf8 |

## 9) Implementation Divergences from Plan

_Reserved — filled during implementation._

## Follow-up Work (Deferred)

1. **`_steerPending` restore in `ws.onclose` (stale-restore on reconnect).** When the WebSocket drops while a steer is in-flight, `ws.onclose` restores the text to the textarea but cannot know whether the steer already landed server-side. This is a separate stale-restore race window, deferred per scope boundary in `§ Scope boundaries & non-goals`. Source: scope boundary.

2. **Late `steer_sent` from old session after rail-click switch.** A `steer_sent` frame (broadcast via `_emit`) for the old session can arrive at the client after `selectSession()` clears `_steerPending`, during the narrow window before the server processes the new `subscribe` and detaches the socket from the old session's subscriber set. The steer_sent handler's `addMessage('steer', steerText)` call is unconditional, so the old steer's band would appear in the new session's just-cleared transcript. The probability is low (requires the steer's `await` to complete before the subscribe reaches the server), and the consequence is cosmetic (a dimmed band). This plan fixes steer_ack but not steer_sent; SC1 is scoped to steer_ack. A follow-up should add a `if (!_steerPending && promptInput.value.trim() !== steerText) return;` guard or adopt acp.html's full top-level `sessionId` frame filter. Source: Cycle 2 review, Reliability engineer + End-user advocate.

## Review Log

## Review Log

### 2026-10-16 — Post-Implementation Review

Overall implementation health: Green.
Personas: Senior engineer, Reliability engineer, End-user advocate, Maintainability reviewer.
4 findings (0 High, 0 Medium, 4 Low).
QA verification: PASS (5 surfaces: SC1/SC2/SC3 session-switch JS, SC4 Python+JS, SC5 dashboard JS; 1132 JS + 3518 pytest; no JS errors on live PowerAtlas after restart).

#### Test execution summary

| Phase | Tests | QA | Notes |
|---|---|---|---|
| 1: Three session-switch state fixes | pass | PASS | 1132 JS (T1–T3 + 1129 baseline) |
| 2: Queue-status restore on reconnect | pass | PASS | 3518 pytest + 1132 JS (4 SC4 tests) |
| 3: Dashboard visualization frame handler | pass | PASS | 1132 JS (3 SC5 tests) |

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Low | `steer_sent` unconditional band render — pre-existing race window not introduced by this plan. | User: accepted — documented in Follow-up Work item 2, pre-existing, deferred per plan scope. |
| 2 | Low | "Waiting…" string in 3 locations across 2 files; text change would require 3 edits. | Fixed — extracted to `QUEUE_WAITING_MSG` constant (acp.html IIFE + dashHandle-local; commit 48f59b1). |

Step 9 cycle-1: Green (2 Low). Finding 1 user-accepted, Finding 2 fixed. Cycle-2 review: Green (no new findings). Step 9b QA: PASS.

### 2026-10-16 — Implementation Review (after Phase 3, persona: Senior engineer, Reliability engineer, End-user advocate, Maintainability reviewer)

Implementation health: Green.
2 findings (0 High, 0 Medium, 2 Low). 2 auto-fixed.
QA: PASS (dashboard loads, no JS errors, visualization handler in served template).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Low | Progress Tracker Phase 3 row showed "Not started" (sub-agent did not update it). | Fixed — updated to Complete, code: fb6ebf8 (Step 7). |
| 2 | Low | No dashHandle-level test for `\|\| 'Visualization'` title fallback at dispatch layer. | Fixed — added title-fallback test (commit 8704e6b). |

Cycle-2 review: Green (no new findings). Fix commit 8704e6b reviewed and approved.

### 2026-10-16 — Implementation Review (after Phase 2, persona: Senior engineer, Reliability engineer, End-user advocate, Maintainability reviewer)

Implementation health: Green.
2 findings (0 High, 0 Medium, 2 Low). 2 auto-fixed.
QA: PASS (JS template verified; Python logic verified by pytest; full end-to-end requires live queued sessions).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Low | JS tests cover only `queued:true` explicitly; no `queued:false` test initially. | Fixed — added 2 `queued:false` tests (commit 2669630). |
| 2 | Low | CRLF artifact in git show for acp.html line 1 (identical byte content). | No action required — cosmetic diff noise. |

Cycle-2 review: Green (no new findings). Fix commit 2669630 reviewed and approved.

### 2026-10-16 — Implementation Review (after Phase 1, persona: Senior engineer, Reliability engineer, End-user advocate, Maintainability reviewer)

Implementation health: Green.
2 findings (0 High, 0 Medium, 2 Low).
QA: PASS (1 surface — JS page behavior; mutation tests confirmed test coverage).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Low | T1/T2 test comments label `railRows()[3]` as `sess-w1-s0`; actual session id depends on session_size. | Fixed — dropped label from test comments (commit 3ebf572). |
| 2 | Low | Pre-existing "Reset prompt navigation state" comment at bottom of `clearTranscript()` now misleadingly precedes SC3 code. | Fixed — removed misplaced comment (commit 3ebf572). |

Cycle 2 skipped — cycle 1 findings all Low + auto-fixes purely mechanical (comment-only, no behavior changes).

### 2026-10-16 — /qplan Cycle 1 (Full effort, 4 personas: Architect, Senior engineer, Reliability engineer, End-user advocate)

8 findings (1 High, 3 Medium, 4 Low). 8 auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | SC1 claims steer_ack is "ignored or handled gracefully" but Phase 1 adds no guard; late rejection ack corrupts new session's composer. | Fixed — added `if (!_steerPending) return;` to steer_ack handler in Phase 1 Changes + exit criterion. |
| 2 | Medium | Tests T1/T2 access IIFE-scoped variables not reachable from test harness. | Fixed — rewrote T1/T2/T3 to use behavioral assertions (DOM state, send() spy, setLiveCalls recorder). |
| 3 | Medium | `_handle_new` also emits session frame; `"queued"` omission there is undocumented. | Fixed — added Design Decisions row explaining the omission + exit criterion noting the comment. |
| 4 | Medium | Phase 1 edits shared `clearTranscript()`; dashboard impact unverified. | Fixed — added exit criterion note + comment in clearTranscript sketch verifying dashboard path is correct. |
| 5 | Low | R3 mechanism prose: "history frame handler calls clearTranscript()" is wrong (session frame handler does). | Fixed — corrected R3 prose. |
| 6 | Low | SC5 broadcast-only nature not documented; viz cards won't appear on reload. | Fixed — added broadcast-only note to Phase 3 sketch. |
| 7 | Low | Deliberate difference (selectSession silently discards vs. releaseSession note) undocumented. | Fixed — added rationale to Design Decisions `_steerPending` row. |
| 8 | Low | T3 should use existing `setLiveCalls` recorder. | Fixed — T3 rewritten to reference the recorder explicitly. |

### 2026-10-16 — /qplan Cycle 2 (Full effort, 4 personas: Architect, Senior engineer, Reliability engineer, End-user advocate)

3 findings (0 High, 1 Medium, 2 Low). 3 auto-resolved (1 fix, 2 deferred to Follow-up Work).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | T2 exercises the live turn:end path which the existing session-identity guard handles with or without the fix; real SC2 distinction requires the round-trip scenario. | Fixed — rewrote T2 to: queue A → switch B → return A (no turn:end in history) → deliver live turn:end → assert no auto-send. |
| 2 | Low | Late `steer_sent` for old session can render a stale band on new session's transcript (narrow race; pre-existing, not introduced by this plan). | Deferred — added to Follow-up Work item 2. |
| 3 | Low | steer_ack guard has a narrow residual race where the user starts a fresh steer on B before a late A ack arrives (same residual as the dashboard's own proven guard). | Accepted as per-design (mirrors existing dashboard pattern; no behavioral consequence). |

## Harness Improvement Opportunities

_Reserved — appended during /qexplore, /qplan and /qdev when harness friction is felt._
