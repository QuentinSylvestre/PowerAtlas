# ACP UI Feature Batch

> **Date**: 2026-08-12
> **Status**: Exploring
> **Scope**: Eight /acp page improvements: image inline, steer/queue, auto-reconnect, new dot colors, rail refresh triggers, cancel-cascades-to-subagents, subagent timer freeze, prompt navigation arrows

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

1. **SC-1 Image inline**: pasting an image into the textarea inserts `[Image N]` at the cursor position. The tray chip still appears. Deleting the marker from the textarea removes the attachment.
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

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### Existing patterns & constraints

- `acp.py` isolation boundary: imports exactly two names from the package (`config.CONFIG_DIR`, `launcher._SESSION_ID_RE`). Any new server-side wiring must not add new imports from sibling modules. (`acp.py` header, lines 1–35)
- `_session/steer` is a kiro-cli extension method — fire-and-forget notification (no id), wraps message in `<user_message>\n...\n</user_message>` envelope. Confirmed in KiroCrew `acp/client.py:4288–4307`. Not available on Claude Code ACP; `supports_steer` check required.
- `session/cancel` as a notification on the parent's channel does NOT affect child session ids — verified by live probe (child cancel silently ignored; parent cancel works with `stopReason: cancelled` in ~0ms).
- After parent cancel, kiro-cli never emits a terminal status for child sessions — verified by live probe (11 post-cancel `list_update` frames, all still `working` across 8 seconds). PowerAtlas must set `done` locally.
- `_subagents_payload` (`acp.py:3040`) builds the wire list from crew dict. New `stoppedAt` field must be added here.
- `elapsedText()` (`acp.html:999`) is dead code — defined, never called. One match only in the file (confirmed by grep).
- `renderAgentBar()` (`acp.html:2315`) calls `setCrew()` synchronously on every `subagents` frame. There is no periodic timer driving crew card updates — adding one is required for SC-7.
- Rail dot only drawn for `state === 'held'` rows (`acp.html:~3427`). Current CSS: working=`#22c55e` (green + pulse), waiting=`#f59e0b` (amber), errored=`#ef4444` (red). All three need updating for the new scheme (`style.css:191–207`).
- `RAIL_STATUS` map on both server (`acp.py`) and client (`acp.html`) must be extended with new states. The JS map uses `Object.create(null)` for closed-set safety — new values follow the same pattern.
- `stuckToBottom()` threshold is 60px (`acp.html:996`). Prompt navigation must not interfere with this mechanism.
- `ws.onclose` → `reconnectBtn.hidden = false` is the only reconnect trigger (`acp.html:~4897`). No backoff loop exists. `reconnectBtn.addEventListener('click', connect)` calls connect directly.
- `railRefreshSoon()` call sites: `session` frame with `!created`, `agent_died`, inside `releaseSession()`. Not called after send or cancel.
- The `no-innerHTML` rule (`acp.html` script comment) requires all new DOM construction to use `createElement` + `textContent`. No markup strings.
- `tests/acp_page.test.mjs` covers inline JS behavior; it is not part of the pytest suite and must be run separately when changing templates.

### Risks & mitigations

- **R1 `_session/steer` availability**: not tested against this exact kiro-cli build. Mitigation: hide the Steer button when `supports_steer` is false (check via a `meta connected` frame property or a capability flag). Graceful degradation — only Queue is shown.
- **R2 steer mid-turn race**: user types text, turn ends, user presses Steer before `setTurn(false)` fires. Mitigation: Steer button is only shown while `turnActive === true`; by the time `meta turn:end` arrives and clears it, the button is gone.
- **R3 Queue double-send**: user queues text, turn ends, auto-send fires — but the user had already manually sent it. Mitigation: clear `queuedPrompt` on `meta turn:end` before sending; if `promptInput.value` was manually cleared, `queuedPrompt` was already cleared by the clear action.
- **R4 unread state stale across sessions**: a session closed and reopened might show green (unread) from a prior PowerAtlas run. Mitigation: acceptable — unread is a soft UX signal, not authoritative state.
- **R5 crew timer interval memory leak**: the per-second crew timer must be cleared when the session closes or the crew goes empty. Mitigation: store the interval id and clear it in `setCrew([])` and `releaseSession()`.
- **R6 `_session/steer` is a notification on the kiro-cli ACP channel**: must be sent as a notification (no id), not a request. Using `_notify` in `acp.py` (same as `session/cancel`). Server must expose a new `steer` client frame type and a `_handle_steer` handler.

### Resolved decisions

- Change-1: What should appear in the textarea on image paste? — A: text marker `[Image N]` inserted at cursor. — Decision: insert `[Image N]` at cursor in textarea; tray chip stays; deleting marker removes attachment.
- Change-2: Queue-only or Queue+Steer? — A: both. Stacked Queue (top) / Steer (bottom) replace Stop when textarea non-empty. Empty textarea shows Stop. — Decision: two vertically-stacked half-height buttons.
- Change-2b: What is "steer"? — A: `_session/steer` ACP notification, mid-turn inject, same protocol KiroCrew uses. Confirmed in KiroCrew source. — Decision: server exposes new `steer` frame type calling `_notify("_session/steer", ...)`.
- Change-3: Backoff shape? — A: exponential (1s→2s→4s→8s→…→30s cap). — Decision: confirmed.
- Change-4: Client-side or server-side unread? — A: client-side localStorage. — Decision: localStorage, held-only sessions, cleared on subscribe.
- Change-5: Trigger sites? — A: after send and after cancel. — Decision: `railRefreshSoon()` in both places.
- Change-6: Can kiro-cli cancel child sessions via parent channel? — A: no (verified by probe). — Decision: server marks all active crew entries `done=true` locally on cancel, emits `subagents` frame.
- Change-7: Show timer while running, freeze at done? — A: yes, live timer while working, frozen elapsed when done. — Decision: `stoppedAt` added server-side; JS wires `elapsedText()` with per-second interval, capped for done entries.
- Change-8: Sticky banner or navigation arrows? — A: floating ↑/↓ arrows. — Decision: bottom-left of transcript, stacked vertically, Up=prev user msg, Down=next or scroll-to-bottom, hidden when <2 user messages.

### Open items

- **O1**: Confirm `_session/steer` is available on the installed kiro-cli 2.16.x build (suggested: probe at `acp.py` startup or on first `steer` call; surface as a `supports_steer` flag in the `meta connected` frame). Execution-contingent.
- **O2**: Confirm the exact `_session/steer` params shape against the installed build (KiroCrew wraps in `<user_message>\n...\n</user_message>`; verify this is still required on 2.16.x or if raw text is accepted). Execution-contingent.

### Recommended approach

Implement as 8 largely-independent changes in a single plan, grouped by surface:

**Server-side (`acp.py`):**
1. Add `steer` to `CLIENT_TYPES` and `_handle_steer` — validates session, checks turn-in-flight, calls `_notify("_session/steer", ...)`. Add `supports_steer` to the `meta connected` payload.
2. On cancel (`_handle_cancel`): after `await _supervisor.cancel(session_id)`, iterate `crews[session_id]`, set `done=True` and `stoppedAt=time.time()` on all active entries, call `_emit_subagents_frame(session_id)`.
3. In `_on_subagent_list`: set `stoppedAt: time.time()` when a crew entry first transitions to `done=True`.
4. In `_subagents_payload`: include `stoppedAt` field (None if not set).

**Client-side (`acp.html`):**
5. Image inline (SC-1): in the image staging pipeline (after `renderTray()`), insert `[Image N]` at `promptInput.selectionStart`.
6. Queue/Steer buttons (SC-2): new `queueBtn` / `steerBtn` elements in the composer row (HTML). In `setTurn()` and `promptInput` input handler: show Queue+Steer when `turnActive && promptInput.value.trim()`, show Stop when `turnActive && !promptInput.value.trim()`, show Send when `!turnActive`. Queue handler: save `promptInput.value` to `queuedPrompt`, clear textarea. On `meta turn:end`: if `queuedPrompt`, auto-send it. Steer handler: send `{type: 'steer', sessionId, payload: {prompt: text}}` via WebSocket.
7. Auto-reconnect (SC-3): in `ws.onclose`, when `opened===true`, start a backoff timer (starting at 1s, doubling, capping at 30s) that calls `connect()`. Clear the timer on a successful `onopen`. Reconnect button still works as before.
8. Dot colors (SC-4): add `status-idle`, `status-unread`, `status-thinking` CSS classes to `style.css`. Update `RAIL_STATUS` map with new keys. In `railRowNode()`, merge localStorage unread state with server status when computing dot class. On `meta turn:end` for non-active session: set `localStorage['pa_unread_' + sid] = '1'`. On `subscribe`: clear it.
9. Rail refresh triggers (SC-5): `railRefreshSoon()` after `send('prompt', ...)` succeeds and after `send('cancel', ...)` succeeds.
10. `steer` frame handler (SC-2 client-receive): handle `type === 'steer'` in `handle()` to surface steer confirmations if needed (may be a no-op; steer response comes as normal `chunk` frames within the same turn).
11. Subagent timer (SC-7): wire `elapsedText()` into `agentPill()` sublabel for working entries. Add a 1s `setInterval` that calls `renderAgentBar()` when crew is non-empty and any entry is not done. Clear the interval when crew is empty or all done. Use `Math.min(Date.now()/1000, entry.stoppedAt || Infinity)` as the time cap.
12. Prompt navigation (SC-8): add two `<button>` elements (`acpPromptUp`, `acpPromptDown`) absolutely positioned at bottom-left of `.acp-transcript`. Track `userMsgEls` array updated in `addMessage('user', ...)`. Up: scroll to `userMsgEls[currentIndex - 1]`. Down: scroll to `userMsgEls[currentIndex + 1]` or to bottom. `currentIndex` derived from scroll position. Show/hide based on `userMsgEls.length >= 2`.

**CSS (`style.css`):**
- New dot color classes: `status-idle` (white/`#e5e7eb`), `status-unread` (green `#22c55e` + pulse), `status-thinking` (blue `#3b82f6` + pulse), keep `status-waiting` → amber, `status-errored` → red.
- Composer row: CSS for the stacked Queue/Steer buttons at half the current Stop button height.
- Transcript overlay: positioning for the `↑`/`↓` nav buttons at bottom-left.

### QA environment

- Start PowerAtlas: `.venv-PowerAtlas\Scripts\power-atlas`
- Open `/acp` at `http://127.0.0.1:<port>/acp`
- JS tests: `node tests/acp_page.test.mjs`
- Python tests: `.venv-PowerAtlas\Scripts\pytest tests/test_web.py tests/test_data.py -x`
- To test fan-out: use a prompt like "Use the subagent tool to dispatch 2 parallel stages doing trivial shell commands"
- To test steer: requires a running turn; send a long-running prompt then type in the textarea and press Steer
- Remote surface not needed for these changes

## Harness Improvement Opportunities

- The ACP probe pattern (spawn kiro-cli as a subprocess, drive JSON-RPC directly) proved useful for verifying behavioral assumptions before writing code. Consider documenting it in `plans/tests/HARNESS.md` as a reusable technique for ACP protocol questions — cost: ~15 min per verification saved vs. runtime testing.
