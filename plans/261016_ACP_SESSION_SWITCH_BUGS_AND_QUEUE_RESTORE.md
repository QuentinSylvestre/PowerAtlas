# ACP session-switch state bugs and queue-status restore on reconnect

> **Date**: 2026-10-16
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Fix three session-switch state bugs in acp.html; restore queue-waiting message on reconnect; add visualization handler to the dashboard

---

## Intent

### Problem statement & desired outcomes

Five concrete bugs, all found by code analysis (no reproduction steps needed):

1. **Steer-pending lock** (`acp.html:5961`): when a Steer send is in-flight and the user clicks a different session in the rail, `_steerPending` is not cleared and `promptInput.disabled`, `sendModeBtn.disabled`, `modeToggle.disabled` are not restored. The new session opens with the composer locked indefinitely — the only recovery is a page reload.

2. **Queued prompt silently discarded** (`acp.html:4903`): `queuedPrompt` and `queuedPromptSession` are not cleared in the rail-click path (`selectSession()`). If the user queued a prompt on session A then switches to session B, the queued text survives the switch. When the user eventually returns to session A and the `meta turn:end` fires during transcript replay, the queued text is discarded silently (the `!replaying` guard at line 4944 suppresses the "session changed" note during replay).

3. **Stale `_transcriptLive`** (`transcript-renderer.js:92`): `setTranscriptLive()` is not called during `selectSession()` or `clearTranscript()`. If the user switches from a live session (mid-turn) to a new empty session (no history), `_transcriptLive` remains `true`. Any tool calls arriving before the new session's first `meta turn:start` are rendered with the live incremental grouping path instead of the replay path.

4. **Queue-waiting state not restored on reconnect** (`acp.py:9118`): `meta["queued_behind"]` persists server-side but `_handle_subscribe` does not re-send `queue_status`. A client that reconnects while a session is queued behind another session gets `turnActive: true` with no indication of the waiting state. The steer-status line stays blank.

5. **Dashboard never renders visualization cards** (`index.html`): `dashHandle()` has no `visualization` case. Visualization frames reach the dashboard WebSocket (broadcast fans to all subscribers) but are silently discarded. Live dashboard sessions never show viz cards.

Desired outcome: switching sessions in /acp is fully clean; the queue-waiting message survives a page reload; the dashboard renders viz cards for sessions it subscribes to.

### Success criteria

- SC1: Switching sessions in /acp when a Steer is in-flight: `_steerPending` is cleared, `promptInput`, `sendModeBtn`, and `modeToggle` are re-enabled on the new session. The steer text is discarded (not restored — the steer was for the previous session). If a `steer_ack` subsequently arrives for the old session, it is ignored or handled gracefully.
- SC2: Switching sessions in /acp: `queuedPrompt` and `queuedPromptSession` are cleared. The queued text is discarded. No silent loss later.
- SC3: Switching sessions in /acp from a live (mid-turn) session to any other session: `_transcriptLive` is `false` after `clearTranscript()` / `selectSession()` completes.
- SC4: After a page reload or WebSocket reconnect, if the session is queued behind another session, the "Waiting for another session's turn to finish…" message is shown on the steer-status line. Implemented by adding `queued: bool(meta.get("queued_behind"))` to the session frame payload in `_handle_subscribe`, and restoring the message in the session frame handler on both pages.
- SC5: The dashboard `dashHandle()` renders visualization iframe cards (`addVisualizationCard()`) when a `visualization` frame arrives for a live subscribed session.

### Scope boundaries & non-goals

**In scope**: the five bugs above.

**Out of scope**:
- Rail or Needs Attention indicator for queued-behind sessions (SC4 is steer-status line only)
- `acp.html` `releaseSession()` path (already clears all these correctly; only `selectSession()` is broken)
- The `_steerPending` fix in `ws.onclose` (stale restore on reconnect — separate from the session-switch path; deferred)
- Anything related to the dashboard session-switch path (`dashCloseIfAbandoned()` already clears all equivalents correctly; no dashboard fix needed for SC1–SC3)

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

**Bug 1 — steer-pending lock** (`acp.html`):
- `_steerPending` set at line 5961 (steer send), cleared in `releaseSession()` at line 1271 and steer-ack/steer-sent handlers at lines 5126–5139.
- `promptInput.disabled = true` at line 5961; restored only in `releaseSession()` (line 1266) and steer-ack paths (lines 5126, 5136).
- Rail-click path: `selectSession()` at line ~4374. Calls `clearTranscript()`, `setTurn(false)`, `setContext(null)` — does NOT touch `_steerPending` or the three `disabled` fields.
- `session` frame handler at line 5051 (`clearTranscript()` called there): also does not restore `_steerPending` or `disabled`.
- Fix: in `selectSession()` (or at the top of the `session` frame handler, before `clearTranscript()`), add: `_steerPending = null; promptInput.disabled = false; sendModeBtn.disabled = false; modeToggle.disabled = false;` — and clear `setSteerStatus('')` (which the session frame handler already does at line 5077).

**Bug 2 — queued prompt not cleared** (`acp.html`):
- `queuedPrompt` and `queuedPromptSession` declared at lines 808–809.
- Set at lines 5928–5929 (Queue button). Cleared in `releaseSession()` at lines 1257–1258 and in `meta turn:end` handler at lines 4903–4904.
- `selectSession()` does NOT clear them.
- The `meta turn:end` handler at line 4903 clears them unconditionally (no `!replaying` guard on the clear itself). So if the user returns to session A and its turn-end was recorded, the queued text is cleared during replay — silently, since the "session changed" note at line 4944 IS guarded by `!replaying`. Text is lost with no visible trace.
- Fix: add `queuedPrompt = null; queuedPromptSession = null;` to `selectSession()`.
- Dashboard parity: `dashCloseIfAbandoned()` at `index.html:5138` already clears `_dashQueuedPrompt` and `_dashQueuedPromptSession`; no fix needed there.

**Bug 3 — stale `_transcriptLive`** (`transcript-renderer.js:92`):
- `setTranscriptLive(v)` at line 98. Called from: `meta turn:start` handler (sets true), `meta turn:end` handler (sets false), `history` frame handler (sets false), `agent_died` handler (sets false).
- NOT called from `clearTranscript()` or `selectSession()`.
- `clearTranscript()` at line ~2931 resets `agentBody`, `toolRows`, `_toolTimers`, `toolGroup`, `openGroup`, `_autoExpandEdits` — but not `_transcriptLive`.
- Fix: add `setTranscriptLive(false)` to `clearTranscript()`. This is safe: `clearTranscript()` is called at session switch, at `releaseSession()`, and in the `session` frame handler — all are contexts where a live state from the previous session must not carry over.

**Bug 4 — queue_status not restored on reconnect** (`acp.py`):
- `_set_queued_behind()` at `acp.py:4624`: stores `meta["queued_behind"]` and broadcasts `queue_status{"waiting": bool}`.
- `_handle_subscribe` at `acp.py:9118–9230`: sends the session frame at line 9171 with `turnActive`, `contextPercent`, `cwd`, `created` — but not `queued_behind`.
- No `queue_status` re-send anywhere in the subscribe path.
- Client handler: `acp.html:5170` (`if (type === 'queue_status') { if (!replaying) setSteerStatus(...); }`) — already guarded against replay. Needs to also handle it in the session frame path.
- Fix: add `"queued": bool(meta.get("queued_behind"))` to the session frame envelope at `acp.py:9171`. In the session frame handler on both pages: after `setSteerStatus('')`, check `if (payload.queued) setSteerStatus('Waiting for another session\u2019s turn to finish\u2026')` (and the dashboard equivalent).
- Note: this fix overlaps with File 1 (`261016_SESSION_FIDELITY...`) SC2 — coordinate with that plan to avoid a double-merge conflict. Recommend landing this fix in whichever plan ships first.

**Bug 5 — dashboard viz handler missing** (`index.html`):
- `dashHandle()` dispatches on `type`. Zero occurrences of "visualization" or "addVisualizationCard" in `index.html` (confirmed by probe).
- `addVisualizationCard(url, title)` at `transcript-renderer.js:2988` is already loaded by both pages.
- Fix: add a `visualization` case to `dashHandle()`:
  ```javascript
  if (type === 'visualization') {
    if (!payload.url) return;
    addVisualizationCard(payload.url, payload.title || 'Visualization');
    return;
  }
  ```
- Note: this fix also overlaps with File 1 Phase 2. Coordinate.

**`releaseSession()` already correct** (`acp.html:1224`):
`releaseSession()` clears `queuedPrompt`, `queuedPromptSession`, `_steerPending`, restores `promptInput.disabled`, `sendModeBtn.disabled`, `modeToggle.disabled`. The bugs are only in the rail-click path (`selectSession()`) which does NOT go through `releaseSession()`.

**Dashboard parity already correct** (`index.html`):
`dashCloseIfAbandoned()` at `index.html:5108` clears `_dashQueuedPrompt`, `_dashQueuedPromptSession`, `_dashSteerPending`, re-enables `dashPromptInput`, `dashSendModeBtn`, `dashModeToggle`. Bugs 1–3 exist only in acp.html's `selectSession()`.

**Tests**:
- `tests/acp_page.test.mjs`: no tests for session-switch state (`selectSession()`, `_steerPending`, `queuedPrompt` cross-session). `queue_status` cross-session has no test coverage.
- `tests/test_web.py`: `TestAcpReplayOnSubscribe` covers subscribe re-send logic; no test for `queued_behind` in session frame.

### 5. Risks & mitigations

**R1 — SC4 overlaps with File 1 SC2.**
Both plans add `"queued"` to the session frame and restore the steer-status message on session frame receipt. If both are planned simultaneously, the Python change is identical and only needs to land once. Recommend implementing SC4 in this plan (which ships first as a bug-fix plan) and noting it as already done in File 1.

**R2 — Clearing `_steerPending` in `selectSession()` discards in-flight steer silently.**
The steer request is already in-flight to the server. A `steer_ack` or `steer_sent` will arrive on the socket for the old session. After `_steerPending = null`, the ack handlers at lines 5120–5139 use `_steerPending` — if it's null, the handlers become no-ops. The in-flight steer to session A is then orphaned: it may or may not apply to session A (server-side it probably does). No new harm is introduced; the text is silently dropped, same as the existing behavior for other session switches. Acceptable.

**R3 — `setTranscriptLive(false)` in `clearTranscript()` is called during replay.**
`clearTranscript()` is called at the start of `history` frame replay (inside the session frame handler). At that point `_transcriptLive` is already false (set to false at the start of the `history` frame handler at `acp.html:5023`). So adding it to `clearTranscript()` is a no-op in the replay path — safe.

### 6. Resolved decisions

- Q1 (decomposition): Option B — this plan covers session-switch bugs and queue restore; `261016_SESSION_FIDELITY_WORKFLOW_UI_AND_WF_INTERACTION.md` covers the larger features
- Q (queue UI scope): steer-status line restoration only; no rail indicator or Needs Attention entry

### 7. Open items

None. All five bugs have deterministic root causes and clear fixes.

Coordinate SC4 / Bug 4 with `261016_SESSION_FIDELITY_WORKFLOW_UI_AND_WF_INTERACTION.md` to avoid merging the same `acp.py` change twice.

### 8. Recommended approach

All five fixes are independent of each other. Suggested order by risk:

**Phase 1 — Three acp.html session-switch state fixes** (SC1, SC2, SC3)
- `acp.html` `selectSession()`: add `_steerPending = null; promptInput.disabled = false; sendModeBtn.disabled = false; modeToggle.disabled = false;` (SC1).
- `acp.html` `selectSession()`: add `queuedPrompt = null; queuedPromptSession = null;` (SC2).
- `transcript-renderer.js` `clearTranscript()`: add `setTranscriptLive(false);` (SC3).
- `acp_page.test.mjs`: add tests for all three — specifically: (a) after `setCrew` then `clearTranscript()`, `_transcriptLive` is false; (b) after a simulated session switch with steer pending, `promptInput.disabled` is false; (c) after a session switch with a queued prompt, `queuedPrompt` is null.

**Phase 2 — Queue-status restore on reconnect** (SC4)
- `acp.py` `_handle_subscribe`: add `"queued": bool(meta.get("queued_behind"))` to the session frame payload.
- `acp.html` session frame handler: after `setSteerStatus('')`, add `if (payload.queued) setSteerStatus('Waiting for another session\u2019s turn to finish\u2026');`
- `index.html` session frame handler: add `if (payload.queued) setDashSteerStatus('Waiting for another session\u2019s turn to finish\u2026');`
- `test_web.py`: add test — `_handle_subscribe` on a session with `queued_behind` set emits `session` frame with `queued: true`.
- `acp_page.test.mjs`: add test — session frame with `{queued: true}` sets steer-status text on both pages.

**Phase 3 — Dashboard visualization handler** (SC5)
- `index.html` `dashHandle()`: add `visualization` case calling `addVisualizationCard(payload.url, payload.title || 'Visualization')`.
- `acp_page.test.mjs`: add test — `visualization` frame in dashboard context calls `addVisualizationCard`.

### 9. QA environment

- PowerAtlas running at `http://127.0.0.1:<port>`. Sign in via `pa_local` cookie.
- Browser: Playwright from `.venv-PowerAtlas` or Chrome via Claude-in-Chrome MCP.
- SC1: trigger a Steer send on session A, switch to session B while the steer is in-flight (before `steer_ack` arrives), confirm the composer on session B is interactive.
- SC2: queue a prompt on session A, switch to session B, switch back, confirm no queued prompt fires unexpectedly.
- SC3: start a turn on session A, switch to session B (new, no history), confirm tool calls arriving for session B render in the replay path.
- SC4: with two sessions open and one mid-turn, reload the page for the waiting session, confirm the steer-status message appears.
- SC5: on a dashboard session that receives a viz emit, confirm the viz card appears.
- Python tests: `pytest tests/test_web.py --timeout=300`; node tests: `node tests/acp_page.test.mjs`.
