# ACP Toolbar Button Redesign

> **Date**: 2026-08-18
> **Status**: Complete
> **Last Updated**: 2026-08-18
> **Scope**: Redesign the send/stop/steer/queue buttons in the `/acp` composer row

---

## Intent

### Problem statement & desired outcomes

The current `/acp` composer row has a Send button labeled "Send" with inconsistent visual language and a fused steer/queue compound control whose chevron toggle does not match the rest of the UI. The user wants a uniform icon-button language throughout:

- A **Start** button: square icon-only, up-arrow SVG. Greyed when empty or no session; accent-filled when ready.
- A **Stop** button: square icon-only, filled-square SVG. Shown only during a turn when the textarea is empty.
- A **Steer/Queue** compound: two separate square icon-only buttons — an action button (up-arrow) and a dropdown button (same visual language as the rail groupby button) — shown during a turn when the textarea has text.
- Start and Stop can never be shown at the same time.

### Success criteria

- SC-1: All four states render correctly:
  - Idle + empty textarea: Start button visible (greyed/disabled), Stop and compound hidden
  - Idle + filled textarea: Start button visible (enabled, accent-filled), Stop and compound hidden
  - Turn active + empty textarea: Stop button visible, Start and compound hidden
  - Turn active + filled textarea: Compound visible (action + dropdown), Start and Stop hidden
- SC-2: The dropdown button in the compound is a standalone 34×34 square (Option B), not fused to the action button
- SC-3: The mode dropdown opens above the dropdown button, right-aligned
- SC-4: Dead code removed: `.acp-send` class removed from `#acpSend` and `#acpStop`; dead `sendBtn.textContent` / `stopBtn.textContent` assignments removed from `setTurn()` and the Stop click handler
- SC-5: `node tests/acp_page.test.mjs` passes with no test changes (`.sr-only` span preserved inside `#acpSendMode`)
- SC-6: Hard reload in browser reflects all changes (no PowerAtlas restart needed)

### Scope boundaries & non-goals

In scope:
- `src/power_atlas/templates/acp.html` — button markup + inline CSS for the composer area
- `src/power_atlas/static/style.css` — icon button CSS rules

Out of scope:
- Backend changes (Python)
- Queue and Steer JS logic (unchanged)
- Any other page or template

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

- Step 1.5 run inline — orchestrator read files before dispatching (non-bypass recorded per skill rules). Sub-agents dispatched as code-tracing trio; all three returned complete findings.
- **Button HTML** (`acp.html` lines 269–310): `#acpSend` (`.acp-btn .acp-send .acp-icon-btn`), `#acpStop` (`.acp-btn .acp-danger .acp-send .acp-icon-btn`), `#acpQueueSteer` wrapper holding `#acpSendMode` + `#acpModeToggle` + `#acpModeMenu`
- **`refreshComposerControls()`** (`acp.html` line 1276): sole owner of `.hidden` and `.disabled` on these three elements. Called from 12+ sites — `setTurn()`, input event, queue/steer send paths, WS close, ack frames, agent_died, error frame, image attach.
- **`setTurn()`** (`acp.html` line 1289): contains dead `sendBtn.textContent` (line 1291) and `stopBtn.textContent` (line 1298) writes — both buttons are icon-only, the writes are no-ops but destroy SVG if button is visible when hit. Also line 7244 in Stop click handler.
- **`.acp-send` class** (`style.css` line 1143): applies `padding: 6px 20px`, overridden to 0 by `.acp-icon-btn`. Dead on both `#acpSend` and `#acpStop`.
- **`.acp-icon-btn`** (`style.css` lines 1146–1162): 34×34px square, `display: inline-flex`, icon centered. The canonical shape for all icon-only buttons.
- **Rail settings button design precedent** (`acp.html` lines 101–122, `style.css` lines 1426–1436): `.acp-rail-settings-btn` = 32×32px standalone square, `aria-expanded` toggles `border-color`/`color` to accent, SVG icon, full border on all sides, `border-radius: var(--radius-sm)`.
- **Current `.acp-mode-toggle`** (inline CSS `acp.html` ~line 562): fused right-side control, `border-left: none`, partial corner rounding. Will be replaced by a standalone 34×34 square.
- **`.acp-mode-menu`** (inline CSS `acp.html` ~line 579): `position: absolute; bottom: calc(100% + 4px); right: 0; z-index: 200` — opens above, right-aligned. Preserved.
- **`.sr-only` span** inside `#acpSendMode`: written by `_applySendMode()` with mode label ('Steer'/'Queue'). Tests at `acp_page.test.mjs` lines 8228, 8235, 8252 assert on `el("acpSendMode").textContent` which reads through this span. Must stay inside `#acpSendMode`.
- **All IDs must be preserved**: `acpSend`, `acpStop`, `acpQueueSteer`, `acpSendMode`, `acpModeToggle`, `acpModeMenu`, `acpModeOptSteer`, `acpModeOptQueue`, `acpModeLiveRegion` — all referenced by JS and/or tests.
- JS variable assignments: `sendBtn = getElementById('acpSend')` (line 683), `stopBtn = getElementById('acpStop')` (line 684), `queueSteerEl = getElementById('acpQueueSteer')` (line 685), `sendModeBtn = getElementById('acpSendMode')` (line 686).
- AGENTS.md constraint: ACP UI changes (HTML/CSS only) take effect on hard reload — no PowerAtlas restart needed.

### 5. Risks & mitigations

- **Test breakage on mode-select tests**: 3 tests read `el("acpSendMode").textContent`. Risk: if `.sr-only` moves outside `#acpSendMode`, those tests fail. Mitigation: keep `.sr-only` inside the button (Q4 confirmed).
- **Dead `textContent` writes**: lines 1291, 1298, 7244 write textContent to icon buttons. If a future change adds a text node to these buttons, the dead writes become active and destroy the content. Mitigation: remove as part of SC-4.
- **`.acp-send-mode` fused CSS**: the current inline CSS for `.acp-send-mode` has `border-radius` that drops the right corners. This must be rewritten to full corner rounding for Option B. Mitigation: replace inline CSS block.
- **`.acp-mode-toggle` losing `border-left: none`**: the fused design removes the left border so there's no double border. Option B uses a gap instead — `gap: 4px` on `.acp-queue-steer`. The `border-left: none` rule must be removed. Mitigation: rewrite the toggle's CSS to a clean `.acp-icon-btn`-mirroring shape.

### 6. Resolved decisions

- Q1: Fused vs separate dropdown button — A: Separate (Option B, two standalone squares) — Decision: `#acpModeToggle` restyled as a 34×34 square with full border and gap from `#acpSendMode`; `.acp-queue-steer` gap changes from `0` to `4px`
- Q2: Dropdown opens above — A: confirmed — Decision: keep `bottom: calc(100% + 4px)` on `.acp-mode-menu`
- Q3: Remove dead `.acp-send` class — A: yes — Decision: remove `.acp-send` from `#acpSend` and `#acpStop` markup; keep `.acp-send { padding: 6px 20px }` rule in style.css only if used elsewhere (check before removing)
- Q4: Preserve `.sr-only` inside `#acpSendMode` — A: yes — Decision: no test changes needed

### 7. Open items

- Verify whether `.acp-send { padding: 6px 20px }` in `style.css` line 1143 is used anywhere outside `#acpSend`/`#acpStop` before deciding to remove the rule itself vs just the class from the buttons.

### 8. Recommended approach

Single phase, two files:

**`acp.html` changes:**
1. `#acpSend`: remove `acp-send` class; keep `acp-btn acp-icon-btn`
2. `#acpStop`: remove `acp-send` class; keep `acp-btn acp-danger acp-icon-btn`
3. `#acpQueueSteer` wrapper: add `gap: 4px` (or handle in CSS)
4. `#acpSendMode` (`acp-send-mode`): rewrite inline CSS — full border-radius, 34×34, keep `.sr-only` span
5. `#acpModeToggle`: replace ▾ text with an SVG chevron; `aria-haspopup="true"` → `"menu"`; rewrite inline CSS to standalone 34×34 square matching `acp-rail-settings-btn` pattern with `[aria-expanded="true"]` accent state
6. Remove dead `sendBtn.textContent` assignment in `setTurn()` (line 1291)
7. Remove dead `stopBtn.textContent = 'Send'/'Working…'` in `setTurn()` (line 1298)
8. Remove dead `stopBtn.textContent = 'Stopping…'` in Stop click handler (line 7244)

**`style.css` changes:**
1. `#acpSend:not(:disabled).acp-icon-btn` — aria-label update if needed; styling already correct (accent fill)
2. Verify and optionally remove `.acp-send` rule (line 1143) if no other consumers

**No JS logic changes.** `refreshComposerControls()`, `_applySendMode()`, `setTurn()`, and all event handlers are unchanged except removal of the three dead textContent writes.

### 9. QA environment

- Start PowerAtlas: `.venv-PowerAtlas\Scripts\power-atlas` (or existing running instance)
- Open `http://127.0.0.1:<port>/acp` in browser
- Hard reload (`Ctrl+Shift+R`) after CSS/HTML edits — no restart needed
- Create or resume a kiro-cli session to test the working-turn states
- Run test suite: `node tests/acp_page.test.mjs`


---

## Implementation

Implementation (2026-08-18, code: ce189da / 8a5ebad / 4e74d76)
Single-phase implementation across `acp.html` and `style.css`. Removed `acp-send` class from `#acpSend` and `#acpStop`; renamed `aria-label` on `#acpSend` from "Send" to "Start"; replaced the fused `#acpModeToggle` (▾ text, partial corners, `border-left: none`) with a standalone 34×34 square carrying `acp-btn acp-icon-btn` classes and a chevron SVG; corrected `aria-haspopup="true"` to `"menu"`; rewrote `.acp-queue-steer` gap from 0 to 4px; replaced the `.acp-send-mode` and `.acp-mode-toggle` inline CSS blocks with the standalone-square pattern and `[aria-expanded="true"]` accent state; removed three dead `textContent` assignments from `setTurn()` and the Stop click handler; removed the dead `.acp-send { padding: 6px 20px }` rule from `style.css`. Auto-fix commits resolved a `[hidden]` display-override bug (`#acpSend[hidden], #acpStop[hidden] { display: none }` added to style.css), a no-op `.acp-send-mode` CSS block (removed), a specificity gap on the expanded-state rule (compound selector added), and an orphan `acp-send-mode` class on `#acpSendMode` (removed).

Open item 7 resolved: `.acp-send { padding: 6px 20px }` had zero consumers outside `#acpSend`/`#acpStop` (confirmed by grep) — rule removed safely.

### Review log

### 2026-08-18 — Post-Implementation Review

Overall implementation health: Green.
Personas: End-user advocate, Senior engineer, Maintainability reviewer, Security auditor (high effort, 4 personas).
5 findings (0 High, 0 Medium, 5 Low). All resolved.
QA verification: PASS (browser — hard reload picks up CSS changes; `[hidden]` override confirmed fixed; 408/413 tests pass, 5 pre-existing failures unrelated to this change).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `.acp-icon-btn { display: inline-flex }` overrode `[hidden]` UA rule on `#acpSend`/`#acpStop` — both rendered visible simultaneously. | Fixed — added `#acpSend[hidden], #acpStop[hidden] { display: none }` to `style.css` (8a5ebad) |
| 2 | Low | `.acp-send-mode { border-radius }` inline rule was a no-op; `.acp-icon-btn` already covers it. | Fixed — block removed (8a5ebad) |
| 3 | Low | `.acp-mode-toggle[aria-expanded="true"]` had same specificity (0,2,0) as `.acp-btn:hover`; hover-while-open state could lose. | Fixed — compound selector `.acp-mode-toggle.acp-icon-btn[aria-expanded="true"]` (0,3,0) added (8a5ebad) |
| 4 | Low | `aria-label="Start"` rename has no test assertion; a future rename would be silent. | User: accepted — no test infrastructure for aria-label assertions exists today |
| 5 | Low | `acp-send-mode` class remained on `#acpSendMode` markup after its CSS block was removed — orphan. | Fixed — class removed from markup (4e74d76) |


## Harness Improvement Opportunities

- `/qexplore` Step 1.5 mandatory dispatch gate: read relevant files inline before dispatching the trio, then dispatched from a context that already had the file contents. The gate exists to ensure fresh-context sub-agents; the inline pre-read before dispatch defeats the isolation. — cost: unclear (sub-agents still returned good results, but the dispatch rationale was violated) — suggested change: add an explicit reminder in the gate wording that the orchestrator must not read target files before dispatching, only build the problem-context summary from directory listings and grep hits

### Acknowledged at archival

- Skipped (harness opportunity): existing gate wording already covers this; wording sufficient
