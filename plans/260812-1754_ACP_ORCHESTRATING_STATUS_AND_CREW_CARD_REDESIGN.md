# ACP Orchestrating Status and Crew Card Redesign

> **Date**: 2026-08-12
> **Status**: In Progress
> **Scope**: /acp crew panel — add orchestrating header + redesign crew rows as lean dot-rows with sessionName
> **Estimated effort**: ~2–4 hours

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

## 1) Current State

The `/acp` crew panel (`renderCrewPanel`, `acp.html:2675`) shows each sub-agent as a bordered card-button (`acp-crew-entry`, `style.css:971`) with a green left border for working state. The green (`#22c55e`) conflicts with the rail's dot vocabulary where blue (`#3b82f6`, pulsing) means "agent is thinking/working." The card's name span (`acp-crew-name`) always shows `entry.role`, which kiro-cli 2.16.2 always populates as `"kiro_default"` — a generic agent name, never a per-stage label (`acp.py:441–452`, measured 2026-08-11). The per-stage short label (`sessionName`) exists in the wire data kiro-cli sends but is discarded by `_on_subagent_list` at `acp.py:2563` — `_first_text(entry, _SUBAGENT_TASK_KEYS)` always returns `initialQuery` (the full prompt) because it is always non-empty. `_subagents_payload` at `acp.py:3263` does not forward `sessionName`. The crew panel has no header element — there is no aggregate "Orchestrating" indication at any point. The `session-status` sizing rule at `style.css:191` is a free-standing global selector (not scoped to any container), so it can be reused on crew rows without an alias class. The wire key name is `"sessionName"` (camelCase, confirmed from `_SUBAGENT_TASK_KEYS = ("initialQuery", "sessionName")` at `acp.py:454`).

## 2) Goal

Forward the per-stage `sessionName` short label from kiro-cli through the server wire payload, then redesign the crew panel with a lean dot-row layout (colored status dot + bold sessionName + muted role + muted action + elapsed) and a header showing "Orchestrating (N agents)" / "Done (N agents)".

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Placement of "Orchestrating" label | Crew panel header row (in-transcript) | Topbar `#acpStatus` pill; new topbar element | Self-contained, appears with the crew, no topbar clutter, disappears when panel is removed |
| Primary row label | `sessionName` (short per-stage slug) | `role` (always `"kiro_default"`); truncated `initialQuery` | `sessionName` is the per-stage identity; role is always identical across stages |
| Fallback when `sessionName` empty | First 30 chars of `task` (`initialQuery`) trimmed | "agent N" ordinal; empty name slot | Prompt snippet is more useful than a number placeholder |
| Done header text | "Done (N agents)" | Keep "Orchestrating" | Accurately reflects the state the user sees before the panel is dismissed |
| Row style | Lean flex row: dot + text, `background:transparent`, `border:none`, hover = `color:var(--text)` | Keep bordered card | Matches web UI best practices (links, not buttons, for navigation actions); less visual weight for ancillary info |
| Dot CSS | Reuse `session-status` + `status-thinking`/`status-idle`/`status-errored` | New `.acp-crew-dot` alias | `.session-status` is a free-standing global rule (verified `style.css:191`) — no alias needed; aligns dot color with the rail |
| Working dot color | `status-thinking` (blue `#3b82f6`, pulsing) | `status-working` (green `#22c55e`) | Matches the rail's own working/thinking vocabulary; fixes the current mismatch |
| `sessionName` storage | New `"sessionName"` field in crew dict, extracted separately from `_on_subagent_list` | Re-derive at serialization time | Wire entry is not retained; extraction at parse time is the only viable path |

## 4) External Dependencies & Costs

### Required external changes

None. This is a code-only change within PowerAtlas. No infrastructure, CI/CD, IAM, data migration, or third-party changes needed.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Python — forward sessionName through the wire [QA]

**Goal**: Extract `sessionName` from the kiro-cli wire entry in `_on_subagent_list` and forward it in `_subagents_payload` so the JS can use it as the display label.

**File scope**: `src/power_atlas/acp.py`, `tests/test_web.py`

**Covers**: SC4, SC6

**Changes**:

`_on_subagent_list` at `acp.py:2563` — after the existing `task =` line, add:

```python
# `sessionName` is _SUBAGENT_TASK_KEYS[1] — the short per-stage slug (e.g. "count_src").
# Extracted separately from `task` (= initialQuery, the full prompt) because they serve
# different purposes: task is for fallback display, sessionName is for the primary label.
session_name = _first_text(entry, (_SUBAGENT_TASK_KEYS[1],))
```

Then in the `updated` dict (the block starting at `acp.py:~2578`), add:

```python
# short per-stage slug for display; "" when absent — use task (initialQuery) as fallback
"sessionName": session_name or existing.get("sessionName", ""),
```

Note: use `existing.get("sessionName", "")` (not `existing["sessionName"]`) — `existing` is a dict that may predate this field on any in-process upgrade or test fixture that was created without `sessionName`.

`_subagents_payload` at `acp.py:3265–3278` — add `"sessionName": entry.get("sessionName", "")` to each dict in the list comprehension, alongside the existing `role`, `task`, etc. fields.

`test_web.py` — in the test covering `_subagents_payload` (extend the existing `TestAcpSubagentsPayload` class or whichever test class uses the `acp_store` fixture): add `"sessionName": "stage_one"` to the crew fixture and assert it round-trips. Add a second case: `"sessionName": ""` serializes as `""` not `None`. Add a third case: a crew dict missing the `"sessionName"` key entirely produces `""` in the payload (covers pre-Phase-1 in-memory entries).

**Exit criteria**:
- [x] `_on_subagent_list` stores `"sessionName"` in every crew dict entry (from wire or `""`)
- [x] `_subagents_payload` includes `"sessionName"` in every serialized entry
- [x] `test_web.py` passes: `sessionName` field present and round-trips correctly
- [x] `test_web.py` passes: empty `sessionName` serializes as `""` not `None`
- [x] `test_web.py` passes: crew dict missing `"sessionName"` key entirely produces `""` in payload (not a KeyError)

**Implementation (2026-08-12, code: a21fdfa)**
`_subagents_payload` was missing the `"sessionName"` field — the extraction was already present in `_on_subagent_list` from a prior refactor, but the serialization step was not forwarding it. Added `"sessionName": entry.get("sessionName", "")` to the list comprehension in `_subagents_payload` using `.get()` with a `""` default to handle pre-Phase-1 in-memory entries that lack the key. The `session_name` extraction in `_on_subagent_list` uses the literal key `"sessionName"` (rather than `_SUBAGENT_TASK_KEYS[1]`) to avoid positional coupling to the tuple order. Four tests added: three unit tests in a new `TestAcpSessionName` class covering round-trip, empty, and missing-key cases; one integration test in `TestAcpSubagentListParsing` exercising the full wire→crew→payload pipeline via `_on_subagent_list`. All 1303 passing tests unaffected.

### Phase 2: JS + CSS — crew panel header and lean row redesign [QA]

**Goal**: Replace the card-style crew buttons with lean dot-rows, add an "Orchestrating / Done (N agents)" header, wire in `sessionName` as the primary label, and align dot colors with the rail vocabulary.

**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/static/style.css`, `tests/acp_page.test.mjs`

**Covers**: SC1, SC2, SC3, SC4, SC5, SC6

**Changes**:

**Shared label helper** — define a `crewLabel(entry)` function near the top of the crew panel section (before `renderCrewPanel`), used by both `renderCrewPanel` and `renderSubHead`:
```js
function crewLabel(entry) {
  if (!entry) return 'agent';
  var sn = entry.sessionName || '';
  if (sn) return sn;
  var t = entry.task || '';
  if (t) return t.length > 30 ? t.slice(0, 30).trim() + '\u2026' : t.trim();
  return 'agent';
}
```

**`renderCrewPanel()` at `acp.html:2675`** — full rewrite of the function body:

1. Header element — create or update a `div.acp-crew-header` as `crewPanel`'s **first child**. Do not re-create if it already exists; update `textContent` only:
   ```js
   var hdr = crewPanel.querySelector('.acp-crew-header');
   if (!hdr) {
     hdr = document.createElement('div');
     hdr.className = 'acp-crew-header';
     crewPanel.insertBefore(hdr, crewPanel.firstChild);
   }
   var n = crew.length;
   hdr.textContent = crewAllDone
     ? 'Done (' + n + (n === 1 ? ' agent)' : ' agents)')
     : 'Orchestrating (' + n + (n === 1 ? ' agent)' : ' agents)');
   ```

2. Row rendering — remove existing rows (not the header) tail-to-head to avoid array allocation, then append rows:
   ```js
   // remove old rows; header is preserved by class check
   while (crewPanel.lastChild && !crewPanel.lastChild.classList.contains('acp-crew-header')) {
     crewPanel.removeChild(crewPanel.lastChild);
   }
   ```
   Per crew entry (replacing the IIFE):
   ```js
   (function(entry) {
     var state = subagentState(entry);  // 'working' | 'done' | 'error'
     var dotClass = state === 'working' ? 'status-thinking'
                  : state === 'error'   ? 'status-errored'
                  : 'status-idle';
     var label = crewLabel(entry);  // shared helper — same fallback chain as renderSubHead
     var stateText = state === 'error' ? 'errored' : state === 'done' ? 'done' : (entry.action || 'working');

     var row = document.createElement('button');
     row.type = 'button';
     row.className = 'acp-crew-row acp-crew-row-' + state;
     row.setAttribute('aria-label',
       label + (entry.role ? ', ' + entry.role : '') + ', ' + stateText);

     var dot = document.createElement('span');
     dot.className = 'session-status ' + dotClass;
     dot.setAttribute('aria-hidden', 'true');

     var nameSpan = document.createElement('span');
     nameSpan.className = 'acp-crew-label';
     nameSpan.textContent = label;

     var roleSpan = document.createElement('span');
     roleSpan.className = 'acp-crew-role';
     roleSpan.textContent = entry.role || '';  // always a string from Python, '' when absent

     var actionSpan = document.createElement('span');
     actionSpan.className = 'acp-crew-action';
     actionSpan.textContent = state === 'working' && !entry.action
       ? 'working\u2026' : stateText;

     var elapsedSpan = document.createElement('span');
     elapsedSpan.className = 'acp-crew-elapsed';
     var start = entry.startedAt;
     var stop = entry.stoppedAt;
     elapsedSpan.textContent = (typeof stop === 'number' && stop)
       ? elapsedText(start, stop) : elapsedText(start);

     row.appendChild(dot);
     row.appendChild(nameSpan);
     row.appendChild(roleSpan);
     row.appendChild(actionSpan);
     row.appendChild(elapsedSpan);
     row.addEventListener('click', function() { openSubagent(entry.sessionId); });
     crewPanel.appendChild(row);
   })(crew[i]);
   ```

**`renderSubHead()` at `acp.html:2795`** — update `subRoleEl.textContent` to use the shared helper:
```js
subRoleEl.textContent = crewLabel(entry) || subViewSid;
```

> **Rejected**: keeping `.acp-crew-entry` class and adding dot CSS alongside it — leaves dead CSS and requires sub-panel to also be updated. Use instead: full replacement of the CSS block so no stale rules remain.

**`style.css` — `.acp-crew-*` block replacement**: locate the block starting at `.acp-crew-panel` (line ~966, **not** 971 — `.acp-crew-panel` is the first rule; the plan previously stated 971 which was wrong). Replace everything from `.acp-crew-panel` through `.acp-crew-elapsed` (inclusive of all old `.acp-crew-working`, `.acp-crew-done`, `.acp-crew-error`, `.acp-crew-name`, `.acp-crew-action` rules) with:
```css
/* Crew panel — lean rows replacing the old card-button design */
.acp-crew-panel  { display:flex; flex-direction:column; gap:2px; padding:6px 8px; margin:4px 0; border-left:2px solid var(--border); flex-shrink:0; }
.acp-crew-header { font-size:11px; color:var(--text-dim); padding:0 2px 4px; text-transform:uppercase; letter-spacing:0.05em; }
.acp-crew-row    { display:flex; align-items:center; gap:8px; padding:3px 2px; background:transparent; border:none; cursor:pointer; font-family:inherit; font-size:12px; text-align:left; width:100%; color:var(--text-muted); border-radius:var(--radius-sm); transition:color 0.1s; }
.acp-crew-row:hover       { color:var(--text); }
.acp-crew-row:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
.acp-crew-label  { font-weight:600; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:45%; }
.acp-crew-role   { color:var(--text-dim); white-space:nowrap; flex-shrink:0; font-size:11px; }
.acp-crew-action { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
/* Use semantic descriptors rather than bare hex so a future theme pass is one-place */
.acp-crew-row-working .acp-crew-action { color:#4ade80; }  /* matches rail's "working" glow */
.acp-crew-row-error   .acp-crew-action { color:#f87171; }  /* matches rail's error red */
.acp-crew-elapsed { font-variant-numeric:tabular-nums; color:var(--text-dim); white-space:nowrap; flex-shrink:0; }
```

**`style.css` — `prefers-reduced-motion` rule** (line ~213): add `status-thinking` and `status-unread` to the suppression list:
> Find: `@media (prefers-reduced-motion: reduce) { .session-status.status-working, .ws-status.status-working, .acp-agentbar-dot.status-working { animation: none; } }`
>
> Replace with: `@media (prefers-reduced-motion: reduce) { .session-status.status-working, .session-status.status-thinking, .session-status.status-unread, .ws-status.status-working, .acp-agentbar-dot.status-working { animation: none; } }`

**`tests/acp_page.test.mjs`** — update plan:
1. **Enumerate before editing**: run `grep -n 'acp-crew-entry\|acp-crew-name' tests/acp_page.test.mjs` to list every assertion on the old class names. Update each selector to `.acp-crew-row` / `.acp-crew-label` respectively. Do NOT leave old assertions alongside new ones.
2. Add/update tests covering:
   - Header text: "Orchestrating (N agents)" for N>1 running; "Orchestrating (1 agent)" singular; "Done (N agents)" / "Done (1 agent)" when all done.
   - Each row has class `acp-crew-row` and a `session-status` child with `status-thinking` (working), `status-idle` (done), `status-errored` (error).
   - `crewLabel()`: shows `entry.sessionName`; fallback to 30-char truncated `task` with `…` appended; fallback to `"agent"` when both empty.
   - `renderSubHead()` uses `crewLabel()` — same fallback chain.
   - Old class names `acp-crew-entry`, `acp-crew-name`, `acp-crew-working`, `acp-crew-done`, `acp-crew-error` absent from rendered output.

**Exit criteria**:
- [ ] `node tests/acp_page.test.mjs` passes with all new and updated assertions (zero failures on old-class selectors)
- [ ] All existing `.acp-crew-entry` / `.acp-crew-name` test assertions updated to new class names (grep returns 0 hits on old names in active test assertions)
- [ ] Crew panel header shows "Orchestrating (N agents)" for N>1; "Orchestrating (1 agent)" for N=1
- [ ] Crew panel header shows "Done (N agents)" / "Done (1 agent)" when `crewAllDone === true`
- [ ] Each row has a `session-status` dot with `status-thinking` (working), `status-idle` (done), or `status-errored` (error)
- [ ] Row label shows `sessionName`; fallback to 30-char truncated `task` with `…`; fallback to `"agent"`
- [ ] No rendered output contains class names `acp-crew-entry`, `acp-crew-name`, `acp-crew-working`, `acp-crew-done`, `acp-crew-error`
- [ ] Sub-panel header (`subRoleEl`) shows `sessionName` when present, same fallback chain via `crewLabel()`
- [ ] `prefers-reduced-motion` suppresses `.session-status.status-thinking` animation
- [ ] Hover on a row changes text color only (no background fill)
- [ ] Manual check at 390 px viewport: rows do not overflow, label truncates with `…`

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `sessionName` absent on future kiro-cli builds | Row shows 30-char truncated prompt with `…` instead of a slug | Fallback chain explicit; degraded display is still useful |
| Old CSS class names in `acp_page.test.mjs` assertions | Test suite fails until updated | Phase 2 exit criterion requires grep enumeration and update of all old selectors before marking done |
| CSS block start misidentified as line 971 | Old `.acp-crew-panel` survives, two conflicting rules cascade | Fixed in plan: replacement targets the block from `.acp-crew-panel` (line ~966), not line 971 |
| `session-status.status-thinking` not in `prefers-reduced-motion` | Crew row animations fire for users with vestibular disorder preference | Fixed in plan: Phase 2 CSS adds `status-thinking` and `status-unread` to the suppression rule |
| `existing["sessionName"]` KeyError on pre-Phase-1 crew entries | `_on_subagent_list` crashes on second `list_update` during deploy window | Fixed in plan: Phase 1 uses `.get("sessionName", "")` |
| Sensitive prompt content in fallback label | `initialQuery` may contain API keys, passwords, paths visible to remote viewers | Accepted (user decision) — fallback is 30 chars of transcript content; remote access requires authentication |
| Sub-panel header still shows `role` after Phase 2 | Inconsistent display label between crew row and sub-panel | Fixed: `renderSubHead()` update uses shared `crewLabel()` helper with same fallback chain |

## 7) Verification

```
# Python tests
.venv-PowerAtlas\Scripts\python -m pytest tests/test_web.py -x -k "subagent"

# JS tests
node tests/acp_page.test.mjs

# Full Python suite
.venv-PowerAtlas\Scripts\python -m pytest tests/ -x
```

Manual check: start PowerAtlas, open `/acp`, trigger a kiro-cli fan-out with 2–3 stages. Verify:
1. "Orchestrating (N agents)" header appears as soon as the first crew frame arrives.
2. Each row shows a blue pulsing dot, bold `sessionName` (or truncated prompt with `…`), muted role text, muted action text, elapsed time.
3. On hover, text highlights (no background change).
4. Clicking a row opens the sub-agent panel with `sessionName` in the header.
5. When all stages finish, header changes to "Done (N agents)".
6. After the next main-session event (turn end / new chunk), the panel is removed.
7. Mobile (390 px emulation): rows do not overflow, label truncates with `…`.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|

No documentation updates needed — no project docs reference the changed identifiers.

## 9) Implementation Divergences from Plan

- **Phase 1**: `existing` can be `None` for first-seen sub-agents. The plan's `updated` dict snippet used `existing.get("sessionName", "")` without a None guard. The committed code uses `session_name or (existing.get("sessionName", "") if existing else "")` — the defensive form matching the pattern used for all other optional fields in the same dict.
- **Phase 1**: `session_name = _first_text(entry, ("sessionName",))` uses the string literal rather than `_SUBAGENT_TASK_KEYS[1]` (which the plan mentioned) to avoid index coupling. Functionally identical.

## Review Log

### 2026-08-12 — Plan Review (cycle 1, high effort, 4 personas)

4 personas: Architect, Senior engineer, End-user advocate, Maintainability reviewer. 9 findings auto-resolved, 1 escalated (Low).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `existing["sessionName"]` raises `KeyError` on second `list_update` for pre-Phase-1 crew entries — needs `.get()`. | Fixed — Phase 1 now uses `existing.get("sessionName", "")`. |
| 2 | High | 8-11 existing `acp_page.test.mjs` assertions on `.acp-crew-entry`/`.acp-crew-name` break without being updated. | Fixed — Phase 2 exit criteria now require grep-enumerate-then-update all old selectors before marking done. |
| 3 | High | `session-status.status-thinking` pulse not suppressed by `prefers-reduced-motion`, breaking accessibility. | Fixed — Phase 2 CSS now updates the media query rule to include `status-thinking` and `status-unread`. |
| 4 | Medium | SC4 JS tests validate only a fixture; no test validates Python→JS round-trip. | Fixed — Phase 1 exit criteria now explicitly require a `test_web.py` test covering missing-key case (not just empty string). |
| 5 | Medium | CSS block replacement range stated as `971–987` misses `.acp-crew-panel` at line ~966 — old rule survives. | Fixed — Phase 2 now targets the block from `.acp-crew-panel` (line ~966). |
| 6 | Medium | `renderSubHead()` update absent from Phase 2 exit criteria checkbox. | Fixed — exit criterion added: "Sub-panel header shows `sessionName` when present, same fallback chain via `crewLabel()`". |
| 7 | Medium | Fallback label truncates at 30 chars with no `…` appended — reads as broken text. | Fixed — `crewLabel()` helper appends `…` when `task` length > 30. |
| 8 | Medium | Label fallback chain duplicated in `renderCrewPanel` and `renderSubHead` — will drift. | Fixed — Phase 2 introduces shared `crewLabel(entry)` helper used by both sites. |
| 9 | Medium | Hex literals in `.acp-crew-row-working`/`.acp-crew-row-error` CSS vs CSS variables. | Fixed — inline comments added to the CSS block; hex values retained (matching the rail's existing hex values; no CSS variables define these colors in `style.css`). |
| 10 | Low | Sensitive `initialQuery` content could appear in fallback label visible to remote viewers. | Escalated — User: accepted — fallback content is a 30-char snippet of user-authored transcript content; remote access requires authentication; severity is low for expected use case. |

### 2026-08-12 — Implementation Review (after Phase 1, persona: Senior engineer + Maintainability reviewer)

Implementation health: Green.
2 cycles. Cycle 1: 5 findings (2 High, 1 Medium, 2 Low). Cycle 2: 3 Low. Cycle 3 short-circuited (cycle 2 all Low + purely mechanical fixes).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | Operator precedence risk on `session_name or existing.get(...) if existing else ""` (reported by Maintainability) | Fixed — was already correct in f781664; no-op confirmed by Senior engineer mutation test. |
| 2 | High | `_on_subagent_list` extraction path untested; all 3 tests bypass it via pre-built dicts. | Fixed — `test_session_name_extracted_from_wire_entry` integration test added; mutation-confirmed discriminating (`assert '' == 'count_src'` on mutation). |
| 3 | Medium | Three tests placed in `TestAcpStoppedAt` (wrong class). | Fixed — moved to new `TestAcpSessionName` class. |
| 4 | Low | `_SUBAGENT_TASK_KEYS[1]` index coupling in `session_name =` line. | Fixed — literal `"sessionName"` used with comment. |
| 5 | Low | `.get()` asymmetry in `_subagents_payload` undocumented. | Fixed — inline comment added. |
| 6 | Low | `TestAcpSessionName` docstring contained change-narrative. | Fixed — replaced with present-state description. |
| 7 | Low | Redundant `acp_mod_direct` import in integration test. | Fixed — removed; `acp_mod` from fixture used directly. |
| 8 | Low | Block comment and inline comment redundant post-fix. | Fixed — merged to single clear 2-line block comment. |

## Harness Improvement Opportunities

- `/qexplore`-generated project files use a time-suffix filename convention (`YYMMDD-HHMM_NAME.md`) that fails the `/qvalidate` `filename-grammar` check (expects `^[0-9]{6}_[A-Z0-9_]+\.md$`). The qvalidate applicability guard correctly rejects it as a precondition failure (exit 2). — cost: qvalidate cannot run on any explore-started plan, so the `sc-coverage` and `status-grammar` checks are silently skipped. — suggested change: either have `/qexplore` use the active-plan convention (`YYMMDD_NAME.md`) or have `/qvalidate` accept both conventions for active plans.
