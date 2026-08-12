# ACP Orchestrating Status and Crew Card Redesign

> **Date**: 2026-08-12
> **Status**: Exploring
> **Scope**: /acp crew panel — add orchestrating header + redesign crew rows as lean dot-rows with sessionName

---

## Intent

### Problem statement & desired outcomes

When a kiro-cli fan-out is running, the `/acp` UI shows nothing until the first sub-agent card appears, and even then gives no aggregate indication that orchestration is happening. Once cards appear, they all display the unhelpful label `"kiro_default"` (the agent name) with no per-stage identity. The card design (bordered button with background fill) is heavier than the information it carries, and the working-state color (green left border) conflicts with the rail's own status-dot vocabulary where blue = working/thinking.

Desired outcomes:
- A clear "Orchestrating (N agents)" header appears in the crew panel as soon as any sub-agent is active, transitioning to "Done (N agents)" when all finish.
- Each row shows the per-stage short label (`sessionName`) as its primary identity, with the agent role name (`kiro_default`) secondary and the current action trailing — matching the visual grammar of kiro-cli's own agent monitor while being more polished for a web UI.
- Row style is lean and link-like (text highlight on hover, no card fill), using the same colored-dot vocabulary as the rail (blue-pulsing = working, grey = done, red = errored).

### Success criteria

- SC1: The crew panel has a header row showing "Orchestrating (N agents)" while any crew entry is active, and "Done (N agents)" when `crewAllDone === true`.
- SC2: Each crew row shows: `●` colored dot + `sessionName` bold + `role` muted + `action` trailing muted. No card border or background fill.
- SC3: Dot colors match the rail vocabulary: working → `status-thinking` (blue, pulsing), done → `status-idle` (grey), error → `status-errored` (red).
- SC4: `sessionName` is forwarded by `_subagents_payload` as a new `sessionName` field on the wire; the JS reads it and falls back to first 30 chars of `task` (trimmed) when absent or empty.
- SC5: Row hover style highlights text (color change) rather than pressing a button; rows remain clickable buttons with keyboard accessibility.
- SC6: `tests/acp_page.test.mjs` passes with updated assertions covering the new header and row layout. `test_web.py` covers the new `sessionName` field in `_subagents_payload`.

### Scope boundaries & non-goals

In scope:
- `acp.py` — `_subagents_payload` adds `sessionName` from internal crew dict. `_on_subagent_list` stores `sessionName` separately (derived from `_SUBAGENT_TASK_KEYS` at index 1, which is `"sessionName"`).
- `acp.html` — `setCrew`/`renderCrewPanel` redesigned; crew panel header added.
- `style.css` — `.acp-crew-*` rules replaced with lean row styles; no new dot CSS needed (reuses `session-status` classes).
- `tests/acp_page.test.mjs` and `tests/test_web.py` — updated/extended.

Out of scope:
- Dashboard session rows, workspace cards, rail rows — no changes.
- The `#acpStatus` topbar pill — not changed.
- kiro-cli protocol — the `sessionName` field is already present in the wire data from kiro-cli; this change only adds it to the server's forwarded payload.

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

- `acp.py:382` `SUBAGENT_LIST_METHOD = "_kiro.dev/subagent/list_update"` — carries the crew list.
- `acp.py:435` `_SUBAGENT_ROLE_KEYS = ("role", "agentName")`, `_SUBAGENT_TASK_KEYS = ("initialQuery", "sessionName")`. Measured 2026-08-11: `role` is always `"kiro_default"`, `sessionName` is always a short slug (e.g. `"count_src"`), `initialQuery` always wins over `sessionName` in `_first_text` because it is always non-empty.
- `acp.py:2497` `_on_subagent_list` — builds crew dict per entry with keys: `role, task, status, action, done, error, order, startedAt, stoppedAt`. Currently `task = _first_text(entry, _SUBAGENT_TASK_KEYS)[:MAX_SUBAGENT_TASK_CHARS]` = the full `initialQuery`. `sessionName` is consumed only as a fallback that never fires, then discarded.
- `acp.py:3263` `_subagents_payload` — serializes crew to wire: `{sessionId, role, task, status, action, done, error, startedAt, stoppedAt}`. `sessionName` is not forwarded. **This is the server-side change point.**
- `acp.html:2675` `renderCrewPanel()` — full rebuild of `crewPanel.textContent`, currently one `<button class="acp-crew-entry acp-crew-{state}">` per entry with three spans: `acp-crew-name`, `acp-crew-action`, `acp-crew-elapsed`.
- `acp.html:2729` `setCrew(next)` — manages `crew`, `crewPanel`, `crewAllDone`, `crewPanelTimer`. `crewPanel` is appended to `transcriptEl`. No header element exists currently.
- `acp.html:1033` `setTurn(active)` — sets `sendBtn.textContent = 'Working…'/'Send'`. Does NOT touch `statusEl` or the crew panel.
- `style.css:971` — current `.acp-crew-*` rules: card-style bordered buttons with `background:var(--surface)`, `border:1px solid var(--border)`, `border-radius`. State via left-border color: working=`#22c55e` (green, **wrong** — rail uses blue for working), done=`var(--text-dim)`, error=`#ef4444`.
- `style.css:192–197` — rail dot vocabulary: `status-thinking` = `#3b82f6` pulsing (= "working" in sub-agent terms), `status-idle` = `#e5e7eb`, `status-errored` = `#ef4444`. Class `session-status` is the dot element; these classes are not scoped to session rows and can be reused.
- `style.css:213` — `@media (prefers-reduced-motion)` already names `.acp-agentbar-dot.status-working` (forward-reference, currently has no backing element in the DOM). New dot elements in the crew rows should use `session-status` + the appropriate status class; the reduced-motion rule already covers `session-status.status-working`.
- `tests/acp_page.test.mjs` — covers all JS behavior. Any function or rendering change needs test coverage here.
- `tests/test_web.py` — covers Python behavior. The new `sessionName` field in `_subagents_payload` needs a test.
- AGENTS.md constraint: never restart PowerAtlas autonomously.

### 5. Risks & mitigations

- **`sessionName` not always present**: kiro-cli 2.16.2 always populates it (measured), but a future build or a sub-agent started without a `stages` DAG label might not. Mitigated by the JS fallback: `entry.sessionName || entry.task.slice(0, 30).trim()`.
- **`session-status` dot class reuse**: the `session-status` sizing rule (defined on `acp.html` session rows) may not be globally accessible — need to verify the selector is not scoped to `.session-row` context. If it is, a new `.acp-crew-dot` sizing rule (same dimensions) would be needed. Decidable by reading the CSS rule — marked as an implementation-time check.
- **`acp.py` crew dict doesn't store `sessionName` separately**: `_on_subagent_list` currently only stores `task` (= `initialQuery`). To forward `sessionName`, either (a) store `sessionName` as a separate field in the crew dict and add it to `_subagents_payload`, or (b) re-derive it from the original wire entry — but the entry is not stored, so (a) is the only viable path. This adds one field to every crew dict entry.
- **Mobile layout**: crew panel is in the transcript, full-width at <768px. Lean rows with dot + 3 text spans should be fine but the `max-width:40%` name cap on the old `.acp-crew-name` needs to be re-evaluated for the new layout (the role column now shares the row with the name).

### 6. Resolved decisions

- Q1: What should the crew card "name" show? — A: `sessionName` (short per-stage slug) as primary bold label, with `role`/agent name as muted secondary. — Decision: `sessionName` bold, `role` muted inline, `action` trailing muted.
- Q2: Where should "Orchestrating (N agents)" appear? — A: crew panel header row (Option C), not topbar. — Decision: header `<div>` at top of `crewPanel`, updated on every `renderCrewPanel()` call.
- Q3: Row style? — A: lean link-like rows, dot + text, no card fill, hover = text highlight. Color dots matched to rail vocabulary (blue-pulsing / grey / red). — Decision: redesign `.acp-crew-*` CSS; reuse `session-status` dot classes.
- Q4a: Header text when crewAllDone? — A: "Done (N agents)". — Decision: `crewAllDone ? 'Done (' + n + ' agents)' : 'Orchestrating (' + n + ' agents)'`.
- Q4b: Fallback when `sessionName` empty? — A: first ~30 chars of `initialQuery` truncated. — Decision: `entry.sessionName || entry.task.slice(0, 30).trim()` (with `entry.task` = `initialQuery`).

### 7. Open items

- **`session-status` CSS selector scope**: verify the sizing rule for `.session-status` dots is not scoped to a session-row container. If it is, add a new `.acp-crew-dot` rule in style.css with the same dimensions. Decidable by reading style.css around line 185–192.
- **`sessionName` storage in `_on_subagent_list`**: the crew dict needs a new `sessionName` key. Confirm the exact wire field name kiro-cli uses (`sessionName` vs `session_name` vs another key) — currently resolved as `"sessionName"` from the `_SUBAGENT_TASK_KEYS[1]` constant, but the constant name and the wire key name should be double-checked at implementation time.

### 8. Recommended approach

**Phase 1 — Python: forward `sessionName` through the wire**
- In `_on_subagent_list` (acp.py:2497): extract and store `sessionName` separately. `_first_text(entry, ("sessionName",))` is the cleanest way — same helper, single-key tuple. Store as `"sessionName": session_name_value or ""` in the crew dict alongside `task`.
- In `_subagents_payload` (acp.py:3263): add `"sessionName": entry.get("sessionName", "")` to the serialized dict.
- In `test_web.py`: add/update assertion that `_subagents_payload` includes the `sessionName` field.

**Phase 2 — JS: crew panel header + lean row redesign**
- In `setCrew` (acp.html:2729): after creating `crewPanel`, create a `div.acp-crew-header` as the first child. Update it on every `renderCrewPanel()` call.
- In `renderCrewPanel` (acp.html:2675): (a) update/create the header div with "Orchestrating (N)" / "Done (N)" text, (b) replace card-button rows with lean rows: `<button class="acp-crew-row acp-crew-row-{state}">` containing `<span class="session-status {dotClass}">`, `<span class="acp-crew-label">sessionName_or_fallback</span>`, `<span class="acp-crew-role">role</span>`, `<span class="acp-crew-action">action</span>`, `<span class="acp-crew-elapsed">elapsed</span>`.
- Name fallback: `var label = entry.sessionName || (entry.task || '').slice(0, 30).trim() || 'agent';`
- Dot class mapping: `subagentState(entry) === 'working' ? 'status-thinking' : subagentState(entry) === 'error' ? 'status-errored' : 'status-idle'`.
- Keep `openSubagent(entry.sessionId)` as the click handler.
- In `tests/acp_page.test.mjs`: add tests for header text, dot class, label fallback, hover style class.

**Phase 3 — CSS: replace crew panel styles**
- Remove card-style `.acp-crew-entry` rules.
- Add lean row rules for `.acp-crew-row`: `display:flex; align-items:center; gap:8px; padding:4px 8px; background:transparent; border:none; cursor:pointer; font-size:12px; text-align:left; width:100%; color:var(--text-muted)`.
- Hover: `color:var(--text)` (no background change — link-style).
- `.acp-crew-label`: `font-weight:600; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:50%;`.
- `.acp-crew-role`: `color:var(--text-dim); white-space:nowrap; flex-shrink:0;`.
- `.acp-crew-action`: `flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;`.
- `.acp-crew-elapsed`: unchanged from current.
- `.acp-crew-header`: `font-size:11px; color:var(--text-dim); padding:2px 8px 4px; text-transform:uppercase; letter-spacing:0.05em;`.
- Keep `.acp-crew-panel` container rules (border-left, padding, margin, flex column).

### 9. QA environment

- Start PowerAtlas: `.venv-PowerAtlas\Scripts\power-atlas` (Windows)
- Navigate to `http://127.0.0.1:<port>/acp`
- Exercise via a kiro-cli session that uses the `subagent` tool with 2–3 stages
- JS test suite: `node tests/acp_page.test.mjs` (run after every JS/HTML/CSS change)
- Python test suite: `.venv-PowerAtlas\Scripts\python -m pytest tests/test_web.py -x` (run after Python changes)

## Harness Improvement Opportunities
