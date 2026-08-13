# ACP Queue/Steer Single-Button Mode Picker and Steer Trace Visibility

> **Date**: 2026-08-13
> **Status**: In Progress
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Two /acp composer UX improvements — replace the two-button Queue/Steer group with a single-button+mode-select, and make injected steer text visible in the transcript during the session and across WS reconnects.
> **Estimated effort**: 0.5–1 day

---

## Intent

### Problem statement & desired outcomes

The Queue/Steer two-button group is cramped and its interaction model is awkward: two half-height buttons that together replace Stop, with no keyboard path during a turn. When a steer is sent, the only feedback is an ephemeral "Steer sent." note in amber italic — it carries no text, disappears on reload, and gives no indication of what was injected. Both issues reduce confidence and flow when using the agent surface.

Desired outcomes:
- A single full-height action button in the composer row during a turn, with an inline mode selector (Queue / Steer) that remembers the last choice.
- Enter key during a turn triggers whichever mode is currently selected, consistent with Enter triggering Send outside a turn.
- Injected steer text is visible in the transcript as a dimmed variant of the user prompt band — visually distinguishable from a real turn prompt but sharing the same layout — and survives WebSocket reconnects within the same PowerAtlas process lifetime.

### Success criteria

- SC-1: During a turn with textarea text, a single button (full Stop-height) replaces the two stacked buttons. A native `<select>` adjacent to it lets the user choose Queue or Steer; the button label tracks the selected mode.
- SC-2: The chosen mode persists across page reloads via `localStorage` key `pa_acp_send_mode` (`"steer"` default, `"queue"` alternative). Fresh installs and missing keys default to Steer.
- SC-3: Enter key during a turn (no Shift, no Ctrl, not a touch device, textarea non-empty) triggers the selected mode, exactly as Enter outside a turn triggers Send.
- SC-4: When a steer is injected (steer_ack success), the steer text appears in the transcript as a dimmed user-band message: same blue-accent band layout as `.acp-msg-user`, but with reduced opacity (`rgba(108,140,255,0.06)` background, `rgba(108,140,255,0.4)` left border, dimmed body text via `var(--text-dim)`). Role label is blank.
- SC-5: The steer trace message survives WebSocket reconnect within the same PowerAtlas process (stored in the ring buffer via `_emit` in `_handle_steer`). It does not survive a PowerAtlas restart (in-memory ring buffer — accepted).
- SC-6: All 16 existing queue/steer tests pass (updated for new DOM structure). New tests cover mode selection, Enter-during-turn, and `steer_sent` frame rendering.

### Scope boundaries & non-goals

In scope: `acp.html`, `style.css`, `acp.py`, `tests/acp_page.test.mjs`, `README.md`.

Out of scope: persisting steer traces across PowerAtlas restarts; handling `AgentExecutionSteeringInjected` from kiro-cli; changing Queue semantics; mobile/touch Enter behavior.

---

## 1) Current State

**Composer row DOM** (`acp.html:262–267`, actual lines ~310–315 after recent dropdown refactor): `div#acpQueueSteer` (class `acp-queue-steer`, initially `hidden`) contains two buttons — `#acpQueue` (`acp-btn acp-queue`) and `#acpSteer` (`acp-btn acp-steer`). `refreshComposerControls()` (actual ~L1108) toggles only `queueSteerEl.hidden = !turnActive || !hasText` — never references the individual buttons.

> **Note on line numbers**: The file has shifted ~50 lines from the exploration snapshot. Use grep to find exact locations: `Select-String -Path acp.html -Pattern 'steerBtn\.'` before editing. Section 1 references are approximate; the grep is authoritative.

**Button sizing** (`style.css:664–666`): `.acp-queue-steer { display:flex; flex-direction:column; gap:0; }`. Each button: `height:calc(var(--send-btn-height,2.5rem)/2)`. The `--send-btn-height` CSS variable is **never defined** — only consumed with a `2.5rem` fallback. Actual rendered height comes from `align-items:stretch` on `.acp-composer-row` (`style.css:977`).

**steerBtn reference points** (9 total, all in `acp.html`):
- 7 × `steerBtn.disabled = false` in: releaseSession, steer_ack rejection, steer_ack success, agent_died handler, error handler, ws.onclose (6 non-click sites)
- 1 × `steerBtn.disabled = true` inside the click handler (removed with the handler)
- 1 × `steerBtn.addEventListener(...)` — the click handler declaration

Run `Select-String -Path acp.html -Pattern 'steerBtn\.'` to get exact lines before implementing.

**queueBtn reference points** (3 total): HTML markup, JS `var queueBtn` declaration, click handler.

**Enter key during turn** (`acp.html`): the final Enter branch always calls `sendPrompt()`, which early-returns when `turnActive`. No branch handles Enter during a turn today.

**Steer flow** (`acp.py`): `_handle_steer` validates text, calls `await _supervisor.steer(session_id, text)`, then sends `steer_ack` via `conn.send()` — unicast, NOT `_emit`, so it never enters the ring buffer. The "Steer sent." note is added client-side only.

**Ring buffer** (`acp.py:3240–3249`): `_emit(session_id, frame)` records in `_supervisor.history[session_id]` (in-memory deque, max 2000 frames / 2 MB) then broadcasts. Survives WS reconnects within the same process; wiped on restart.

**`envelope()` guard** (`acp.py:806–811`): raises `ValueError` if `type_` not in `SERVER_TYPES`. `"steer_sent"` must be added to `SERVER_TYPES` (`acp.py:163`) before calling `_emit` with it.

**User band CSS** (`style.css:738–743`): `.acp-msg-user { background:var(--accent-dim); border-left:2px solid var(--accent); margin:0 -14px; padding:6px 14px 6px 12px; }`. `--accent:#6c8cff`, `--accent-dim:rgba(108,140,255,0.12)` (`style.css:15,17`).

**localStorage wrappers** (`acp.html`): `railStored(key)` / `railStore(key, value)` — try/catch wrappers. Existing keys: `pa_acp_debug_log`, `pa_acp_group`. Both are `function` declarations, hoisted to the top of the closure — safe to call from any var declaration site.

## 2) Goal

Replace the two-stacked-button Queue/Steer group with a single action button plus a native `<select>` mode picker (defaulting to Steer, persisted in localStorage), wire Enter-during-turn to dispatch the selected mode, and add a `steer_sent` ring-buffer frame so the steer text appears as a dimmed user-band message that survives WS reconnects.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Mode-picker widget | Native `<select>` | Custom dropdown | Two items, never grows; native keyboard semantics; zero extra JS |
| Mode persistence | `localStorage` key `pa_acp_send_mode` | In-memory only | Consistent with `pa_acp_group` pattern; user who prefers Queue shouldn't re-select every session |
| Enter-during-turn triggers | Currently selected mode | Always Steer; always Queue | Consistent with "the button does what it says" |
| Steer trace storage | `_emit("steer_sent", {text})` from `_handle_steer` on success | Client-side only; `AgentExecutionSteeringInjected` | Client-only disappears on reconnect; `AgentExecutionSteeringInjected` is dropped by acp.py; `_emit` costs ~8 lines using existing replay |
| Steer trace visual style | `.acp-msg-steer` — same band layout as `.acp-msg-user` at reduced opacity | Re-use `acp-msg-note` | User requested same formatting as user prompt, slightly dimmer |
| Emit only on success | `_emit` fires only when `queued === true` | Emit on every attempt | A rejected steer shouldn't appear as sent in replay |
| DOM structure | Reuse `#acpQueueSteer` wrapper — replace its children | Replace wrapper entirely | `refreshComposerControls()` wrapper visibility logic already correct; no change needed there |
| Existing "Steer sent." note | Remove it in Phase 2 — replaced by steer trace band | Keep both | Redundant with the band; band is more informative |
| Phase 1/2 steer_ack conflict | Serialize: Phase 1 completes all `steer_ack` handler changes (disable replace + note removal); Phase 2 only adds `steer_sent` handler | Parallel | Both phases touch the same `steer_ack` block; parallel dispatch creates a merge conflict |

## 4) External Dependencies & Costs

### Required external changes

None — all changes are in-repo, in-process.

### Cost impact

None.

## 5) Implementation Phases

> **Phasing note**: Phases 1 and 2 both touch `acp.html`. Phase 1 completes ALL changes to the `steer_ack` success handler (including removing the "Steer sent." note), so Phase 2 only adds the new `steer_sent` frame handler — no merge conflict. The `[P:1]`/`[P:2]` annotations are retained but see the serialization note: Phase 1 must be complete before Phase 2 starts on shared files.

### Phase 1: Single-button + mode-select UI [QA] [P:2]

**Goal**: Replace the two half-height Queue/Steer buttons with a single full-height action button and a native `<select>` mode picker inside the existing `#acpQueueSteer` wrapper. Wire localStorage persistence and update all steerBtn/queueBtn references. Complete ALL steer_ack handler changes (including removing the "Steer sent." note). No server changes.

**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/static/style.css`, `tests/acp_page.test.mjs`, `README.md` (button-structure sentence only)

**Covers**: SC-1, SC-2, SC-3, SC-6 (partial — existing tests updated + new tests for mode selection and Enter-during-turn)

#### HTML changes (`acp.html`)

Replace the two buttons inside `#acpQueueSteer`:

```html
<!-- Before -->
<div class="acp-queue-steer" id="acpQueueSteer" hidden>
  <button class="acp-btn acp-queue" id="acpQueue" type="button"
          aria-label="Queue this prompt — it will send when the agent finishes">Queue</button>
  <button class="acp-btn acp-steer" id="acpSteer" type="button"
          aria-label="Inject this text into the current agent turn">Steer</button>
</div>

<!-- After -->
<div class="acp-queue-steer" id="acpQueueSteer" hidden>
  <button class="acp-btn acp-send-mode" id="acpSendMode" type="button"
          aria-label="Inject this text into the current agent turn">Steer</button>
  <select class="acp-mode-select" id="acpModeSelect" aria-label="Send mode">
    <option value="steer">Steer</option>
    <option value="queue">Queue</option>
  </select>
</div>
```

Note: `aria-label` on `sendModeBtn` is updated by the mode-change handler to reflect the active mode. Initial value matches the Steer default.

#### CSS changes (`style.css`)

Remove the two half-height rules and their combined selector; add the new row layout:

```css
/* Remove these rules entirely (elements are being removed): */
/* .acp-queue-steer { display: flex; flex-direction: column; gap: 0; }  ← replace with row layout below */
/* .acp-queue, .acp-steer { height: calc(var(--send-btn-height, 2.5rem) / 2); line-height: 1; font-size: 11px; display: flex; align-items: center; justify-content: center; padding: 0 12px; } */

/* New rules: */
.acp-queue-steer { display: flex; flex-direction: row; gap: 0; align-items: stretch; }
.acp-queue-steer[hidden] { display: none; }
.acp-send-mode {
  flex: 1;
  border-radius: var(--radius-sm) 0 0 var(--radius-sm);
}
.acp-mode-select {
  padding: 0 4px;
  height: 100%;           /* WebView fallback alongside align-items:stretch */
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: none;
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
  min-width: 0;
}
.acp-mode-select:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
```

Note: `height:100%` on `.acp-mode-select` is a pywebview/embedded-WebView fallback in case `align-items:stretch` alone does not propagate to a native `<select>`.

#### JS changes (`acp.html`)

**Step 1 — Variable declarations**: replace `queueBtn`/`steerBtn` with new elements:

```js
// Remove:
// var queueBtn = document.getElementById('acpQueue');
// var steerBtn = document.getElementById('acpSteer');

// Add:
var sendModeBtn = document.getElementById('acpSendMode');
var modeSelect = document.getElementById('acpModeSelect');
var SEND_MODE_KEY = 'pa_acp_send_mode';
// Validate stored value; default to 'steer' for any missing/invalid value.
var _sendMode = railStored(SEND_MODE_KEY);
if (_sendMode !== 'steer' && _sendMode !== 'queue') _sendMode = 'steer';
modeSelect.value = _sendMode;
sendModeBtn.textContent = _sendMode === 'queue' ? 'Queue' : 'Steer';
sendModeBtn.setAttribute('aria-label',
  _sendMode === 'queue'
    ? 'Queue this prompt — it will send when the agent finishes'
    : 'Inject this text into the current agent turn');
```

**Step 2 — Mode select change handler**:

```js
modeSelect.addEventListener('change', function () {
  _sendMode = modeSelect.value;
  railStore(SEND_MODE_KEY, _sendMode);
  sendModeBtn.textContent = _sendMode === 'queue' ? 'Queue' : 'Steer';
  sendModeBtn.setAttribute('aria-label',
    _sendMode === 'queue'
      ? 'Queue this prompt — it will send when the agent finishes'
      : 'Inject this text into the current agent turn');
});
```

**Step 3 — `sendModeBtn` click handler**: replace the separate `queueBtn` + `steerBtn` click handlers with one dispatcher:

```js
sendModeBtn.addEventListener('click', function () {
  var text = promptInput.value.trim();
  if (!text || !turnActive || !sessionId) return;
  if (_sendMode === 'queue') {
    // Queue path — verbatim from old queueBtn click handler
    queuedPrompt = text;
    queuedPromptSession = sessionId;
    promptInput.value = '';
    autoGrowPrompt();
    refreshComposerControls();
    var queueNoteEl = addMessage('note', '');
    var preview = text.length > 30 ? text.slice(0, 30) + '\u2026' : text;
    var noteSpan = document.createElement('span');
    noteSpan.textContent = 'Queued: ' + preview + ' \u2014 ';
    queueNoteEl.appendChild(noteSpan);
    var cancelLink = document.createElement('button');
    cancelLink.className = 'acp-inline-cancel';
    cancelLink.type = 'button';
    cancelLink.textContent = 'Cancel';
    cancelLink.setAttribute('aria-label', 'Cancel queued prompt');
    cancelLink.addEventListener('click', function () {
      if (!queuedPrompt) return;
      promptInput.value = queuedPrompt;
      queuedPrompt = null;
      queuedPromptSession = null;
      autoGrowPrompt();
      refreshComposerControls();
      queueNoteEl.textContent = 'Queued prompt cancelled.';
    });
    queueNoteEl.appendChild(cancelLink);
  } else {
    // Steer path — verbatim from old steerBtn click handler
    _steerPending = text;
    promptInput.disabled = true;
    sendModeBtn.disabled = true;
    modeSelect.disabled = true;  // disable mode select while steer is in-flight
    promptInput.value = '';
    autoGrowPrompt();
    refreshComposerControls();
    send('steer', {message: text}, sessionId);
  }
});
```

**Step 4 — Update all `steerBtn.disabled` sites** (run `Select-String -Path acp.html -Pattern 'steerBtn\.'` first to confirm locations):

| Location | Old | New | Also add |
|---|---|---|---|
| `releaseSession()` | `steerBtn.disabled = false` | `sendModeBtn.disabled = false` | `modeSelect.disabled = false` |
| `steer_ack` rejection | `steerBtn.disabled = false` | `sendModeBtn.disabled = false` | `modeSelect.disabled = false` |
| `steer_ack` success | `steerBtn.disabled = false` | `sendModeBtn.disabled = false` | `modeSelect.disabled = false` |
| `agent_died` handler | `steerBtn.disabled = false` | `sendModeBtn.disabled = false` | `modeSelect.disabled = false` |
| `error` handler | `steerBtn.disabled = false` | `sendModeBtn.disabled = false` | `modeSelect.disabled = false` |
| `ws.onclose` | `steerBtn.disabled = false` | `sendModeBtn.disabled = false` | `modeSelect.disabled = false` |

Notes on unchanged behavior:
- `promptInput.disabled = false` restore lines at all 5 recovery sites (steer_ack rejection, agent_died, error, ws.onclose, releaseSession) are **unchanged** — leave them as-is.
- `promptInput.value = _steerPending` restore lines in steer_ack rejection, agent_died, error, and ws.onclose are **unchanged** — leave them as-is.
- `refreshComposerControls()` is **unchanged** — it references `queueSteerEl` (wrapper), not individual buttons.

**Step 5 — Remove "Steer sent." note** from `steer_ack` success path (currently the line `addMessage('note', 'Steer sent.')`):
```js
// Remove this line from steer_ack success path:
// addMessage('note', 'Steer sent.');
// It will be replaced by the steer_sent band rendered in Phase 2.
```

This belongs in Phase 1 so both phases don't touch the same `steer_ack` block.

**Step 6 — Enter-during-turn branch**: in `promptInput` keydown handler, after the command-dropdown block and before the final `sendPrompt()` call:

```js
if (ev.key === 'Enter' && !ev.shiftKey && !ev.ctrlKey && !ev.altKey && !isTouchDevice) {
  ev.preventDefault();
  if (turnActive && promptInput.value.trim()) {
    // Dispatch to queue or steer based on selected mode.
    // sendModeBtn.click() is used to avoid duplicating the guard logic.
    // Safety invariant: promptInput is disabled while a steer is in-flight
    // (_steerPending set), which prevents the keydown handler from firing
    // in that state. The wrapper #acpQueueSteer is hidden when !hasText,
    // so sendModeBtn is only reachable when text is present.
    sendModeBtn.click();
  } else {
    sendPrompt();
  }
}
```

#### Test changes (`tests/acp_page.test.mjs`)

**Update existing 16 tests** that reference `acpQueueSteer`, `acpQueue`, or `acpSteer`:
- DOM fixture: replace `#acpQueue`/`#acpSteer` buttons with `#acpSendMode` button + `#acpModeSelect` select.
- Tests clicking `queueBtn.click()` → set `modeSelect.value = 'queue'` + fire change event, then `sendModeBtn.click()`.
- Tests clicking `steerBtn.click()` → confirm `modeSelect.value = 'steer'` (default), then `sendModeBtn.click()`.
- Tests checking `steerBtn.disabled` → check `sendModeBtn.disabled`.
- Tests that check for "Steer sent" in transcript → update to assert this string is NOT present (note removed in Phase 1; band arrives via steer_sent in Phase 2).
- Note: the three `_steerPending` restore tests (steer_ack rejection, error frame, agent_died, ws.onclose) also verify `promptInput.value` is restored — these assertions are unchanged.

**New tests** (5 for Phase 1):
- `"mode select defaults to steer"` — after init, `modeSelect.value === 'steer'` and `sendModeBtn.textContent === 'Steer'`.
- `"mode select change updates button label and persists"` — change `modeSelect.value = 'queue'`, fire change event; assert `sendModeBtn.textContent === 'Queue'` and `railStore('pa_acp_send_mode', 'queue')` was called.
- `"mode select with invalid stored value defaults to steer"` — pre-seed localStorage with an invalid value `'invalid'`; reload init; assert `_sendMode === 'steer'`.
- `"Enter during turn in steer mode triggers steer send"` — set up turn active, text in textarea, `_sendMode = 'steer'`; fire Enter keydown; assert `steer` frame sent.
- `"Enter during turn in queue mode stores queued prompt"` — set up turn active, text in textarea, `_sendMode = 'queue'`; fire Enter keydown; assert `queuedPrompt` is set, no `steer` frame.

**Exit criteria**:
- [x] `#acpQueueSteer` wrapper contains `#acpSendMode` button and `#acpModeSelect` select; `#acpQueue` and `#acpSteer` elements do not exist in DOM
- [x] `grep 'steerBtn\.' acp.html` returns zero hits; `grep 'queueBtn\.' acp.html` returns zero hits (both fully replaced)
- [x] `pa_acp_send_mode` defaults to `"steer"` on fresh load; invalid stored values fall back to `"steer"`; persists across reload after change
- [x] Button label and `aria-label` track selected mode
- [x] `modeSelect.disabled` is set alongside `sendModeBtn.disabled` at all 6 re-enable sites plus the click handler's steer path
- [x] All 5 `_steerPending` recovery paths restore both `sendModeBtn.disabled = false` AND `promptInput.disabled = false` AND `modeSelect.disabled = false`
- [x] `addMessage('note', 'Steer sent.')` removed from `steer_ack` success path
- [x] `"Steer was not accepted by the agent."` error message remains in `steer_ack` rejection path (unchanged)
- [x] Enter during turn with steer mode → steer frame sent; Enter during turn with queue mode → queued prompt stored; Enter outside turn → `sendPrompt()` called
- [x] `README.md` updated: "two stacked half-height buttons" reworded to describe single button + mode selector
- [x] `node tests/acp_page.test.mjs` passes; expected count: **326/327** (321 baseline + 5 Phase-1 + 2 cycle-1 auto-fix additions = 328 total registrations minus 2 skipped groups = 326 pass; pre-existing dashboard-link failure unchanged)

#### Implementation (2026-08-13, code: 8102e9d + c718b87)

Phase 1 replaced the two half-height Queue/Steer buttons in `acp.html` with a single full-height `#acpSendMode` button and an adjacent `#acpModeSelect` select element. CSS rules added to the page's inline `<style>` block (not `style.css`, per AGENTS.md). JS variables updated to `sendModeBtn`/`modeSelect`/`SEND_MODE_KEY`/`_sendMode` with localStorage persistence and validation; `modeSelect.addEventListener('change')` handler updates button label/aria-label and persists selection; separate `queueBtn`/`steerBtn` click handlers merged into single `sendModeBtn.addEventListener('click')` dispatcher with `modeSelect.disabled` toggled alongside `sendModeBtn.disabled` at all sites. "Steer sent." note removed from `steer_ack` success path. Enter-during-turn branch added. All 16 existing queue/steer tests updated; 5 new tests added; `El.click()` added to test harness. Cycle-1 auto-fix added 2 `flushToolGroups` tests and corrected `style.css` dead rules, `aria-controls`, `aria-live`, and `send()` return guard.

Step 5b QA: BLOCKED — requires PowerAtlas running for browser verification. Deferred to Step 9b exhaustive QA.

---

### Phase 2: Steer trace — server frame and client rendering [QA] [P:1]

**Goal**: Add a `steer_sent` frame type to the server ring buffer, emit it from `_handle_steer` on success, and render it in the client as a dimmed user-band message.

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/templates/acp.html`, `tests/acp_page.test.mjs`, `README.md` (steer-sent description sentence)

**Covers**: SC-4, SC-5, SC-6 (partial — steer_sent frame tests)

**Prerequisite**: Phase 1 must be complete before starting Phase 2 on shared files (`acp.html`), since Phase 1 removes "Steer sent." from `steer_ack` and Phase 2 adds `steer_sent` handler in the same function.

#### Server changes (`acp.py`)

**Atomic edit — do both in the same commit**:

1. Add `"steer_sent"` to `SERVER_TYPES` (`acp.py:163`):

```python
SERVER_TYPES = frozenset({
    "session", "chunk", "rendered", "tool_call", "tool_update", "meta", "error",
    "agent_died", "session_closed", "history_truncated", "history", "thought",
    "subagents", "steer_ack", "steer_sent",        # ← add steer_sent
    "commands", "compaction", "commands_options_result", "commands_execute_result",
})
```

2. Update `_handle_steer` success path to gate the `_emit` on `queued` being truthy (find the block that calls `conn.send(steer_ack)` and replace it):

```python
# Before:
result = await _supervisor.steer(session_id, text)
conn.send(envelope("steer_ack", {"queued": result.get("queued", True)}, session_id))

# After:
result = await _supervisor.steer(session_id, text)
queued = result.get("queued", True)
conn.send(envelope("steer_ack", {"queued": queued}, session_id))
if queued:
    # Emit to ring buffer so the steer text is visible in the transcript
    # and survives WS reconnects (SC-4, SC-5). Not emitted on queued=False
    # (rejected steer) to avoid showing a band for an injection that didn't land.
    _emit(session_id, envelope("steer_sent", {"text": text}, session_id))
```

> **Rejected**: placing `_emit` outside the `try:` block — if `"steer_sent"` were missing from `SERVER_TYPES`, `envelope()` would raise `ValueError` and the `except Exception` handler would send a second `internal_error` frame after `steer_ack` already succeeded. **Use instead**: keep both `conn.send(steer_ack)` and `_emit(steer_sent)` inside the same `try:` block. Both changes above are one atomic edit; `SERVER_TYPES` is updated in the same commit.

#### CSS changes (`acp.html` inline `<style>`)

Add `.acp-msg-steer` after `.acp-msg-user` rule in `acp.html`'s inline `<style>` block:

```css
/* Steered messages: same band layout as user prompts, dimmed to distinguish
   injected text from a real turn-initiating prompt. No role label (blank). */
.acp-msg-steer {
  background: rgba(108, 140, 255, 0.06);
  border-left: 2px solid rgba(108, 140, 255, 0.4);
  margin: 0 -14px;
  padding: 6px 14px 6px 12px;
}
.acp-msg-steer .acp-msg-role { color: var(--text-dim); }
.acp-msg-steer .acp-msg-body { color: var(--text-dim); }
```

Note: `--accent:#6c8cff` = `rgb(108,140,255)`. Using literal `rgba()` rather than `color-mix()` for embedded-WebView compatibility, consistent with `--accent-dim:rgba(108,140,255,0.12)` at `style.css:17`.

#### JS changes (`acp.html`)

**Add `steer_sent` frame handler** in `handle()`, near the `steer_ack` handler:

```js
if (type === 'steer_sent') {
  // Steer text emitted to the ring buffer by _handle_steer on success.
  // Rendered as a dimmed user-band message (SC-4).
  // No !replaying guard — this frame IS the reconnect replay mechanism
  // for SC-5; suppressing it during replay would defeat the feature.
  var steerText = payload && typeof payload.text === 'string' ? payload.text : '';
  if (steerText) addMessage('steer', steerText);
  return;
}
```

Note: `addMessage('steer', text)` uses `body.textContent = text` — not innerHTML. Role label for `'steer'` falls through to the empty-string branch in addMessage's ternary, producing a blank role span. This is intentional (user requested no extra labels).

**WS-drop-before-steer_ack guard**: If the WS drops after the steer send but before `steer_ack`, `ws.onclose` restores `_steerPending` text to the textarea (user can retry). The ring buffer may or may not have the `steer_sent` frame (depends on whether the server processed the steer before the drop). On reconnect, if `steer_sent` replays AND `_steerPending` was null (steer was accepted but ack dropped), the band appears correctly. If `_steerPending` is still set (steer was never processed), the textarea also has the text — a contradictory state. Mitigate in the `steer_sent` handler:

```js
if (type === 'steer_sent') {
  var steerText = payload && typeof payload.text === 'string' ? payload.text : '';
  if (steerText) {
    // If _steerPending is still set (WS drop before steer_ack), the steer
    // text was restored to the textarea by ws.onclose. Clear it here since
    // the band confirms the steer was accepted by the server.
    if (_steerPending) {
      promptInput.value = '';
      promptInput.disabled = false;
      sendModeBtn.disabled = false;
      modeSelect.disabled = false;
      _steerPending = null;
      autoGrowPrompt();
      refreshComposerControls();
    }
    addMessage('steer', steerText);
  }
  return;
}
```

#### Test changes (`tests/acp_page.test.mjs`)

**Update `"steer_ack re-enables controls and shows note"` test** (~L7060):
- Rename to `"steer_ack re-enables controls"`.
- Remove assertion that transcript contains `'Steer sent'`.
- Assert that `sendModeBtn.disabled === false` and `promptInput.disabled === false` and `modeSelect.disabled === false`.

**New tests** (3 for Phase 2):
- `"steer_sent frame adds dimmed steer band"` — dispatch `{type:'steer_sent', payload:{text:'do X'}, sessionId}` through `handle()`; assert transcript contains `.acp-msg-steer` with body text `'do X'`.
- `"steer_sent frame renders during replay"` — feed `steer_sent` frame through `handle()` with `replaying = true`; assert the message IS added (no `!replaying` guard present).
- `"steer_sent frame with empty text is no-op"` — dispatch `{type:'steer_sent', payload:{text:''}}` through `handle()`; assert `transcriptEl` children count is unchanged (no new element appended).

**Exit criteria**:
- [x] `"steer_sent"` added to `SERVER_TYPES` in same commit as `_emit` call — run `python -c "from power_atlas.acp import SERVER_TYPES; assert 'steer_sent' in SERVER_TYPES"` to verify
- [x] `_emit` call in `_handle_steer` is gated on `if queued:` and placed inside the `try:` block
- [x] `steer_sent` frame handler added in client `handle()`, renders band only for non-empty text
- [x] `steer_sent` handler includes `_steerPending` cleanup for WS-drop-before-ack scenario
- [x] `.acp-msg-steer` CSS rule added to `acp.html` inline `<style>` block (per AGENTS.md, all /acp CSS is inline in acp.html, not style.css)
- [x] `README.md` updated: steer description updated to describe the dimmed transcript band and WS reconnect persistence
- [x] `node tests/acp_page.test.mjs` passes; actual count: **329/330** (326 baseline + 3 new; 1 pre-existing dashboard-link failure)
- [x] `.venv-PowerAtlas\Scripts\python -m pytest tests/ -x -q` passes (pre-existing dashboard-link failure only; `test_steer_frame_is_routed` updated for new steer_sent broadcast behavior)

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `steer_sent` emitted before `steer_ack` reaches client | Low — `conn.send(steer_ack)` is a queue push (never raises per `_Connection.send` docstring); `_emit` follows inside same `try:` block | No mitigation needed; order guaranteed by single-threaded async dispatch |
| `steer_sent` ring-buffer entry on `queued=False` path | High if unguarded | Plan gates `_emit` on `if queued:` — only truthy `queued` emits |
| `"steer_sent"` absent from `SERVER_TYPES` at runtime | High — `envelope()` raises `ValueError` | Both `SERVER_TYPES` update and `_emit` call are one atomic commit |
| WS drop before `steer_ack` — contradictory UI state | Medium — text in textarea AND band shown | `steer_sent` handler clears `_steerPending` if present when band renders |
| Parallel phases 1+2 merge conflict on `steer_ack` block | Medium | Serialization: Phase 1 removes "Steer sent." note; Phase 2 only adds `steer_sent` handler. No shared edit |
| Existing 16 tests reference old button IDs | Medium | Explicit update list in Phase 1; exit criterion verifies zero `steerBtn`/`queueBtn` references remain |
| `<select>` height in pywebview embedded WebView | Low | `height:100%` CSS fallback added alongside `align-items:stretch` |
| `localStorage` containing invalid stored mode | Low | Validation added: `if (_sendMode !== 'steer' && _sendMode !== 'queue') _sendMode = 'steer'` |

## 7) Verification

**Phase 1 — JS suite** (no server needed, no restart):
```powershell
node tests/acp_page.test.mjs
# Expected: 324/324 passed (pre-existing dashboard-link failure not included in count)
```

**Phase 2 — Python suite** (requires PowerAtlas restart for acp.py changes):
```powershell
.venv-PowerAtlas\Scripts\python -m pytest tests/ -x -q
# Expected: all pass
```

**Phase 2 — SERVER_TYPES verification**:
```powershell
.venv-PowerAtlas\Scripts\python -c "from power_atlas.acp import SERVER_TYPES; assert 'steer_sent' in SERVER_TYPES; print('ok')"
```

**Browser visual QA** (Phase 1 — no restart, hard reload):
- Open `/acp`, start a turn, type text → single button + select appears
- Change select to Queue → label updates; reload → persists
- Enter during turn (Steer mode) → steer frame sent; Enter during turn (Queue mode) → queued note appears

**Browser visual QA** (Phase 2 — requires PowerAtlas restart):
- Send a steer → dimmed blue band appears with steer text (no "Steer sent." note)
- Close tab, reopen `/acp`, reload session → band visible in replay

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Sentence "two stacked half-height buttons" → describe single button + mode selector | 1 |
| `README.md` | Sentence "a brief confirmation appears when the injection is accepted" → describe the dimmed transcript band | 2 |

## 9) Implementation Divergences from Plan

- **flushToolGroups at user-chunk boundary**: Added `if (role === 'user' && toolGroup) flushToolGroups()` in `appendChunk()` to fix tool-group display during live streaming when a user message arrives without a preceding `turn:end`. Rationale: discovered as a correctness gap while implementing the Enter-during-turn path; closely related to the transcript changes being made. Tests added in cycle-1 auto-fix.
- **flushToolGroups at post-replay tail**: Added `if (toolGroup) flushToolGroups()` after history replay to flush any open tool group at replay end. Rationale: same discovery, prevents orphaned tool-group state after a session load. Tests added in cycle-1 auto-fix.
- **Phase 2 CSS for .acp-msg-steer placed in acp.html inline `<style>`, not style.css**: Phase 2 `**File scope**` and `#### CSS changes` section named `style.css`, but AGENTS.md requires all /acp CSS in `acp.html`'s inline `<style>`. The exit criterion correctly names `acp.html`; the file-scope line and CSS-changes section heading were not updated to match.

## Review Log

### 2026-08-13 — Cycle 1 (via /qplan, 4 personas: Architect, Senior engineer, End-user advocate, Reliability engineer)

12 findings (2 High, 6 Medium, 4 Low). All auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | Plan's steerBtn update table had 6 rows and exit criterion said "8 call sites" — count inconsistent with actual 7 `.disabled` sites. | Fixed — exit criterion replaced with grep-based verification; table updated with "Also add modeSelect.disabled" column. |
| 2 | High | Phases 1 and 2 both touched `steer_ack` success block — parallel dispatch creates merge conflict. | Fixed — serialization design decision added; Phase 1 owns all `steer_ack` changes including "Steer sent." removal; Phase 2 only adds `steer_sent` handler. |
| 3 | High | `SERVER_TYPES` update and `_emit` call must be atomic — if separate, `envelope()` raises `ValueError` after `steer_ack` already succeeded. | Fixed — Phase 2 calls both edits one atomic commit; `> **Rejected**: …` note added; verification command added to exit criteria. |
| 4 | High | Plan said "14 existing queue/steer tests" but actual count is 16; 2 missed tests (`queued prompt not sent` variants) use `acpQueue` and will fail if not updated. | Fixed — SC-6 and all test counts updated to 16; missed tests added to update list. |
| 5 | Medium | `localStorage` corruption — invalid stored value sets `_sendMode` to unknown string, `sendModeBtn.textContent` shows raw garbage. | Fixed — validation block added: `if (_sendMode !== 'steer' && _sendMode !== 'queue') _sendMode = 'steer'`; new test added. |
| 6 | Medium | README update split across phases incorrectly — button-structure sentence belongs in Phase 1, steer-description in Phase 2. | Fixed — Documentation Updates table split accordingly; Phase 1 exit criterion updated. |
| 7 | Medium | `modeSelect` not disabled during steer in-flight — user can change mode mid-flight. | Fixed — `modeSelect.disabled = true/false` added to click handler steer path and all 6 re-enable sites. |
| 8 | Medium | WS drop before `steer_ack` leaves contradictory state: textarea text restored AND steer band shown. | Fixed — `steer_sent` handler clears `_steerPending` when present, preventing dual state. |
| 9 | Medium | Phase 1 exit criterion said "319/319" — wrong (5 new tests added in Phase 1). | Fixed — updated to 324/324. |
| 10 | Medium | Exit criteria didn't verify all `_steerPending` recovery paths restore `sendModeBtn.disabled`, `promptInput.disabled`, and `modeSelect.disabled`. | Fixed — explicit exit criterion bullet added covering all 5 recovery paths. |
| 11 | Low | `sendModeBtn` had no `aria-label` beyond button text; mode change needed to update `aria-label` too. | Fixed — `aria-label` set at init and updated by change handler. |
| 12 | Low | Line numbers in Section 1 were ~50 lines off from actual file. | Fixed — Section 1 now says "approximate" and instructs implementer to grep for exact locations. |

### 2026-08-13 — Implementation Review (after Phase 1, personas: Senior engineer, End-user advocate, Maintainability reviewer, Reliability engineer)

Implementation health: Green (after cycle-1 auto-fix).
2 cycles. Cycle 1: 9 findings (3 Medium, 6 Low) — all auto-fixed. Cycle 2: 2 findings (0 Medium, 1 Low + 1 refuted).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | `style.css` dead `.acp-queue-steer {flex-direction:column}` and `.acp-queue,.acp-steer` rules not removed; old column rule creates fragile cascade dependency. | Fixed — dead CSS rules deleted from style.css; comment updated. |
| 2 | Medium | Two `flushToolGroups()` hunks added out of Phase 1 scope with no tests and not in divergences log. | Fixed — 2 tests added; Section 9 divergences log filled. |
| 3 | Medium | `aria-controls="acpSendMode"` missing on `modeSelect`; SR user doesn't know select controls adjacent button. | Fixed — attribute added to select element. |
| 4 | Medium | `send()` return value not checked in steer path; if WS CLOSING, controls disabled with no immediate recovery. | Fixed — `if (!send(...)) { restore controls; return; }` guard added. |
| 5 | Low | Error-frame steer recovery test missing `modeSelect.disabled === false` assertion (5th recovery path). | Fixed — assertion added. |
| 6 | Low | Stale HTML comment near `#acpQueueSteer` described "two half-height buttons". | Fixed — comment updated. |
| 7 | Low | No `aria-live` region for mode change announcement. | Fixed — visually-hidden `acpModeLiveRegion` span added; updated by change handler. |
| 8 | Low | `agent_died` handler assigned `_steerPending = null` before `disabled = false` lines — ordering inconsistency. | Fixed — moved `_steerPending = null` after control re-enable lines. |
| C2-1 | Low | Missing `.acp-queue-steer` display/flex rule (cycle-2 finding). | Refuted — `display:flex;flex-direction:row` rule exists in acp.html inline `<style>` at L498, correct per AGENTS.md. |
| C2-2 | Low | Phase 1 exit criterion test count stale (324 vs actual 326). | Fixed — exit criterion updated to 326/327. |
| F8 | Low | README leaves gap between Phase 1 (note removed) and Phase 2 (band not yet landed). | User: accepted — Phase 2 lands the band description; documenting an interim state would mislead. README Phase-1 update correctly describes the button structure only. |

## Harness Improvement Opportunities

- Skill check-in (depth recommendation) should be stated at the top of Step 2 before first question — cost: user correction round. Suggested change: add an explicit "state depth level before first question" step to the Kiro /qexplore overlay.
