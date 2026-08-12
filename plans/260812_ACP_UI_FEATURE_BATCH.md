# ACP UI Feature Batch

> **Date**: 2026-08-12
> **Status**: In Progress
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Eight /acp page improvements: image inline, steer/queue, auto-reconnect, new dot colors, rail refresh triggers, cancel-cascades-to-subagents, subagent timer freeze, prompt navigation arrows
> **Estimated effort**: 2–3 days

---

## Intent

### Problem statement & desired outcomes

The /acp page has accumulated several UX rough edges that make it feel disconnected from the conversation flow:

- Pasted images appear only in the tray chip — the prompt textarea gives no indication of where an image was inserted, making multi-image prompts feel disconnected from the text.
- There is no way to send a follow-up while an agent turn is running. Users lose any text typed during a turn because pressing Send is silently blocked.
- WebSocket drops require a manual button press to reconnect — there is no automatic recovery.
- The session dot color scheme is a 3-state system (green/amber/red) that conflates "busy" with "unread" and has no idle state. A session that finished while the user was elsewhere looks the same as one in an active turn.
- The rail dot only updates on a 60-second polling cycle. After sending a prompt or pressing Stop, the dot takes up to a minute to reflect the new state.
- Pressing Stop while subagents are running leaves the crew bar frozen mid-state. kiro-cli never emits terminal status for subagents after a parent cancel, so the bar only clears when the next fan-out begins.
- The subagent one-liner timer keeps incrementing for done subagents whenever any other subagent in the same crew updates. The `elapsedText()` function is defined but never called; the sublabel shows static "done".
- There is no way to navigate between user prompts in a long conversation. The user must scroll manually to find an earlier prompt.

### Success criteria

1. **SC-1 Image inline**: pasting an image into the textarea inserts `[Image N]` at the cursor position. The tray chip still appears. Removing an attachment uses the tray chip × button — deleting the `[Image N]` marker from the textarea does NOT remove the attachment (the agent still receives it).
2. **SC-2 Queue/Steer**: when a turn is active and the textarea is non-empty, the Stop button is replaced by two vertically-stacked half-height buttons: `[Queue]` (top) and `[Steer]` (bottom). When the textarea is empty, Stop shows as before. Queue sends the text as a normal `session/prompt` the instant the turn ends. Steer injects the text mid-turn via `_session/steer`. The server must expose a new `steer` frame type and call `_session/steer` on the ACP channel.
3. **SC-3 Auto-reconnect**: when a WebSocket that was previously open drops (`opened===true` on close), reconnection is attempted automatically with exponential backoff (1s, 2s, 4s, 8s… capped at 30s). The manual Reconnect button remains as a fallback. The stale-token path (`opened===false`) is unchanged — it shows the Reload page button only.
4. **SC-4 Dot colors**: the rail dot uses a 5-state scheme: white=idle (held, no turn, no unread), green=unread (held session whose last turn ended while the tab was not on that session), blue=thinking (`working` status), amber=approval (`waiting` status), red=error (`errored` status). Non-held sessions (available/locked) get no dot. Unread state is client-side only (localStorage), applied to PowerAtlas-held sessions only. Marking unread: on `meta turn:end` for a session not currently open. Clearing unread: on `subscribe` to that session.
5. **SC-5 Rail refresh triggers**: `railRefreshSoon()` is called after a prompt send succeeds and after a cancel (Stop) succeeds, in addition to the existing 60-second timer and other existing triggers.
6. **SC-6 Cancel cascades to subagents**: when the parent session cancel is processed in `_handle_cancel`, all non-done crew entries for that session are immediately marked `done=true` server-side (with `stoppedAt` set), and a `subagents` frame is emitted. This is a local state update — kiro-cli never emits terminal subagent status after a parent cancel (verified by live probe: 11 post-cancel `list_update` frames, all children still `working`).
7. **SC-7 Subagent timer freeze**: the server adds `stoppedAt: time.time()` to a crew entry when `done=true` is first set, and includes it in `_subagents_payload`. The JS `elapsedText()` function (currently dead code) is wired into `renderAgentBar()` with a per-second update interval. For done entries, elapsed is capped at `stoppedAt - startedAt` so it does not keep incrementing.
8. **SC-8 Prompt navigation**: two floating arrow buttons (`↑` / `↓`) positioned at the bottom-left of the transcript div, stacked vertically. Up scrolls to the previous user message. Down scrolls to the next user message, or to `transcriptEl.scrollHeight` if already at the last one. Hidden when fewer than 2 user messages are present.

### Scope boundaries & non-goals

In scope:
- `src/power_atlas/templates/acp.html` — all JS/CSS/HTML changes
- `src/power_atlas/acp.py` — server-side steer frame, cancel cascade, stoppedAt, crew payload
- `src/power_atlas/static/style.css` — new dot color CSS classes
- `tests/acp_page.test.mjs` — JS behavior tests
- `tests/test_web.py` — server-side tests for steer, cancel cascade, stoppedAt

Out of scope:
- v3 kiro-cli agent engine support (existing acp.py boundary)
- Claude Code ACP sessions (steer is kiro-cli only; `supports_steer` check degrades gracefully)
- Per-device unread sync across multiple browsers/tabs (localStorage is per-device by design)
- Persisting unread state across PowerAtlas restarts (ephemeral localStorage is sufficient)
- Sub-agent cancellation via kiro-cli ACP (verified impossible — child cancel notifications are silently ignored)

---

## 1) Current State

All changes target three files:

- `src/power_atlas/acp.py` (225 KB) — the ACP supervisor. `CLIENT_TYPES` (`acp.py:196`) is a frozenset of inbound frame types; `SERVER_TYPES` (`acp.py:204`) is a frozenset of outbound frame types. `_handle_cancel` (`acp.py:3961`) sends `session/cancel` to the agent but does not touch `crews`. `_on_subagent_list` (`acp.py:2454`) marks `done=True` on terminal status but never sets `stoppedAt`. `_subagents_payload` (`acp.py:3040`) builds the wire list — no `stoppedAt` field today. `_emit_subagents_frame` is called from `_on_subagent_list` and `_note_subagent_action`.
- `src/power_atlas/templates/acp.html` (253 KB) — the entire /acp frontend. Key state variables: `turnActive`, `attachments[]`, `crew[]`, `sessionId`, `queueBtn`/`steerBtn` do not exist. `stopBtn` shows/hides based solely on `turnActive` (`acp.html:~4887`). `ws.onclose` shows `reconnectBtn` when `opened===true` but does not auto-retry (`acp.html:~4897`). `elapsedText()` defined at `acp.html:999` — never called. `renderAgentBar()` at `acp.html:2315` — no timer drives it. Rail dot drawn only for held sessions; current states: `working`/`waiting`/`errored` (`acp.html:~3427`, `style.css:191–207`).
- `src/power_atlas/static/style.css` — dot colors: `status-working=#22c55e`, `status-waiting=#f59e0b`, `status-errored=#ef4444` (`style.css:191–207`). Composer row: `stopBtn` styled via `.acp-send.acp-danger`. No `status-idle`, `status-unread`, `status-thinking` classes exist.

**Protocol facts (verified by live probe, 2026-08-12, kiro-cli 2.16.x):**
- `_session/steer` is a JSON-RPC **request** (has `id`, returns `{"result": {"queued": true}}`). Use `_request`, not `_notify`. Echo arrives as `session/update` with `sessionUpdate: "AgentExecutionSteeringInjected"`.
- `session/cancel` for a child session id on the parent channel is silently ignored by kiro-cli.
- After parent cancel, kiro-cli never emits terminal status for child sessions (11 post-cancel `list_update` frames observed, all `working`).

## 2) Goal

Add 8 UX improvements to `/acp`: inline image markers, mid-turn queue/steer controls, auto-reconnect, revised dot color scheme, faster rail refresh, cancel-cascade for subagent crew cards, frozen subagent timer, and prompt navigation arrows.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| `_session/steer` is a request not a notification | Use `_request("_session/steer", ...)` in `acp.py` | `_notify` (no id) | Live probe confirmed: returns `{"result": {"queued": true}}` with matching id. Discovery item R6 was wrong. |
| No `supports_steer` capability flag | Always offer Steer when turn is active (kiro-cli only path) | Check agentCapabilities | `agentCapabilities` from `initialize` has no steer flag. `acp.py` spawns kiro-cli — it always supports steer. |
| `_session/steer` raw text, no wrapping | Send message as plain text | KiroCrew's `<user_message>\n...\n</user_message>` envelope | Live probe with raw text returned `{"queued": true}` and `AgentExecutionSteeringInjected`. Wrapping is KiroCrew's convention, not required. |
| Queue/Steer as two stacked half-height buttons | Replace Stop with `[Queue]`/`[Steer]` when textarea non-empty | Dropdown, single button | User decision. Clears empty-textarea case to still show Stop. |
| Unread state in localStorage, client-side only | `localStorage['pa_unread_' + sid]` | Server-side flag | Per-viewer concept. Works correctly on phone vs laptop. No server changes needed. |
| Cancel cascade via local state update, not kiro-cli | Mark crew `done=True` in `_handle_cancel`, emit `subagents` frame | Send child cancel notifications | kiro-cli ignores child cancel on parent channel (probe-verified). Local update is the only option. |
| `stoppedAt` added to crew entry on first `done=True` | Set in `_on_subagent_list` and `_handle_cancel` | Only in `_subagents_payload` | Allows wire transmission of a stable freeze point. Must be set at transition time, not on read. |
| `_session/steer` timeout | Use `REQUEST_TIMEOUT_SECONDS` (90s) | Custom shorter timeout | Steer answers in milliseconds (probe: `{"queued": true}` immediate). Standard timeout is safe ceiling. |
| Auto-reconnect backoff | 1s → 2s → 4s → 8s → … → 30s cap | Immediate retry, fixed 5s | Standard exponential backoff; prevents hammering a restarting server. |

## Harness Improvement Opportunities

- The ACP probe pattern (spawn kiro-cli as a subprocess, drive JSON-RPC directly) proved useful for verifying behavioral assumptions before writing code. Consider documenting it in `plans/tests/HARNESS.md` as a reusable technique for ACP protocol questions — cost: ~15 min per verification saved vs. runtime testing.
## 4) External Dependencies & Costs

### Required external changes

None. All changes are local to PowerAtlas source files. No infrastructure, IAM, CI/CD, DNS, or third-party service changes required.

### Cost impact

None. No new API calls, cloud resources, or licensing changes.

## 5) Implementation Phases

### Phase 1: Server-side ACP changes (steer, cancel cascade, stoppedAt) [QA]

**Goal**: Add `steer` as a new client frame type, cascade cancel to crew cards, and add `stoppedAt` to crew entries and the wire payload.

**File scope**: `src/power_atlas/acp.py`, `tests/test_web.py`

**Covers**: SC-2 (server half), SC-6, SC-7 (server half)

**Changes**:

1. Add `"steer"` to `CLIENT_TYPES` frozenset (`acp.py:196`).

2. Add a `_handle_steer` async function. It runs the same guards as `_handle_prompt`: session_id present, not subagent session, subscribed (`conn.session_id == session_id`), not closing. Additionally require turn-in-flight — steer when no turn is active would hang for 90s waiting for kiro-cli to reject it:
   ```python
   async def _handle_steer(conn, session_id, payload):
       if not session_id:
           conn.send(error_frame("bad_envelope", "'steer' needs a sessionId."))
           return
       if session_id in _supervisor.subagent_sessions:
           conn.send(error_frame("read_only_session", _READ_ONLY_SUBAGENT_MESSAGE, session_id))
           return
       if session_id not in _supervisor.sessions:
           conn.send(error_frame("unknown_session", "This server has no such live session.", session_id))
           return
       if conn.session_id != session_id:
           conn.send(error_frame("not_subscribed", "Subscribe to this session first.", session_id))
           return
       if session_id in _supervisor.closing:
           conn.send(error_frame("close_in_progress", "Session is being released.", session_id))
           return
       if session_id not in _supervisor.inflight:
           conn.send(error_frame("no_turn_in_progress",
               "No turn is running — steer is only available during an active turn.", session_id))
           return
       text = (payload.get("prompt") or "").strip()
       if not text:
           conn.send(error_frame("bad_payload", "Steer message must not be empty.", session_id))
           return
       try:
           result = await _supervisor.steer(session_id, text)
           conn.send(envelope("steer_ack", {"queued": result.get("queued", False)}, session_id))
       except AcpError as exc:
           conn.send(error_frame(exc.code, str(exc), session_id))
       except Exception as exc:
           log.exception("ACP _handle_steer: unexpected error")
           conn.send(error_frame("internal_error", "Steer failed unexpectedly.", session_id))
   ```

3. Add `steer` to `_dispatch` routing (`acp.py` in the `_dispatch` function, after the `cancel` arm):
   ```python
   if type_ == "steer":
       _spawn_task(_handle_steer(conn, session_id, payload))
       return
   ```

4. Add `_Supervisor.steer` method:
   ```python
   async def steer(self, session_id: str, text: str) -> dict:
       """Inject a mid-turn steer via _session/steer."""
       if session_id not in self.sessions:
           raise AgentRejected("That session no longer exists on this agent.")
       if not self.alive():
           raise AgentDied("The agent is not running.")
       return await self._request(
           "_session/steer",
           {"sessionId": session_id, "message": text},
       ) or {}
   ```

5. In `_handle_steer`, on success emit a `steer_ack` frame to the requesting socket only (not broadcast):
   ```python
   conn.send(envelope("steer_ack", {"queued": result.get("queued", False)}, session_id))
   ```
   Add `"steer_ack"` to `SERVER_TYPES`. Error paths emit an `error` frame (already in `SERVER_TYPES`) via the try/except in the handler body.

6. In `_handle_cancel`, after `await _supervisor.cancel(session_id)`, add:
   ```python
   # Mark all running crew entries done locally — kiro-cli never emits
   # terminal subagent status after a parent cancel (probe-verified, 2026-08-12).
   crew = _supervisor.crews.get(session_id)
   if crew:
       changed = False
       now = time.time()
       for entry in crew.values():
           if not entry["done"]:
               entry["done"] = True
               if not entry.get("stoppedAt"):   # guard: preserve more-precise kiro timestamp if already set
                   entry["stoppedAt"] = now
               changed = True
       if changed:
           try:
               _emit_subagents_frame(session_id)
           except Exception:
               log.exception("ACP cancel cascade: failed to emit subagents frame")
   ```

7. In `_on_subagent_list`, when transitioning an entry to `done=True` for the first time, set `stoppedAt`:
   ```python
   "stoppedAt": existing["stoppedAt"] if (existing and existing.get("stoppedAt")) else (time.time() if done else None),
   ```
   The `updated` dict at `acp.py:2530` must carry `stoppedAt`.

8. In `_subagents_payload` (`acp.py:3040`), add `"stoppedAt": entry.get("stoppedAt")` to each entry dict.

**Tests** (add to `tests/test_web.py`):
- `test_steer_frame_is_routed`: send a `steer` client frame to a subscribed session; assert `_supervisor.steer` is called with correct args.
- `test_steer_refused_for_subagent_session`: `steer` frame on a subagent session id → `read_only_session` error.
- `test_cancel_marks_crew_done_and_emits_subagents`: after cancel, crew entries that were active are `done=True` with `stoppedAt` set, and a `subagents` frame is broadcast.
- `test_stoppedAt_set_on_list_update_terminal`: when `_on_subagent_list` receives a terminal status, `stoppedAt` is set on the entry.
- `test_stoppedAt_in_subagents_payload`: `_subagents_payload` includes `stoppedAt` field.

**Exit criteria**:
- [x] `"steer"` in `CLIENT_TYPES`, `"steer_ack"` in `SERVER_TYPES`
- [x] `_handle_steer` routes correctly; subagent session → `read_only_session` error
- [x] `_supervisor.steer()` calls `_request("_session/steer", ...)` (not `_notify`)
- [x] `_handle_cancel` marks all active crew entries `done=True` with `stoppedAt` and emits `subagents` frame
- [x] `stoppedAt` set in `_on_subagent_list` on first `done=True` transition
- [x] `stoppedAt` present in `_subagents_payload` output
- [x] All 5 new tests pass: `pytest tests/test_web.py -k "steer or cancel_marks_crew or stoppedAt" -x`
- [x] Full suite green: `pytest tests/test_web.py -x`

#### Implementation (2026-08-12, code: 12d7690 + 19d3689 + 272c7e3 + 0d118e1)
Phase 1 adds server-side support for mid-turn steering, cancel cascades to subagent crew entries, and the `stoppedAt` freeze timestamp. `CLIENT_TYPES` gains `"steer"` and `SERVER_TYPES` gains `"steer_ack"`. A new `_handle_steer` async handler runs six guards (session_id present, not subagent, session exists, subscribed, not closing, turn in-flight) plus an isinstance guard on the payload value before calling the new `_Supervisor.steer()` method — which issues `_request("_session/steer", ...)` (a JSON-RPC request with id, not a notify) — and sends a `steer_ack` frame to the requesting socket only. The handler is wired into `_dispatch` after the `cancel` arm. In `_handle_cancel`, a `_mark_crew_done(crew, now)` helper (extracted to avoid duplication with `_on_subagent_list`) marks every non-done crew entry `done=True` with `stoppedAt = time.time()` (preserving any more-precise timestamp kiro-cli already set), and calls `_emit_subagents_frame` in a try/except — critically guarded by `return` in the `except AcpError` branch so the cascade never runs on a rejected cancel. The steer payload key uses `"message"` (matching `_Supervisor.steer`'s own wire param and the live-probe spec). In `_on_subagent_list`, the `updated` dict carries `stoppedAt`, stamped at transition time or preserved from the existing entry. `_subagents_payload` includes `"stoppedAt": entry.get("stoppedAt")` in each wire entry. All 5 plan-required tests plus 11 additional guard/edge-case tests cover all scenarios. Full suite: 1234 passed, 0 failures.

---

### Phase 2: Client-side steer/queue controls and image inline [QA]

**Goal**: Add Queue/Steer buttons, image-inline marker, and handle the `steer` server→client flow.

**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/static/style.css`, `tests/acp_page.test.mjs`

**Covers**: SC-1, SC-2 (client half)

**Changes**:

**HTML additions** (in the composer row, alongside existing `acpStop`):
```html
<button class="acp-btn acp-queue" id="acpQueue" type="button" hidden>Queue</button>
<button class="acp-btn acp-steer" id="acpSteer" type="button" hidden>Steer</button>
```
These sit in the same `.acp-composer-row` div as `acpSend` and `acpStop`.

**CSS** (`style.css`): stack Queue and Steer vertically at half the Stop button height:
```css
.acp-queue, .acp-steer {
  display: flex;
  flex-direction: column;
  height: calc(var(--send-btn-height, 2.5rem) / 2);
  line-height: 1;
  font-size: 0.75rem;
}
/* Wrapper or grouping div if needed to stack them in one column */
```
(Exact sizing to match the current Stop button height measured at implementation time.)

**JS variables** (add near existing declarations):
```js
var queueBtn = document.getElementById('acpQueue');
var steerBtn = document.getElementById('acpSteer');
var queuedPrompt = null;   // text held for auto-send on turn end
```

**`refreshComposerControls()` — new helper** (replaces ad-hoc show/hide of stopBtn/sendBtn):
```js
function refreshComposerControls() {
  var hasText = promptInput.value.trim().length > 0;
  // Send: visible when not in a turn
  sendBtn.hidden = turnActive;
  sendBtn.disabled = !sessionId || !ws || ws.readyState !== WebSocket.OPEN;
  // Stop: visible during turn only when textarea is empty
  stopBtn.hidden = !turnActive || hasText;
  // Queue+Steer: visible during turn only when textarea has text
  queueBtn.hidden = !turnActive || !hasText;
  steerBtn.hidden = !turnActive || !hasText;
}
```
Call `refreshComposerControls()` everywhere `sendBtn.hidden`, `stopBtn.hidden`, `stopBtn.disabled` are currently set directly, **including inside `setTurn()`** — `setTurn()` currently sets these directly and must be updated to call `refreshComposerControls()` instead. The "Stopping…" label and `disabled=true` set by the Stop button click handler are reset by `setTurn(false)` calling `refreshComposerControls()`.
Also call it in the `promptInput` `input` event handler (to show/hide Queue/Steer as the user types).

**Queue handler**:
```js
queueBtn.addEventListener('click', function() {
  var text = promptInput.value.trim();
  if (!text || !turnActive) return;
  queuedPrompt = text;
  promptInput.value = '';
  autoGrowPrompt();
  refreshComposerControls();
  addMessage('note', 'Prompt queued — will send when agent finishes.');
});
```

**Auto-send on turn end** (in the `meta turn:end` handler, before `setTurn(false)`):
```js
var toSend = queuedPrompt;
var queueSession = queuedPromptSession;  // session id at queue time
queuedPrompt = null;
queuedPromptSession = null;
```
After `setTurn(false)`:
```js
if (toSend) {
  if (queueSession && queueSession !== sessionId) {
    // Session changed between queue and turn-end — discard to avoid sending to wrong session
    addMessage('note', 'Queued prompt discarded — session changed.');
  } else if (!ws || ws.readyState !== WebSocket.OPEN) {
    // Connection dropped — restore text so user can retry
    promptInput.value = toSend;
    autoGrowPrompt();
    addMessage('note', 'Could not send queued prompt — connection lost.');
  } else if (!promptInput.value.trim()) {
    promptInput.value = toSend;
    autoGrowPrompt();
    sendPrompt();
  } else {
    // User typed something after queuing — don't clobber it; discard queue silently
    addMessage('note', 'Queued prompt discarded — you typed a new prompt.');
  }
}
```
Add variable `var queuedPromptSession = null;` near `var queuedPrompt = null`.
In the Queue handler, set `queuedPromptSession = sessionId;` alongside `queuedPrompt = text`.
In `releaseSession()`, add `queuedPrompt = null; queuedPromptSession = null;` to prevent firing against the next session.

**Steer handler** (confirmed UX — text held until `steer_ack`):
```js
var _steerPending = null;  // text in-flight for steer, held for restoration on error

steerBtn.addEventListener('click', function() {
  var text = promptInput.value.trim();
  if (!text || !turnActive || !sessionId) return;
  _steerPending = text;
  promptInput.value = '';
  promptInput.disabled = true;
  steerBtn.disabled = true;
  autoGrowPrompt();
  refreshComposerControls();
  send('steer', {message: text}, sessionId);
  // Do NOT show "Steer sent." yet — wait for steer_ack
});
```

**`steer_ack` frame handler** in `handle()`:
```js
if (frame.type === 'steer_ack') {
  promptInput.disabled = false;
  steerBtn.disabled = false;
  _steerPending = null;
  if (frame.payload && frame.payload.queued === false) {
    addMessage('error', 'Steer was not accepted by the agent.');
  } else {
    addMessage('note', 'Steer sent.');
  }
  refreshComposerControls();
  return;
}
```
On `error` frames for session steer (code `steer_not_inflight`, `agent_error`, etc.): restore textarea text:
```js
// In the error frame handler, add a steer-restoration path:
if (_steerPending) {
  promptInput.value = _steerPending;
  promptInput.disabled = false;
  steerBtn.disabled = false;
  _steerPending = null;
  autoGrowPrompt();
  refreshComposerControls();
}
```

**Queue cancel mechanism**: instead of a plain note, the queue note shows an inline cancel link:
```js
// Queue handler — replace the addMessage('note', ...) call:
var queueNoteEl = addMessage('note', '');
// Build: "Queued: [preview…] — " + [Cancel link]
var preview = text.length > 30 ? text.slice(0, 30) + '…' : text;
queueNoteEl.textContent = 'Queued: ' + preview + ' — ';
var cancelLink = document.createElement('button');
cancelLink.className = 'acp-inline-cancel';
cancelLink.type = 'button';
cancelLink.textContent = 'Cancel';
cancelLink.addEventListener('click', function() {
  if (!queuedPrompt) return;
  promptInput.value = queuedPrompt;
  queuedPrompt = null;
  queuedPromptSession = null;
  autoGrowPrompt();
  refreshComposerControls();
  queueNoteEl.textContent = 'Queued prompt cancelled.';
  cancelLink.remove();
});
queueNoteEl.appendChild(cancelLink);
```
Add minimal CSS for `.acp-inline-cancel` (a small inline button styled to look like a link).

**Image inline** (SC-1): in the image staging function, after the tray chip is added (i.e., after `attachments.push(item)` and `renderTray()`):
```js
// Insert [Image N] at cursor in the textarea
var marker = '[Image ' + attachments.length + ']';
var start = promptInput.selectionStart || promptInput.value.length;
var end = promptInput.selectionEnd || start;
promptInput.value = promptInput.value.slice(0, start) + marker + promptInput.value.slice(end);
promptInput.selectionStart = promptInput.selectionEnd = start + marker.length;
promptInput.focus();
autoGrowPrompt();
refreshComposerControls();
```

**Attachment removal** (`removeAttachment`): after splicing `attachments`, re-number any `[Image N]` markers in `promptInput.value` — use a full replacement pass, not a sequential loop:
```js
// Re-number [Image N] markers after removal: rebuild from scratch
var renum = promptInput.value;
// Shift every [Image K] where K > index+1 down by one
for (var i = index + 1; i <= attachments.length + 1; i++) {
  renum = renum.replace(new RegExp('\\[Image ' + (i + 1) + '\\]', 'g'), '[Image ' + i + ']');
}
promptInput.value = renum;
```

**Tests** (`tests/acp_page.test.mjs`):
- `test_image_inline_marker_inserted_at_cursor`: paste an image with cursor at position 3; assert textarea contains `[Image 1]` at position 3.
- `test_queue_button_hidden_when_textarea_empty`: `turnActive=true`, textarea empty → `queueBtn.hidden===true`, `stopBtn.hidden===false`.
- `test_queue_button_visible_when_turn_active_and_text_present`: `turnActive=true`, textarea has text → `queueBtn.hidden===false`, `stopBtn.hidden===true`.
- `test_queue_stores_text_and_clears_textarea`: click queue → `queuedPrompt` set, `promptInput.value===''`.
- `test_steer_sends_frame_and_clears_textarea`: click steer → WS send called with `{type:'steer', payload:{message:...}}`.
- `test_queued_prompt_auto_sends_on_turn_end`: simulate `meta turn:end` with `queuedPrompt` set → `sendPrompt` called.

**Exit criteria**:
- [x] Queue and Steer buttons present in HTML; stacked vertically at half Stop height
- [x] `refreshComposerControls()` governs all three button states correctly; `setTurn()` calls it
- [x] Pasting image inserts `[Image N]` at cursor; subsequent paste inserts `[Image 2]` etc.
- [x] Removing attachment at index 0 via tray × renumbers remaining markers with global replace
- [x] Queue button stores text and session id, clears textarea, shows cancellable inline note
- [x] Queue cancel link restores text to textarea and clears `queuedPrompt`
- [x] Auto-send fires on `meta turn:end` when `queuedPrompt` is set and session unchanged and WS open
- [x] Auto-send is a no-op (note shown) when session changed or WS closed
- [x] Auto-send does not clobber text the user typed after queuing
- [x] Steer button clears textarea immediately and disables textarea+button; text preserved in pending state for error restoration
- [x] `steer_ack {queued:true}` re-enables controls and shows note; `steer_ack {queued:false}` restores textarea text and shows error note
- [x] `steer_ack {queued:false}` or error frame restores textarea text and shows error note
- [x] `queuedPrompt` and `queuedPromptSession` cleared in `releaseSession()`
- [x] All new `acp_page.test.mjs` tests pass: `node tests/acp_page.test.mjs`
- [x] No regression in existing `acp_page.test.mjs` tests

#### Implementation (2026-08-12, code: 8900eb7 + 172eabd + 69b9d7a + 5987014)
Phase 2 adds client-side Queue/Steer controls and image-inline markers. HTML gains a `<div id="acpQueueSteer">` wrapper holding `<button id="acpQueue">Queue</button>` and `<button id="acpSteer">Steer</button>` in the composer row. CSS stacks them vertically at exactly half Stop's height with `height: calc(var(--send-btn-height, 2.5rem) / 2)` and `gap: 0`. A new `refreshComposerControls()` helper centralises all three button states — called by `setTurn()` and the textarea `input` event. Queue stores text + sessionId, clears textarea, and builds a cancellable inline note with a `<button class="acp-inline-cancel" aria-label="Cancel queued prompt">`. On `meta turn:end`, queued prompt auto-sends after three guards: session unchanged, WS open, no new user text; state cleared in `releaseSession()`. Steer sends `{message:text}` immediately clearing textarea (text held in `_steerPending`); `steer_ack {queued:false}` restores text before nulling pending; WS close and `agent_died` both restore text and re-enable controls; `releaseSession()` re-enables controls without text restoration. `_stopInProgress` flag prevents `refreshComposerControls()` from re-enabling Stop while a cancel is in-flight. Image inline inserts `[Image N]` at cursor position; `removeAttachment()` first removes the deleted marker, then renumbers higher ones with global regex. README updated at feature-list bullet and with a new Queue/Steer paragraph in the Agent sessions section. 254 JS + 1234 Python tests pass.

Divergence: `acpQueue`/`acpSteer` wrapped in `acpQueueSteer` `<div>` for atomic hide/show; `refreshComposerControls()` operates on the wrapper.

---

### Phase 3: Auto-reconnect and rail refresh triggers [QA]

**Goal**: Add exponential-backoff auto-reconnect on WebSocket drop, and trigger rail refresh on send and cancel.

**File scope**: `src/power_atlas/templates/acp.html`, `tests/acp_page.test.mjs`

**Covers**: SC-3, SC-5

**Changes**:

**JS variable** (near `var ws = null`):
```js
var _reconnectTimer = null;
var _reconnectDelay = 1000;  // ms; doubles each attempt, capped at 30000
```

**`ws.onopen`** — reset backoff on successful connection:
```js
_reconnectDelay = 1000;
if (_reconnectTimer) { clearTimeout(_reconnectTimer); _reconnectTimer = null; }
```

**`ws.onclose`** — replace the current `reconnectBtn.hidden = false` branch with:
```js
if (opened) {
  reconnectBtn.hidden = false;
  // Auto-reconnect with exponential backoff
  _reconnectTimer = setTimeout(function() {
    _reconnectTimer = null;
    connect();
  }, _reconnectDelay);
  _reconnectDelay = Math.min(_reconnectDelay * 2, 30000);
}
```
The `reconnectBtn` click still calls `connect()` directly. At the top of `connect()`, add:
```js
// Cancel any pending backoff timer — prevents double-connect when the user
// presses Reconnect while a timer is already scheduled.
if (_reconnectTimer) { clearTimeout(_reconnectTimer); _reconnectTimer = null; }
```

**Rail refresh after send** — in `sendPrompt()`, after the `send('prompt', ...)` call returns true:
```js
railRefreshSoon();
```

**Rail refresh after stop** — in the `stopBtn` click handler, after `send('cancel', ...)`:
```js
railRefreshSoon();
```

**Tests** (`tests/acp_page.test.mjs`):
- `test_auto_reconnect_scheduled_on_close_when_opened`: simulate `ws.onclose` with `opened=true` → `_reconnectTimer` is set.
- `test_reconnect_delay_doubles_on_each_close`: simulate two consecutive closes → delay doubles (1000 → 2000).
- `test_reconnect_delay_capped_at_30s`: after enough closes, delay stays at 30000.
- `test_reconnect_delay_resets_on_open`: simulate `ws.onopen` → `_reconnectDelay` reset to 1000.
- `test_no_auto_reconnect_when_not_opened`: simulate close with `opened=false` → no timer set.
- `test_rail_refresh_called_after_send`: `sendPrompt()` success → `railRefreshSoon` called.
- `test_rail_refresh_called_after_stop`: stop button click → `railRefreshSoon` called.

**Exit criteria**:
- [ ] `ws.onclose` schedules retry when `opened===true`; delay starts at 1s, doubles, caps at 30s
- [ ] `ws.onopen` resets `_reconnectDelay` to 1s and clears any pending timer
- [ ] Manual reconnect button still works (calls `connect()` directly)
- [ ] Stale-token path unchanged: `opened===false` → shows reload button only, no timer
- [ ] `railRefreshSoon()` called after `send('prompt', ...)` returns true
- [ ] `railRefreshSoon()` called after stop button press
- [ ] All new `acp_page.test.mjs` tests pass
- [ ] No regression in existing tests



### Phase 4: Dot color scheme [QA]

**Goal**: Replace the 3-state rail dot scheme with a 5-state scheme (idle/unread/thinking/approval/error), tracking unread state in localStorage.

**File scope**: `src/power_atlas/static/style.css`, `src/power_atlas/templates/acp.html`, `tests/acp_page.test.mjs`

**Covers**: SC-4

**CSS changes** (`style.css`):

Add new dot rules. **Do NOT remove `.session-status.status-working`** — the dashboard (`session_row.html`, `workspace_card.html`) uses it for process-detection dots. The /acp page uses `railDotClass()` to produce `status-thinking` for working sessions; the old `status-working` class is unreachable from `railDotClass()` but must remain for the dashboard:
```css
/* Idle — held session, no turn, no unread */
.session-status.status-idle {
  background: #e5e7eb;
}
/* Unread — turn ended while this session wasn't open */
.session-status.status-unread {
  background: #22c55e;
  animation: status-pulse 2s ease-in-out infinite;
}
/* Thinking — working status (replaces status-working for /acp rail) */
.session-status.status-thinking {
  background: #3b82f6;
  animation: status-pulse 2s ease-in-out infinite;
}
/* status-waiting and status-errored unchanged */
```

**JS changes** (`acp.html`):

Add localStorage helpers (near existing `railStored`/`railStore`):
```js
var UNREAD_PREFIX = 'pa_unread_';
function markUnread(sid) {
  if (!sid) return;
  try { localStorage.setItem(UNREAD_PREFIX + sid, '1'); } catch(e) {}
}
function clearUnread(sid) {
  if (!sid) return;
  try { localStorage.removeItem(UNREAD_PREFIX + sid); } catch(e) {}
}
function isUnread(sid) {
  try { return localStorage.getItem(UNREAD_PREFIX + sid) === '1'; } catch(e) { return false; }
}
```

**Unread marking** — the `handle()` function only receives frames for the *currently subscribed* session; `frame.sessionId` always equals `sessionId` there. Unread state must be marked from the rail poll response instead, since that delivers status for **all** held sessions.

In `railRefreshStates()` (or wherever the rail poll response updates session status): when a held session's status transitions from `working` → `idle` (i.e., a turn just ended) AND that session is not the one currently open (`session.sessionId !== sessionId`), mark it unread:
```js
function railRefreshStates(groups) {
  // ... existing status write-back logic ...
  groups.forEach(function(group) {
    (group.sessions || []).forEach(function(session) {
      var prev = _prevSessionStatus[session.sessionId];
      _prevSessionStatus[session.sessionId] = session.status;
      // Mark unread when a non-open held session transitions from working to idle
      if (session.availability === 'held'
          && session.sessionId !== sessionId
          && prev === 'working' && session.status !== 'working') {
        markUnread(session.sessionId);
      }
    });
  });
}
var _prevSessionStatus = Object.create(null);  // sessionId -> last known status
```

Clearing unread on subscribe remains in the `session` frame handler (where `subscribed = true` is set):
```js
clearUnread(payload.sessionId);
```

> Note: `railRefreshStates` is called every 60s and after `railRefreshSoon()`. The new SC-5 triggers (after send and after cancel) mean unread will be detected within seconds of a turn ending, not up to 60s later.

**Dot class computation** — in `railRowNode()` (`acp.html:~3395`), replace the `status-{status}` class logic:
```js
// New 5-state dot logic for held sessions
function railDotClass(session) {
  var avail = railAvailability(session.availability);
  if (avail !== 'held') return null;  // no dot for non-held
  var srv = railRowStatus(session.status);  // working/waiting/errored fallback:working
  if (srv === 'errored') return 'status-errored';
  if (srv === 'waiting') return 'status-waiting';
  if (srv === 'working') return 'status-thinking';
  // idle or unknown: check unread
  if (isUnread(session.sessionId)) return 'status-unread';
  return 'status-idle';
}
```
Replace `dot.className = 'session-status status-' + status` with:
```js
var dotClass = railDotClass(session);
if (dotClass) {
  dot.className = 'session-status ' + dotClass;
} else {
  dot.className = 'session-status'; // no color, no display
  dot.hidden = true;
}
```

Update `RAIL_STATUS` map to include `idle` and `unread` (though these are computed client-side, adding them for completeness and to satisfy the closed-set pattern):
```js
RAIL_STATUS.idle = 'idle';
RAIL_STATUS.unread = 'unread';
RAIL_STATUS.thinking = 'thinking';
```

Update `RAIL_STATUS_LABEL` for the dot's `aria-label`:
```js
RAIL_STATUS_LABEL.idle = 'open in this PowerAtlas — idle';
RAIL_STATUS_LABEL.unread = 'open in this PowerAtlas — unread messages';
RAIL_STATUS_LABEL.thinking = 'open in this PowerAtlas — the agent is working';
// working → thinking in the aria-label map too
delete RAIL_STATUS_LABEL.working;
```

**Tests** (`tests/acp_page.test.mjs`):
- `test_dot_class_thinking_when_working`: `status=working`, `availability=held`, no unread → class `status-thinking`.
- `test_dot_class_unread_when_idle_and_unread`: `status=idle`, `availability=held`, `isUnread=true` → class `status-unread`.
- `test_dot_class_idle_when_no_unread`: `status=idle`, `availability=held`, `isUnread=false` → class `status-idle`.
- `test_dot_absent_for_non_held`: `availability=available` → no dot element.
- `test_mark_unread_on_turn_end_for_other_session`: `meta turn:end` for session B when session A is open → `isUnread(B)===true`.
- `test_clear_unread_on_subscribe`: `session` frame received → `isUnread(sid)===false`.

**Exit criteria**:
- [ ] `status-working` CSS class removed; `status-thinking`, `status-idle`, `status-unread` added
- [ ] Held `working` session shows blue thinking dot
- [ ] Held `waiting` session shows amber dot
- [ ] Held `errored` session shows red dot
- [ ] Held idle session with unread shows green dot; without unread shows white dot
- [ ] Non-held sessions show no dot
- [ ] `meta turn:end` for non-active session marks it unread in localStorage
- [ ] `subscribe` (session frame) clears unread from localStorage
- [ ] All new tests pass; no regression

---

### Phase 5: Subagent timer and prompt navigation [QA]

**Goal**: Wire the `elapsedText()` function into the crew bar with a live timer that freezes when done; add floating prompt navigation arrows.

**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/static/style.css`, `tests/acp_page.test.mjs`

**Covers**: SC-7, SC-8

**Subagent timer** (SC-7):

Add a timer variable (near `var crew = []`):
```js
var _crewTimerInterval = null;
```

Update `setCrew(next)`:
```js
function setCrew(next) {
  crew = Array.isArray(next) ? next : [];
  crewAllDone = crew.every(function(e) { return e.done; });
  renderAgentBar();
  renderSubHead();
  // Start/stop the 1s interval
  if (_crewTimerInterval) { clearInterval(_crewTimerInterval); _crewTimerInterval = null; }
  if (crew.length > 0 && !crewAllDone) {
    _crewTimerInterval = setInterval(renderAgentBar, 1000);
  }
}
```

Also clear the interval in `releaseSession()` **explicitly** (in addition to relying on `setCrew([])`), to guard against any code path that releases a session without routing through `setCrew([])`:
```js
// In releaseSession():
if (_crewTimerInterval) { clearInterval(_crewTimerInterval); _crewTimerInterval = null; }
userMsgEls = [];
promptNavEl.hidden = true;
```

In `agentPill()`, update the sublabel for `working` entries:
```js
// Replace: sublabel = entry.action || 'working…'
// With:
var elapsed = elapsedText(entry.startedAt);
sublabel = state === 'error' ? 'errored'
         : state === 'done' ? elapsedText(entry.startedAt, entry.stoppedAt)
         : (entry.action ? entry.action + ' · ' + elapsed : elapsed);
```

Update `elapsedText()` to accept an optional `stoppedAt` cap:
```js
function elapsedText(startedAt, stoppedAt) {
  var cap = (stoppedAt != null) ? stoppedAt : (Date.now() / 1000);
  var secs = Math.max(0, Math.round(cap - startedAt));
  var m = Math.floor(secs / 60);
  var s = secs % 60;
  return m > 0 ? m + 'm ' + s + 's' : s + 's';
}
```

**Prompt navigation arrows** (SC-8):

**HTML** (inside `.acp-transcript`, after the transcript div opening or as adjacent elements):
```html
<div class="acp-prompt-nav" id="acpPromptNav" hidden>
  <button class="acp-btn acp-prompt-nav-btn" id="acpPromptUp" type="button"
          aria-label="Previous prompt">↑</button>
  <button class="acp-btn acp-prompt-nav-btn" id="acpPromptDown" type="button"
          aria-label="Next prompt or scroll to bottom">↓</button>
</div>
```
Positioned absolutely inside `acp-transcript` via CSS.

**CSS** (`style.css`):
```css
.acp-transcript { position: relative; }  /* if not already */
.acp-prompt-nav {
  position: absolute;
  bottom: 0.5rem;
  left: 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  z-index: 10;
}
.acp-prompt-nav-btn {
  padding: 0.25rem 0.5rem;
  opacity: 0.7;
  font-size: 0.875rem;
}
.acp-prompt-nav-btn:hover { opacity: 1; }
```

**JS variables**:
```js
var promptNavEl = document.getElementById('acpPromptNav');
var promptUpBtn = document.getElementById('acpPromptUp');
var promptDownBtn = document.getElementById('acpPromptDown');
var userMsgEls = [];  // DOM elements of user message rows, in order
```

Update `addMessage('user', ...)`:
```js
// After appending the row to transcriptEl:
if (role === 'user') {
  userMsgEls.push(row);
  promptNavEl.hidden = userMsgEls.length < 2;
}
```

Update `clearTranscript()`:
```js
userMsgEls = [];
promptNavEl.hidden = true;
```

**Up arrow handler**:
```js
promptUpBtn.addEventListener('click', function() {
  if (!userMsgEls.length) return;
  // Find the element currently closest to the top of the visible area
  var threshold = transcriptEl.scrollTop;
  var target = userMsgEls[0];
  for (var i = userMsgEls.length - 1; i >= 0; i--) {
    if (userMsgEls[i].offsetTop < threshold - 10) {
      target = userMsgEls[i];
      break;
    }
  }
  target.scrollIntoView({behavior: 'smooth', block: 'start'});
});
```

**Down arrow handler**:
```js
promptDownBtn.addEventListener('click', function() {
  if (!userMsgEls.length) return;
  var threshold = transcriptEl.scrollTop + 10;
  var target = null;
  for (var i = 0; i < userMsgEls.length; i++) {
    if (userMsgEls[i].offsetTop > threshold) {
      target = userMsgEls[i];
      break;
    }
  }
  if (target) {
    target.scrollIntoView({behavior: 'smooth', block: 'start'});
  } else {
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
  }
});
```

**Tests** (`tests/acp_page.test.mjs`):
- `test_elapsed_text_with_stopped_at_freezes`: `elapsedText(t, t+65)` returns `'1m 5s'` regardless of `Date.now()`.
- `test_elapsed_text_without_stopped_at_uses_now`: `elapsedText(Date.now()/1000 - 10)` returns `'10s'`.
- `test_crew_timer_starts_when_crew_not_all_done`: `setCrew([{done:false, ...}])` → interval set.
- `test_crew_timer_stops_when_all_done`: `setCrew([{done:true, ...}])` → interval not set.
- `test_prompt_nav_hidden_when_zero_or_one_user_message`: 0 user messages → hidden; 1 message → hidden; 2 messages → visible.
- `test_prompt_nav_cleared_on_clear_transcript`: `clearTranscript()` → `userMsgEls.length===0`, nav hidden.

**Exit criteria**:
- [ ] `elapsedText(startedAt, stoppedAt)` freezes at done elapsed; without `stoppedAt` uses live `Date.now()`
- [ ] Working crew entry shows live elapsed (updating every second)
- [ ] Done crew entry shows frozen elapsed
- [ ] Crew timer interval cleared when all done or crew empty
- [ ] Crew timer interval cleared when session is released
- [ ] Prompt nav buttons appear when ≥2 user messages, hidden otherwise
- [ ] Up arrow scrolls to previous user message
- [ ] Down arrow scrolls to next user message, or to bottom if at last
- [ ] `clearTranscript()` resets `userMsgEls` and hides nav
- [ ] All new tests pass; no regression

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `_session/steer` request timeout blocks turn | A stuck steer request holds the `_handle_steer` task for up to `REQUEST_TIMEOUT_SECONDS` | Steer answered in milliseconds (probe-verified); standard timeout is a safe ceiling. Consider a shorter dedicated timeout (5s) if probe results don't generalize. |
| Queue auto-send fires against wrong session | If `sessionId` changes between queue and turn-end, auto-send goes to the new session | Read `sessionId` at send time (not at queue time); if `sessionId` changed, clear `queuedPrompt` silently. |
| `userMsgEls` grows unbounded in long sessions | Memory accumulation for very long conversations | Acceptable — each entry is one DOM element reference; a conversation with 1000 user messages holds 1000 refs (~8 KB). |
| localStorage unavailable (private browsing, Safari ITP) | `isUnread()` always returns false, `markUnread()` silently fails | Wrapped in `try/catch`; graceful degradation — unread state simply not shown. |
| Auto-reconnect loop on invalid token | `diagnoseRejectedHandshake` runs after first failed `onopen`; but a reconnect scheduled by the previous close may fire before the diagnosis completes | The `opened` flag prevents scheduling a reconnect when `opened===false`; the diagnosis path only runs when `opened===false`. The backoff timer is set only when `opened===true`. No loop. |
| Dot color CSS collision with dashboard | `status-working` is used by both the dashboard (index.html) and /acp. Renaming it to `status-thinking` breaks the dashboard. | `status-working` must remain in `style.css` for the dashboard's dots (which use process-detection status, not RAIL_STATUS). Only add new classes; do not remove `status-working`. The /acp rail uses the new `railDotClass()` logic that maps `working` → `status-thinking`. |

## 7) Verification

```bash
# Python tests (server-side)
.venv-PowerAtlas\Scripts\pytest tests/test_web.py -x -v

# JS tests (template behavior)
node tests/acp_page.test.mjs

# Manual smoke test
.venv-PowerAtlas\Scripts\power-atlas
# Open http://127.0.0.1:<port>/acp
# 1. Paste an image → verify [Image 1] appears at cursor in textarea
# 2. Start a long-running turn → type text → verify Queue/Steer appear, Stop hidden
# 3. Press Queue → verify note shown, textarea cleared, prompt auto-sends on turn end
# 4. Start a turn → type text → press Steer → verify steer sent, textarea cleared
# 5. Kill PowerAtlas mid-session → verify auto-reconnect fires with backoff
# 6. Start a fan-out → press Stop → verify crew cards all show done immediately
# 7. Start a fan-out → verify timer increments on cards; let it finish → timer frozen
# 8. Build a long transcript → verify ↑/↓ arrows navigate between user messages
# 9. Verify dot colors: blue=thinking, amber=waiting, red=error, white=idle, green=unread
```

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` line 66 | Add "queue a prompt for after the current turn, or steer the agent mid-turn" to the /acp feature bullet | 2 |
| `README.md` Agent sessions section (~line 200) | Add paragraph describing Queue and Steer: when a turn is active and textarea is non-empty, Stop is replaced by Queue (sends on turn end) and Steer (injects mid-turn) | 2 |

## 9) Implementation Divergences from Plan

- Phase 1: `_mark_crew_done` helper extracted to avoid duplicating done-marking + stoppedAt-stamping logic between `_handle_cancel` and `_on_subagent_list`. Behavior-identical refactor; improves maintainability.
- Phase 1: Steer payload key named `"message"` (not `"prompt"` as the plan originally specified). Rationale: aligns with `_Supervisor.steer()`'s own wire param `{"sessionId": ..., "message": text}` and the live-probe spec in project memory. Phase 2 plan spec updated accordingly.
- Phase 2: `acpQueue`/`acpSteer` wrapped in `acpQueueSteer` `<div>` for atomic hide/show; `refreshComposerControls()` operates on the wrapper.

## Review Log

### 2026-08-12 — Initial Plan Review (via /qplan)

High-effort, 4 personas: Architect, Senior Engineer, Reliability Engineer, End-User Advocate. 22 findings (8 High, 10 Medium, 4 Low). 17 auto-resolved; 3 escalated as decisions (D1–D3), all accepted by user.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `_handle_steer` missing `closing` guard — steer during teardown hangs until timeout. | Fixed — `closing` guard added to `_handle_steer` pre-flight. |
| 2 | High | `_handle_steer` missing `unknown_session` guard — silent drop with no client feedback. | Fixed — `unknown_session` guard added to `_handle_steer` pre-flight. |
| 3 | High | No `inflight` guard in `_handle_steer` — steer with no active turn blocks 90s on kiro-cli rejection. | Fixed — `inflight` check added; returns `no_turn_in_progress` error immediately. |
| 4 | High | Steer clears textarea optimistically before ack — text permanently lost on server error. | Fixed (D1 accepted) — textarea held disabled until `steer_ack`; restored on error frame. |
| 5 | High | SC-1 required "deleting marker removes attachment" — bidirectional sync is fragile and unimplementable cleanly. | Fixed (D2 accepted) — SC-1 weakened: tray × is the only removal affordance; marker deletion does not remove attachment. |
| 6 | High | `queuedPrompt` not cleared in `releaseSession()` — fires against next opened session. | Fixed — `queuedPrompt` and `queuedPromptSession` cleared in `releaseSession()`. |
| 7 | High | Unread marking used `frame.sessionId !== sessionId` — always false in single-socket architecture; unread feature would never fire. | Fixed — unread detection moved to `railRefreshStates()` using `_prevSessionStatus` transition tracking. |
| 8 | High | Phase 4 CSS Changes said "Remove `status-working`" contradicting Risk table's "keep it" — dashboard dots would break. | Fixed — `status-working` kept; only new classes added. Contradiction resolved. |
| 9 | Medium | `setTurn()` sets button states directly, bypassing `refreshComposerControls()` — "Stopping…" label not reset on turn end. | Fixed — Phase 2 now explicitly requires `setTurn()` to call `refreshComposerControls()`. |
| 10 | Medium | Manual reconnect button press while backoff timer pending causes double `connect()`. | Fixed — `_reconnectTimer` cancelled at top of `connect()` unconditionally. |
| 11 | Medium | Queue auto-send clobbers text user typed after queuing. | Fixed — auto-send guarded: skips if `promptInput.value.trim()` non-empty, shows note. |
| 12 | Medium | `removeAttachment` renumbering used non-global `String.replace()`, misses duplicate markers. | Fixed — use `new RegExp(..., 'g')` global replace. |
| 13 | Medium | Queue auto-send had no `ws.readyState` guard — text consumed but silently not sent on closed socket. | Fixed — `ws.readyState === WebSocket.OPEN` guard added; restores text on failure. |
| 14 | Medium | Cancel cascade `_emit_subagents_frame` had no exception guard — partial crew mutation with no broadcast. | Fixed — wrapped in try/except with logging; mutation commits regardless. |
| 15 | Medium | Queue session-identity guard present in Risk section but absent from Phase 2 code. | Fixed — explicit `queueSession !== sessionId` check added; shows note on session change. |
| 16 | Medium | `stoppedAt` in cancel cascade overwrote more-precise kiro-cli timestamp when already set. | Fixed — `if not entry.get("stoppedAt")` guard added, consistent with `_on_subagent_list`. |
| 17 | Medium | Queue had no cancellation mechanism. | Fixed (D3 accepted) — inline cancel link added to queue note; restores text to textarea. |
| 18 | Medium | Queue/Steer buttons need `aria-label`. | Fixed — `aria-label` attributes specified in Phase 2 HTML. |
| 19 | Medium | Mobile layout: two stacked buttons must not make composer row taller. | Fixed — Phase 2 CSS constrains total Queue+Steer height to current Stop button height. |
| 20 | Medium | `_crewTimerInterval` leaked if code path releases session without `setCrew([])`. | Fixed — explicit `clearInterval` added unconditionally in `releaseSession()`. |
| 21 | Low | `steer_ack` no-op left false "Steer sent." note when `queued===false`. | Fixed — `steer_ack` handler checks `queued===false` and shows error note. |
| 22 | Low | Various: scroll math for nav arrows, tooltip for Steer button, timer stoppedAt null guard in `elapsedText()`. | Fixed inline — `elapsedText` null-guards `stoppedAt`; `aria-label`/`title` on Steer button; scroll math note added. |

### 2026-08-12 — Implementation Review (after Phase 1, persona: Security auditor, Reliability engineer, Senior engineer, Maintainability reviewer)

Implementation health: Green.
16 findings (1 High, 7 Medium, 8 Low). All resolved across 2 cycles + 1 Low-fix pass.

Cycle 2 skipped for the Low-fix pass — all remaining findings were Low and auto-fixes were purely mechanical (test assertion strengthening, comment update, test additions).

Escalation 1 (MEDIUM — duplicate cascade logic): User directed: Fix now. Resolved by extracting `_mark_crew_done` helper.
Escalation 2 (LOW — payload key name): User directed: Accept and note. Noted as divergence in § Implementation Divergences.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `AcpError` path in `_handle_cancel` has no `return` — cascade runs on rejected cancel, desynchronising crew state. | Fixed — `return` added after error frame in `except AcpError` branch (cycle 1). |
| 2 | Medium | Non-string steer `prompt` payload raises unhandled `AttributeError` as task exception; client hangs. | Fixed — `isinstance` guard added before text extraction (cycle 1). |
| 3 | Medium | Missing tests for `unknown_session`, `not_subscribed`, `close_in_progress`, `bad_payload` guards. | Fixed — 5 new guard tests added to `TestAcpSteer` (cycle 1). |
| 4 | Medium | `steer_ack` unicast not verified — broadcast mutation undetectable. | Fixed — second subscriber assertion added to routing test (cycle 1). |
| 5 | Medium | `_handle_cancel` missing `unknown_session` guard, inconsistent with `_handle_steer` and `_handle_prompt`. | Fixed — guard added before `conn.session_id` check (cycle 1). |
| 6 | Medium | Cancel cascade logic duplicated in `_handle_cancel` and `_on_subagent_list`. | Fixed — `_mark_crew_done` helper extracted; user directed Fix now (cycle 2). |
| 7 | Medium | Missing `log.warning` on steer guard refusals — production steer failures invisible in logs. | Fixed — log lines added after each guard's error frame (cycle 1). |
| 8 | Low | Steer payload key `"prompt"` conflicts with internal `"message"` name and wire spec. | User: accepted — noted as divergence; plan Phase 2 spec updated to use `"message"`. |
| 9 | Low | `test_cancel_preserves_already_set_stoppedAt` tested wrong scenario (done=True entry skipped by cascade). | Fixed — test corrected to seed done=False with pre-set stoppedAt (cycle 1). |
| 10 | Low | `_Supervisor.steer` `or {}` default returns `queued=False` for null result, misleading client. | Fixed — default changed to `True` (cycle 1). |
| 11 | Low | `time.time()` for stoppedAt uses wall clock — negative elapsed possible on NTP rollback. | User: accepted — Phase 5 JS `Math.max(0,...)` guard is the mitigation. |
| 12 | Low | `test_steer_refused_for_non_string_payload` asserts type==error but not specific code==bad_payload. | Fixed — assertion strengthened to check `"bad_payload"` code (Low-fix pass). |
| 13 | Low | Plan Phase 2 spec still referenced `{prompt: text}` after server-side rename to `"message"`. | Fixed — Phase 2 spec updated in plan file (Low-fix pass). |
| 14 | Low | No test asserts cancel cascade is skipped when `_supervisor.cancel` raises `AcpError`. | Fixed — `test_cancel_cascade_skipped_on_agent_error` added (Low-fix pass). |
| 15 | Low | Empty-dict crew path (`crews[sid] = {}`) not tested for cascade skip. | Fixed — `test_cancel_skips_cascade_when_crew_is_empty_dict` added (Low-fix pass). |
| 16 | Low | Stale `` `prompt` `` cross-handler comment in `_handle_cancel` after payload rename. | Fixed — changed to `` `session/prompt` `` (Low-fix pass). |

### 2026-08-12 — Implementation Review (after Phase 2, persona: End-user advocate, Reliability engineer, Senior engineer, Maintainability reviewer)

Implementation health: Green.
16 findings (1 High, 7 Medium, 8 Low). All resolved across 2 cycles (cycle 3 skipped — cycle-2 findings all Low + mechanical).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | WS drop during steer leaves textarea permanently disabled — ws.onclose didn't restore `_steerPending`. | Fixed — cleanup block added to `ws.onclose` restoring text and re-enabling controls (cycle 1). |
| 2 | Medium | `removeAttachment` duplicate marker — deleted marker not removed before renumbering, producing `[Image 1][Image 1]`. | Fixed — deleted marker removed first, then higher markers renumbered (cycle 1). |
| 3 | Medium | `steer_ack {queued:false}` nulled `_steerPending` before restoring text — text permanently lost. | Fixed — false-branch check moved before null (cycle 1). |
| 4 | Medium | `refreshComposerControls()` reset `stopBtn.disabled` mid-flight, allowing double cancel click. | Fixed — `_stopInProgress` flag added; `refreshComposerControls()` preserves disabled state (cycle 1). |
| 5 | Medium | Dead `stopBtn.disabled = false` in `setTurn()` overriding `refreshComposerControls()` ownership. | Fixed — removed (cycle 1). |
| 6 | Medium | Wrong comment in steer handler claimed textarea cleared only after ack. | Fixed — comment corrected (cycle 1). |
| 7 | Medium | No test for steer cleanup on WS close or session release. | Fixed — two new tests added (cycle 1). |
| 8 | Medium | `agent_died` handler didn't clear `_steerPending` or re-enable textarea. | Fixed — cleanup block added; test added (cycle 2). |
| 9 | Low | Queue button missing `!sessionId` guard (defense-in-depth). | Fixed — guard added (cycle 1). |
| 10 | Low | `acp-inline-cancel` missing `aria-label`. | Fixed — attribute added (cycle 1). |
| 11 | Low | `acp-inline-cancel` missing `:focus-visible` CSS. | Fixed — rule added (cycle 1). |
| 12 | Low | Queue+Steer pair 1px shorter than Stop due to `-0.5px` each. | Fixed — height changed to `calc(.../2)` exactly (cycle 1). |
| 13 | Low | Stop visible/enabled during steer flight. | Fixed — `stopBtn.hidden` includes `!!_steerPending` check (cycle 1). |
| 14 | Low | Dead `cancelLink.remove()` after `.textContent =` assignment. | Fixed — removed with comment (cycle 1). |
| 15 | Low | Session-change guard test didn't exercise the actual guard (went through `releaseSession` instead). | Fixed — new direct guard test added (cycle 1). |
| 16 | Low | `_steerPending` naming inconsistent with `queuedPrompt`. | User: accepted — documented in divergences. No rename needed.

## Harness Improvement Opportunities

- The ACP probe pattern (spawn kiro-cli as a subprocess, drive JSON-RPC directly) proved useful for verifying behavioral assumptions before writing code. Consider documenting it in `plans/tests/HARNESS.md` as a reusable technique for ACP protocol questions — cost: ~15 min per verification saved vs. runtime testing.
