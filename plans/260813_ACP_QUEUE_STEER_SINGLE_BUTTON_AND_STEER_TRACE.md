# ACP Queue/Steer Single-Button Mode Picker and Steer Trace Visibility

> **Date**: 2026-08-13
> **Status**: Exploring
> **Scope**: Two /acp composer UX improvements — replace the two-button Queue/Steer group with a single-button+mode-select, and make injected steer text visible in the transcript during the session and across WS reconnects.

---

## Intent

### Problem statement & desired outcomes

The Queue/Steer two-button group is cramped and its interaction model is awkward: two half-height buttons that together replace Stop, with no keyboard path during a turn. When a steer is sent, the only feedback is an ephemeral "Steer sent." note in amber italic — it carries no text, disappears on reload, and gives no indication of what was injected. Both issues reduce confidence and flow when using the agent surface.

Desired outcomes:
- A single full-height action button in the composer row during a turn, with an inline mode selector (Queue / Steer) that remembers the last choice.
- Enter key during a turn triggers whichever mode is currently selected, consistent with Enter triggering Send outside a turn.
- Injected steer text is visible in the transcript as a dimmed variant of the user prompt band — visually distinguishable from a real turn prompt but sharing the same layout — and survives WebSocket reconnects within the same PowerAtlas process lifetime.

### Success criteria

- SC-1: During a turn with textarea text, a single button (full Stop-height) replaces the two stacked buttons. A native `<select>` adjacent to it lets the user choose Queue or Steer; the label on the button matches the selected mode.
- SC-2: The chosen mode persists across page reloads via `localStorage` key `pa_acp_send_mode` (`"steer"` default, `"queue"` alternative). Fresh installs and missing keys default to Steer.
- SC-3: Enter key during a turn (no Shift, no Ctrl, not a touch device, textarea non-empty) triggers the selected mode, exactly as Enter outside a turn triggers Send.
- SC-4: When a steer is injected (steer_ack success), the steer text appears in the transcript as a dimmed user-band message: same blue-accent band layout as `.acp-msg-user`, but with reduced opacity (`rgba(108,140,255,0.06)` background, `rgba(108,140,255,0.4)` left border, dimmed body text).
- SC-5: The steer trace message survives WebSocket reconnect within the same PowerAtlas process (stored in the ring buffer via `_emit` in `_handle_steer`). It does not survive a PowerAtlas restart (ring buffer is in-memory — accepted).
- SC-6: All 14 existing queue/steer tests pass (updated for new DOM structure). New tests cover mode selection, Enter-during-turn, and `steer_sent` frame rendering.

### Scope boundaries & non-goals

In scope:
- `src/power_atlas/templates/acp.html` — HTML structure, inline CSS, inline JS
- `src/power_atlas/static/style.css` — new `.acp-msg-steer` rule
- `src/power_atlas/acp.py` — `SERVER_TYPES`, `_handle_steer`, client `handle()` handler
- `tests/acp_page.test.mjs` — update existing + add new tests

Out of scope:
- Persisting steer traces across PowerAtlas restarts (would require writing to the kiro-cli `.jsonl` transcript or a separate store — not desired)
- Handling the `AgentExecutionSteeringInjected` echo from kiro-cli (it arrives at `acp.py` but is dropped; no value in forwarding it since the text is already known server-side at injection time)
- Changing how Queue works — it retains its current behavior (inline cancellable note, auto-send on turn end)
- Mobile/touch Enter behavior — unchanged (touch devices use Shift+Enter / explicit button tap)

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### Existing patterns & constraints

- `acp.html:262–267`: current HTML — `div#acpQueueSteer` (flex-column wrapper, `hidden`) containing `#acpQueue` and `#acpSteer` as `acp-btn acp-queue` / `acp-btn acp-steer`.
- `style.css:664–666`: `.acp-queue-steer { display:flex; flex-direction:column; gap:0; }` and `.acp-queue,.acp-steer { height:calc(var(--send-btn-height,2.5rem)/2); ... }`. The `--send-btn-height` CSS variable is **never defined** — only consumed with fallback `2.5rem`. Actual button height is determined by `align-items:stretch` on `.acp-composer-row`.
- `acp.html:1054–1065`: `refreshComposerControls()` — only references `queueSteerEl` (the wrapper), never `queueBtn` or `steerBtn` individually. New single-button + select must replace the wrapper at the same visibility logic: `queueSteerEl.hidden = !turnActive || !hasText`.
- `steerBtn` has **8 reference points** in acp.html (lines 1139, 5310, 5319, 5397, 5446, 5626, 5735, 5743) — all `steerBtn.disabled` mutations in lifecycle handlers (steer_ack, error, agent_died, releaseSession, ws.onclose) plus the click handler. The new single button replaces `steerBtn`; the same disable/enable logic applies to it.
- `queueBtn` has **3 reference points** (HTML markup, var declaration, click handler) — simpler replacement.
- `acp.html:5990–6044`: `promptInput` keydown handler — Enter calls `sendPrompt()` unconditionally; `sendPrompt()` itself early-returns when `turnActive`. The new branch must fire **before** `sendPrompt()`, after the command-dropdown check.
- `acp.py:806–811`: `envelope()` raises `ValueError` if `type_` not in `SERVER_TYPES`. Adding `"steer_sent"` to `SERVER_TYPES` (line 163) is required before calling `_emit` with that type.
- `acp.py:3240–3249`: `_emit(session_id, frame)` — records in ring buffer first, then broadcasts. Ring buffer max 2000 frames / 2 MB (`acp.py:299–300`). In-memory only; does not survive process restart.
- `acp.py:4295–4310`: `_handle_steer` success path — currently calls `conn.send(envelope("steer_ack", ...))` only. New `_emit` call goes here after `result.get("queued", True)` is confirmed true.
- `style.css:738–743`: `.acp-msg-user` — `background: var(--accent-dim)` = `rgba(108,140,255,0.12)`, `border-left: 2px solid var(--accent)` = `#6c8cff`, `margin: 0 -14px`, `padding: 6px 14px 6px 12px`. New `.acp-msg-steer` mirrors this at reduced opacity.
- `acp.html:3355–3362`: `railStored(key)` / `railStore(key, value)` — try/catch localStorage wrappers. Used for `pa_acp_debug_log` and `pa_acp_group`. New `pa_acp_send_mode` follows same pattern.
- `tests/acp_page.test.mjs`: 14 existing queue/steer tests (lines ~6943–7175). All reference `acpQueueSteer`, `acpQueue`, `acpSteer` by id. Must update DOM fixture and assertions for new single-button structure.

### Risks & mitigations

- **R1 — steer_sent emitted for model-refused steers**: `_emit` fires when `steer_ack` returns `queued: true` (kiro-cli accepted the injection). The model may still choose to ignore the steer — the trace reflects injection acceptance, not model compliance. This is the same ambiguity as the current "Steer sent." note. Mitigation: document in code comments; no behavioral change needed.
- **R2 — `<select>` aria conflict with textarea**: The textarea already owns `aria-haspopup="listbox"` and `aria-controls="acpCmdDropdown"` for the slash-command dropdown. A native `<select>` is an independent focusable element — no aria conflict. The select should have its own `aria-label="Send mode"` or similar.
- **R3 — `replaying` guard for steer_sent**: The steer trace must render normally during history replay (same as `chunk` frames). The client handler for `steer_sent` must NOT be gated by `!replaying`. Confirmed: `addMessage` itself has no `replaying` guard; the guard is only in specific places (e.g. `restorePendingPrompt`).
- **R4 — rejection path emits**: `_emit` must only fire on success (`queued === true`). The rejection path (`queued === false`) must not emit a `steer_sent` frame — it would pollute the replay with a steer that didn't land.

### Resolved decisions

- Q1: Dropdown shape for mode picker — A: native `<select>` — Decision: use `<select>` element adjacent to the single action button; the button label tracks the selected mode.
- Q2: Mode persistence — A: yes, persist — Decision: `localStorage` key `pa_acp_send_mode`, values `"steer"` / `"queue"`, default `"steer"`.
- Q3: Enter during turn triggers which mode — A: whichever is selected — Decision: keydown handler checks `turnActive && hasText` and dispatches to queue or steer logic based on current mode selection.
- Q4: Ring buffer scope / PowerAtlas restart — A: understood, restart wipes it — Decision: server-assisted approach (`_emit("steer_sent", ...)`) for within-session replay; no cross-restart persistence needed.
- Q5: Steer trace visual style — A: same band layout as user prompt, slightly dimmer — Decision: new `.acp-msg-steer` CSS class: `rgba(108,140,255,0.06)` background, `rgba(108,140,255,0.4)` left border, same margin/padding as `.acp-msg-user`, dimmed body text (`var(--text-dim)`). Role label blank (same as note).

### Open items

- O1 (execution-contingent): Exact DOM structure of the replacement — the single button and `<select>` could be wrapped in the existing `#acpQueueSteer` div (reusing its show/hide logic) or replaced entirely. Preference is to reuse the wrapper with new children; confirm during implementation that `refreshComposerControls()` still only needs to toggle the wrapper's `hidden` attr.

### Recommended approach

Two phases, independent file scope:

**Phase 1 — Single button + mode select (`acp.html`, `style.css`)**:
1. Replace `#acpQueue` and `#acpSteer` inside `#acpQueueSteer` with a single `<button id="acpSendMode">` (full stretch height, label = selected mode) and a `<select id="acpModeSelect">` with options Queue/Steer.
2. Add `var _sendMode` initialized from `railStored('pa_acp_send_mode') || 'steer'`. Wire the `<select>` change event to update `_sendMode` and persist via `railStore`.
3. Update `refreshComposerControls()` — wrapper show/hide logic unchanged; button label update on mode change.
4. Replace `queueBtn`/`steerBtn` references: the single button's click handler dispatches to the queue or steer code path based on `_sendMode`. The 8 `steerBtn.disabled` mutation sites become `sendModeBtn.disabled`.
5. Add Enter-during-turn branch in keydown handler: after the command-dropdown check, before `sendPrompt()`, add `if (turnActive && hasText && !ev.shiftKey && !isTouchDevice) { ev.preventDefault(); sendModeBtn.click(); return; }`.
6. CSS: remove `.acp-queue, .acp-steer` half-height rules; the wrapper already stretches via `align-items:stretch`.

**Phase 2 — Steer trace (`acp.py`, `acp.html`, `style.css`)**:
1. `acp.py`: add `"steer_sent"` to `SERVER_TYPES`. In `_handle_steer` success path, after `conn.send(steer_ack)`, call `_emit(session_id, envelope("steer_sent", {"text": text}, session_id))`.
2. `acp.html`: add `steer_sent` handler in `handle()`: `addMessage('steer', payload.text)`. Remove the `addMessage('note', 'Steer sent.')` call from the `steer_ack` success path.
3. `style.css`: add `.acp-msg-steer` rule mirroring `.acp-msg-user` at reduced opacity.
4. `tests/acp_page.test.mjs`: update 14 existing tests for new DOM structure; add tests for mode selection, Enter-during-turn, `steer_sent` frame rendering.

### QA environment

- `node tests/acp_page.test.mjs` — JS test suite covering the template's inline script. Runs against a DOM stand-in (no browser needed). Run after every change to `acp.html`.
- `.venv-PowerAtlas\Scripts\python -m pytest tests/ -x -q` — Python test suite.
- Browser hard-reload (`Ctrl+Shift+R`) against the running PowerAtlas instance for visual QA of CSS and interaction — no restart needed for `acp.html` / `style.css` changes (served with `Cache-Control: no-store`, Jinja `auto_reload=True`).
- PowerAtlas restart required only if `acp.py` changes (Phase 2).

---

**Assumptions (unconfirmed)**

- A2: `steer_sent` client handler renders normally during `replaying` — assumed yes; `addMessage` has no `replaying` guard and `steer_sent` frames in the ring buffer should replay like `chunk` frames.
- A5: The `<select>` sits visually adjacent to the single action button inside `#acpQueueSteer`; exact layout (button left / select right, or button with select inline) TBD at implementation based on what fits the composer row without vertical misalignment.

## Harness Improvement Opportunities

- Skill check-in (depth recommendation) should be done at the top of Step 2, not implied — cost: user had to prompt me to re-read the skill. Suggested change: add an explicit "state depth level before first question" step to the Kiro overlay.
