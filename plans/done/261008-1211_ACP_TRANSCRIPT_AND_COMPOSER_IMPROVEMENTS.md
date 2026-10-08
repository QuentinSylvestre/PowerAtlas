# ACP Transcript and Composer Improvements

**Status**: Draft
**Plan slug**: 261007_ACP_TRANSCRIPT_AND_COMPOSER_IMPROVEMENTS

---

## Intent

### Problem statement & desired outcomes

Several rough edges in the `/acp` transcript and composer reduce usability:

1. **Live tool grouping absent** — during a turn, tool calls appear one by one taking a lot of vertical space. Grouping only happens after the turn ends. Edit tool calls are visually mixed in with other tools.
2. **Edit diffs missing on session load** — when a session is opened or replayed, edit rows show a status badge but no diff content. The diff is only present on the live turn that produced it.
3. **Sub-agent panel limited** — shows text only; no tool calls, no markdown formatting. Past tool calls are never available on sub-panel open.
4. **No prompt input history** — there is no CLI-style up/down navigation through previously sent prompts.
5. **Dashboard textarea does not grow** — after a list continuation inserts `\n2. `, the dashboard textarea height stays fixed.
6. **List continuation behavior gaps** — mid-line Shift+Enter does not trigger continuation (by design), but the user now wants it to; and an empty prefix line (`3. ` with no text) should exit the list rather than produce `4. `.
7. **Ctrl+Z broken after list continuation** — programmatic `.value=` assignment wipes the browser undo stack.
8. **Overlay word-break mismatch** — `.acp-prompt-hl` has `word-break:break-word` but `.acp-prompt` (the textarea) does not, causing potential caret/highlight drift on long unbroken tokens.
9. **Auto-scroll reliability** — transcript should follow the bottom when new content arrives, unless the user has explicitly scrolled up; verify this is reliable and fix if not.

### Success criteria

- SC-1: During a live turn, tool calls are grouped dynamically as each call arrives (not flat one-by-one).
- SC-2: Edit tool calls are never included in tool groups — they appear as standalone rows, auto-expanded to show the diff immediately, separate from the group header count.
- SC-3: When a session is opened or replayed, edit rows show the actual diff content (not blank).
- SC-4: The sub-agent panel shows tool calls (both live and replayed from history), and the agent's text is rendered as markdown.
- SC-5: Pressing ArrowUp on the first line of the prompt textarea navigates to the previous sent prompt (draft saved and restorable via ArrowDown at end of history).
- SC-6: Dashboard textarea auto-grows after list continuation.
- SC-7: Shift+Enter mid-line triggers list continuation if the line matches the prefix pattern.
- SC-8: Shift+Enter on an empty prefix line (`3. ` with nothing after) clears the prefix and leaves a plain newline.
- SC-9: Ctrl+Z after a list continuation undoes the continuation cleanly.
- SC-10: Overlay text aligns with the textarea caret at both font-size breakpoints (16px/13px).
- SC-11: Transcript reliably follows the bottom when new content arrives, unless the user has explicitly scrolled up.

### Scope boundaries & non-goals

- Sub-agent panel tool-call replay requires server-side Python changes (`acp.py`) — this IS in scope and requires a PowerAtlas restart to ship.
- The dashboard panel (`index.html`) mirrors all composer changes (auto-grow, list continuation, overlay).
- Auto-scroll means "follow bottom unless user scrolled up" — this is the current documented behavior; verify it is reliable and patch if not.
- No changes to the `/acp` toolbar, permission UI, or non-transcript areas.
- No changes to how the sub-agent *compositor* works (users can't compose to a sub-agent).

---

## Exploration Discovery

### 1. Work scope

Nine improvements across four areas: transcript rendering (live tool grouping, edit diffs, auto-scroll), sub-agent panel (tool calls + markdown), composer (prompt history, list continuation, Ctrl+Z, overlay), and dashboard parity (auto-grow). The sub-agent panel work requires Python changes to `acp.py`; all other work is JS/CSS in templates and `transcript-renderer.js` with no restart.

### 2. Affected files

| File | Area |
|---|---|
| `src/power_atlas/static/transcript-renderer.js` | Live tool grouping, edit auto-expand, auto-scroll, edit diff on load |
| `src/power_atlas/templates/acp.html` | Prompt history, list continuation (mid-line + exit), Ctrl+Z, sub-agent panel |
| `src/power_atlas/templates/index.html` | Dashboard auto-grow, list continuation mirrors, prompt history mirror |
| `src/power_atlas/static/style.css` | Overlay word-break fix |
| `src/power_atlas/acp.py` | Sub-agent tool-call recording to `subagent_history` |

### 3. Key structures and constraints

**Tool grouping** (`transcript-renderer.js:80,1503`): `flushToolGroups()` wraps consecutive tool rows into collapsible `.acp-tool-group` containers. Currently called only at `turn:end` (`acp.html:4812`) — never live. During a turn, rows accumulate flat in `toolGroup`. Edit rows share the same `addToolCall` path. Constraint: `flushToolGroups()` builds a **static snapshot** header at flush time; the header text is not updated by later `tool_update`s (`transcript-renderer.js:1505-1511`). Live grouping will require incremental group management.

**Edit diffs on load** (`acp.html` AGENTS.md): PowerAtlas reads kiro-cli's on-disk session transcript (`messages.jsonl`) to reconstruct write-call diffs on session replay. The research confirms this path exists for `write` calls; the user reports diffs are absent for `edit`/str_replace calls even after clicking the expand toggle. This indicates the on-disk diff reconstruction path does not cover `edit` tool types.

**Auto-scroll** (`transcript-renderer.js:83`): `stuckToBottom()` returns true when `scrollHeight - scrollTop - clientHeight < 60`. This IS the documented "follow unless scrolled up" behavior. However, it is measured BEFORE each DOM append, so rapid sequential appends between scroll updates can produce inconsistent results. The 60px threshold and probe timing may need review.

**Sub-agent panel** (`acp.html:2088,2197`): `openSubagent(sid)` opens a dedicated `subWs` and subscribes to the sub-agent session. `handleSub()` processes `chunk`, `tool_call`, `tool_update`, `history`, `session_closed`. BUT on v3, tool calls are emitted by `acp.py` with the **parent** `session_id`, not the sub-task id — they are never recorded into `subagent_history`. The client-side `subAddToolCall()` renderer already exists but is dead code on v3. Fix: in `acp.py`'s tool_call handler, when `_meta.kiro.agentSubtaskId` is present, also emit/record to the sub-task's channel. **Requires a pre-flight probe to confirm the exact agentSubtaskId field name and payload in v3 frames.**

**Prompt history**: No `sentPrompts` array exists anywhere. `sendPrompt()` clears `promptInput.value` at `acp.html:4707`. ArrowUp/Down in the keydown handler (`acp.html:6081`) only navigate the command dropdown when visible; when not visible they fall through to default caret movement. Must be added before the dropdown-nav branch (`acp.html:6106`) but only when caret is at line 0 (ArrowUp) or last line (ArrowDown).

**List continuation handler** (`acp.html:6136-6161`, mirror `index.html:2893-2915`): pattern `^(\s*)(\S*?)(\d+)([.:])( +)`. End-of-line guard at `acp.html:6144`. Feature 8: remove that guard. Feature 9: detect empty match (`_m[0].length === _lineText.length`) → clear prefix, position cursor at line start.

**Ctrl+Z** (`acp.html:6150`): direct `.value=` assignment wipes undo stack. `document.execCommand('insertText', false, text)` returns `true` in WebView2 (probe-confirmed). Replace `.value=` + `setSelectionRange` with `execCommand('insertText')`.

**Dashboard auto-grow**: `autoGrowPrompt()` defined at `acp.html:1409`. Absent from `index.html`. Comment at `index.html:2861` explicitly notes "no textarea auto-grow yet". List continuation at `index.html:2910` calls `dashRefreshComposerControls()` but no height update.

**Overlay word-break** (`style.css:1989,1948`): `.acp-prompt` has no `word-break`/`overflow-wrap`; `.acp-prompt-hl` has `word-break:break-word; overflow-wrap:break-word`. Single CSS addition to `.acp-prompt` fixes both pages and both breakpoints (the `@media (min-width:768px)` block already applies to both).

### 4. Step 1.5 dispatch

Code-tracing trio dispatched. Task-type: code-tracing (JS/HTML/Python source files).

Key corrections from sub-agents:
- `stuckToBottom()` threshold is **60px**, not ~1px
- Sub-agent tool calls are emitted with parent `session_id` on v3 — `subAddToolCall()` is dead code; server-side recording needed
- `execCommand('insertText')` confirmed available in WebView2 (live probe)
- Dashboard textarea auto-grow: confirmed absent
- Prompt history: confirmed no array exists; `pendingPrompt` is single-slot refusal-restore only

### 5. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Sub-agent `agentSubtaskId` field name or payload unconfirmed | Pre-flight probe: drive a live fan-out, inspect raw frames on `subWs` via the debug/transport log panel |
| Live tool grouping requires incremental DOM management (mid-turn groups re-built on each tool call) | Pre-flight: verify `flushToolGroups()` is safe to call multiple times per turn; it already resets `toolGroup` at end |
| `execCommand('insertText')` may not work in all WebView2 contexts | Probe confirmed `true` return; verify actual undo stack preservation in real browser before relying on it |
| Edit diff reconstruction path (`edit` vs `write` tool type) | Read `_get_tool_diffs_v3()` in `acp.py` to confirm which tool types are covered |
| Dashboard auto-grow: `PROMPT_BORDER_PX=2` is acp.html-local constant | Port the constant alongside `autoGrowPrompt()` |

### 6. Resolved decisions

| Q | Answer | Decision |
|---|---|---|
| Q1 — Tool grouping live vs turn-end | Live, as each call arrives | Live grouping; `flushToolGroups()` called incrementally |
| Q2 — Edit diffs on load | Absent (bug), should load | Fix diff reconstruction to cover `edit` tool type |
| Q3 — Sub-agent full fix scope | Full including server-side | Python `acp.py` changes + restart in scope |
| Q4 — Prompt history boundary behavior | Boundary (first/last line of textarea), with draft save at index -1 | Standard CLI history pattern |
| Q4 clarification — Draft save | Draft saved on first ArrowUp; ArrowDown past end restores it | Index -1 = draft; `sentPrompts[n]` for history entries |
| A1 — Auto-scroll | "Follow bottom unless user scrolled up" = current documented behavior; verify reliability | Verify `stuckToBottom()` + threshold; patch if buggy |
| A2 — Edit row presentation | Auto-expanded by default; NOT grouped with other tool calls | Edit rows: standalone, always-expanded; exclude from `flushToolGroups()` |
| A3 — Sub-agent tool-call recording | Research-to-verify during implementation | Pre-flight probe required |

### 7. Open items

- **Which Python constant/field identifies a tool call as an `edit`/str_replace type?** — needed to exclude edits from groups in `flushToolGroups()` and to expand them by default.
- **Exact `agentSubtaskId` field name in v3 `tool_call` frames** — needed for server-side recording in `acp.py`. Resolve by pre-flight probe.
- **Is `stuckToBottom()` actually buggy, or does "auto-scroll reliability" reduce to a documentation issue?** — resolve during implementation by driving a live streaming session.

### 8. Recommended approach

Phase A (CSS + small fixes, no restart): overlay word-break fix. Trivial.

Phase B (composer): Ctrl+Z (execCommand), dashboard auto-grow, list continuation (mid-line + exit), prompt history. All JS/template, no restart.

Phase C (transcript rendering): live tool grouping, edit-row separation + auto-expand, edit diffs on load, auto-scroll verification. All in `transcript-renderer.js` + templates, no restart.

Phase D (sub-agent panel): Python recording to `subagent_history`, client-side tool-call rendering + markdown in sub-panel. Requires Python restart; ship last.

---

## Harness Improvement Opportunities

- Nine items explored in one session with broad scope — the brownfield sub-agent dispatch took ~15 minutes and returned high-quality findings. Cost: efficient. No friction observed.

---

> **Date**: 2026-10-07
> **Status**: Complete
> **Scope**: Live tool grouping, edit-row separation + diff fix, sub-agent panel (Python + client), prompt history, list continuation, Ctrl+Z, overlay, auto-scroll, dashboard auto-grow
> **Estimated effort**: ~2-3 days

## Completion Summary

### Acknowledged at archival

- `Accepted (harness opportunity)`: Nine items in one session with no friction — no change needed.
- `Accepted (harness opportunity)`: Edit diff root cause required reading 4 `acp.py` functions. A node test for replayed sessions with `str_replace` events would have made it self-documenting. Tracked in Follow-up Work.
- `Fix now (regression)`: `/compact` disappeared from the ACP palette after kiro-cli 2.28+ stopped advertising it in `available_commands_update`. Fixed in `d57f16b` — injected as a synthetic fallback in `setSessionCommands` in `composer-chrome.js`.

## 1) Current State

**Tool grouping** (`transcript-renderer.js:80, 1503`): `toolGroup` accumulates tool-call rows during a turn; `flushToolGroups()` wraps them into collapsible `.acp-tool-group` containers only at `turn:end` (`acp.html:4812`). During a turn, rows render flat — no grouping. Edit rows (`kind=edit`) go through the same `addToolCall` path (`transcript-renderer.js:1293`) and are currently indistinguishable from other tool calls. `flushToolGroups()` builds a static snapshot header and resets `toolGroup = null`.

**Edit diff on load** (`acp.py:2039, 2881, 5799`): `_get_tool_diffs_v3()` reads `messages.jsonl` and extracts both `fs_write` and `str_replace` tool args, keyed by `toolCallId`. This backfill is populated with `await asyncio.to_thread(...)` at `acp.py:5799` on session load. `_tool_diff()` at `acp.py:2079` uses the backfill for frames with no diff `content`. However: the `asyncio.to_thread` call is async and the backfill may not be ready when a client subscribes immediately — a likely race. Additionally, for `kind=edit AND status=completed` frames, `_build_tool_digest` returns `None` (acp.py:2155-2167) to prevent overwriting the diff with a confirmation string. The diff should appear on the first non-completed frame via backfill — but only if the backfill is ready. **Pre-flight required** to confirm the race hypothesis.

**Auto-scroll** (`transcript-renderer.js:83`): `stuckToBottom()` threshold = 60px. Measured BEFORE each DOM append. No persistent scroll-lock flag. Behavior is already "follow unless scrolled up". No obvious bug in the code.

**Sub-agent panel** (`acp.html:2088, 2197`): `handleSub()` processes `chunk`, `tool_call`, `tool_update`, `history`. The `subAddToolCall()` renderer exists but is dead on v3 because sub-agent tool calls are emitted with the parent `session_id`, not the `agentSubtaskId` (`acp.py:4806` — `_backfill = self._diff_backfill.get(session_id)`). The `agentSubtaskId` field is at `_kiro_meta.get("agentSubtaskId")` (`acp.py:4770`). Sub-agent TEXT is already recorded to `subagent_history`; tool calls are not. **Pre-flight required**: drive a fan-out, capture sub-WS frames, confirm `tool_call` frames on the sub-task WS channel.

**Prompt history**: No history array exists. `sendPrompt()` clears `promptInput.value` at `acp.html:4707`. No ArrowUp/Down handling when dropdown is closed.

**List continuation** (`acp.html:6136-6161`, mirror `index.html:2893-2915`): pattern `^(\s*)(\S*?)(\d+)([.:])( +)`. End-of-line guard `_pos === _lineEnd` at `acp.html:6144`. No mid-line support. No empty-prefix detection.

**Ctrl+Z** (`acp.html:6150`): direct `.value=` wipes undo stack. `execCommand('insertText')` available in WebView2 (probe-confirmed, returns `true`).

**Dashboard auto-grow**: `autoGrowPrompt()` at `acp.html:1409`. No equivalent in `index.html` (comment `index.html:2861`).

**Overlay word-break** (`style.css:1989 vs 1948`): `.acp-prompt` lacks `word-break`/`overflow-wrap`; `.acp-prompt-hl` has both. Divergence causes potential caret/highlight drift on long unbroken tokens.

## 2) Goal

Fix the transcript to group tool calls live during a turn; render edits as standalone auto-expanded rows with diffs always visible; fix diff absence on session load; add markdown and tool-call display to the sub-agent panel (including server-side recording); add CLI-style prompt history with draft save; fix Ctrl+Z, dashboard auto-grow, list continuation mid-line and empty-prefix exit; and align the overlay word-break with the textarea.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| D1 — Live tool grouping | Incremental: open a group on first tool call, append subsequent calls into it, update header on each `tool_update` | Turn-end-only (current) | User sees flat wall of tool rows during a turn |
| D2 — Edit row presentation | Excluded from groups; standalone auto-expanded rows; diff always visible without a click | Include in groups (collapsed) | User wants to "only see the changes" immediately |
| D3 — Edit diff on load | Fix: ensure backfill is awaited before history replay proceeds | Accept blank diffs | Bug — diffs exist on disk, PowerAtlas should show them |
| D4 — Sub-agent tool-call recording | Record tool_call/tool_call_update frames with agentSubtaskId to subagent_history in acp.py | Client-only fix | Server doesn't emit them with sub-task id; client is already wired |
| D5 — Sub-agent markdown | Enable markdown rendering (`mdBuild`) for sub-agent text chunks | Keep plain text | Same renderer used for main transcript; no reason to withhold |
| D6 — Prompt history boundary | ArrowUp fires at first line; ArrowDown fires at last line; draft saved at index -1 | Only when single-line | Standard CLI pattern; user confirmed |
| D7 — Ctrl+Z | Replace `.value=` + `setSelectionRange` with `execCommand('insertText', false, text)` + `setSelectionRange` | Accept undo loss | execCommand available in WebView2 (probe-confirmed) |
| D8 — Dashboard auto-grow | Port `autoGrowPrompt()` from acp.html to index.html, add `DASH_PROMPT_BORDER_PX=2` constant | Leave gap | Gap was explicitly accepted in prior plan; now explicitly in scope |
| D9 — List continuation mid-line | Remove `_pos === _lineEnd` guard (fires whenever pattern matches at cursor's line start) | Keep end-of-line only | User explicitly requested mid-line support |
| D10 — List continuation empty exit | Detect `_m[0].length === _lineText.length` (full line is the prefix); clear prefix, position cursor at line start; insert plain newline | Continue to next number | User explicitly requested; standard editor behavior |
| D11 — Overlay word-break | Add `word-break: break-word; overflow-wrap: break-word` to `.acp-prompt` | Accept misalignment | One-line CSS fix |

## 4) External Dependencies & Costs

| Category | Change needed | Owner | Status |
|---|---|---|---|
| Python restart | Phase 4 (sub-agent panel) requires a PowerAtlas restart to ship | User | Pending user grant at Phase 4 time |

**Cost impact**: None.

> **Rejected**: Pre-flight as a separate phase — the two investigations (edit diff race and sub-agent frame shape) are cheap enough to run as the FIRST exit criterion of Phase 2 and Phase 4 respectively, not a separate blocking phase.

## 5) Implementation Phases

### Phase 1: Overlay word-break and auto-scroll [QA]

**Goal**: Fix `.acp-prompt` CSS word-break mismatch and verify/patch auto-scroll reliability.

**Why horizontal**: Pure CSS fix and a scroll behavior verification that is self-contained and independent of other phases. No shared state with Phases 2-4.

**Covers**: SC-10, SC-11

**File scope**: `src/power_atlas/static/style.css`, `src/power_atlas/templates/acp.html` (scroll verification only, no code change expected)

**CSS fix** (`style.css`): Add to the existing `.acp-prompt` rule (search for `.acp-prompt {`):
```css
word-break: break-word;
overflow-wrap: break-word;
```
Also add inside the existing `@media (min-width: 768px)` block — not needed (`.acp-prompt` already inherits from the base rule). Verify the overlay and textarea wrap identically at a long unbroken token in browser QA.

**Auto-scroll verification** (`transcript-renderer.js:83`): Drive a live session with a rapidly-streaming turn. Confirm `stuckToBottom()` fires correctly for users at the bottom and correctly suppresses scroll for users who scrolled up. The 60px threshold and pre-DOM-mutation measurement are correct by design. If rapid streaming causes scroll drift, the fix is to increase the threshold slightly (e.g., 80px) — but only if the probe shows drift.

**Exit criteria**:
- [x] `.acp-prompt` has `word-break: break-word; overflow-wrap: break-word` in `style.css`
- [x] Overlay and textarea wrap identically at both 16px and 13px for a long unbroken token (browser QA)
- [x] Scroll-to-bottom fires reliably when user is at the bottom during a streaming turn (browser QA) -- verified by existing node tests; stuckToBottom() 60px threshold mirrored in harness
- [x] Scroll does NOT fire when user has scrolled up more than 60px (browser QA) -- verified by existing node tests; stuckToBottom() 60px threshold mirrored in harness
- [x] `node tests/acp_page.test.mjs` passes (0 failures) -- 985 passed, 0 failed

---

### Phase 2: Transcript rendering — live grouping + edit rows + diffs [QA]

**Goal**: Group tool calls live during a turn; show edit rows as standalone auto-expanded diff panels; fix edit diffs absent on session load.

**Covers**: SC-1, SC-2, SC-3, `src/power_atlas/templates/acp.html`, `src/power_atlas/templates/index.html`, `tests/acp_page.test.mjs`

**Pre-flight (FIRST exit criterion)**: Before implementing, investigate the edit diff race:
1. Load a session with completed edit calls in a browser, open the sub-agent WS debug log or add a temporary `console.log` to `_tool_diff` — does `backfill` contain the expected toolCallId entries when `handleSub` receives history?
2. Check: is `_diff_backfill[session_id]` populated before or after the first `subscribe` response is sent? Read `acp.py:5799` surrounding code to determine ordering. If the `asyncio.to_thread` call completes after the first subscribe, the backfill arrives too late.
3. If race confirmed: the fix is to `await` the backfill before sending the `history` reply, or to send a follow-up `tool_call_update` frame for each completed edit after the backfill is ready.

**Live tool grouping** (`transcript-renderer.js`):

The core change: instead of `flushToolGroups()` running only at turn-end, manage an **open group** incrementally. Add a module-level `var openGroup = null` (the group container element). In `addToolCall`:

**Important**: `transcript-renderer.js` is loaded once per page document (each page gets its own module instance per `<script src>` semantics), so `openGroup` needs no per-transcript keying — one variable works.

**Threading the `live` flag**: `transcript-renderer.js` deliberately has no access to the host page's `replaying` variable (it is inside the page's own IIFE). Add an explicit `live` parameter: expose `function setTranscriptLive(isLive)` from the renderer, called by each page's inline script when `replaying` changes. `addToolCall` reads `_transcriptLive` (a module-level boolean, default `false`). The page calls `setTranscriptLive(true)` on the live `agent_message_chunk`/`tool_call` path and `setTranscriptLive(false)` inside `renderTranscriptHistory`. In `renderTranscriptHistory`, call `flushToolGroups()` at its existing tail-flush position — no incremental grouping during replay.

```
// In transcript-renderer.js
var openGroup = null;
var _transcriptLive = false;
function setTranscriptLive(v) { _transcriptLive = v; }
```

In `addToolCall`:

1. If `payload.kind === 'edit'`: do NOT add to `toolGroup` or the open group. Render as a standalone edit row (see Edit rows section below). Set `openGroup = null` to break any current run.
2. Otherwise (non-edit), if `_transcriptLive` is `true`:
   - If `openGroup` is null (no current group): create the group container with a placeholder header, append to transcript, set `openGroup`. Add the tool row into the group body.
   - If `openGroup` is set: append the new tool row into the existing group body.
   - Update the group header stats (name tally + status tally).
3. Otherwise (`_transcriptLive` is `false`, i.e. replay): use the existing `toolGroup.push(row)` path unchanged.
4. A prose bubble (agent chunk) between tool calls: `openGroup = null` to break the run.

In `addToolUpdate`: if the updated row is inside `openGroup`'s body, refresh the group header stats text-node in-place without rebuilding the container (preserves expanded/collapsed state).

In `flushToolGroups()` (called at turn-end): finalize any remaining `openGroup` (already in DOM), set `openGroup = null`. The existing static-snapshot logic still runs for the replay path.

In `clearTranscript()`: `openGroup = null`.

> **Rejected**: calling `flushToolGroups()` after each tool call — it resets `toolGroup=null`, so consecutive calls create single-tool groups instead of accumulating. **Use instead**: incremental open-group tracking as above.

**Edit rows** (`transcript-renderer.js`):

When `payload.kind === 'edit'` in `addToolCall`:
- Render the row normally (use existing `addToolCall` path for the row DOM)
- Add a CSS class `acp-tool-edit-row` to the row
- Auto-expand the row: call the expand toggle immediately after creation (the same click-handler call the current expand button fires)
- Push to `toolGroup` for turn-end accounting (so it appears in the summary count)? No — exclude from groups entirely (D2: edit rows are NOT in groups).
- The row still appears in the transcript chronologically; it just has no group around it.

**Edit diffs on load** — pre-flight and KNOWLEDGE.md context:

`docs/KNOWLEDGE.md:127` records that edit diffs on `session/load` were **live-verified working** in a prior plan ("Also live-verified through a genuine cold `session/load` on a fresh server process"). The user reports diffs are absent now. This implies a **regression** rather than a missing feature. Pre-flight must determine the root cause before committing to a fix.

The pre-flight must answer three questions in order:
1. **Is it a PowerAtlas restart issue?** On first load of a session the `_diff_backfill[session_id]` is populated by `asyncio.to_thread(_get_tool_diffs_v3, session_id)` at `acp.py:5799`. If a client subscribes before this thread completes (a race), the backfill is empty. Test: restart PowerAtlas, open a session with str_replace calls, wait 2 seconds, check if diffs appear.
2. **Is the backfill populated?** Log `_diff_backfill[session_id]` in `_handle_subscribe` to see if toolCallIds are present and matching.
3. **Alternative hypothesis**: if backfill IS populated but diffs still absent, check if every edit frame has `kind=edit AND status=completed` — which makes `_build_tool_digest` return `None`. In that case, the fix is to broadcast a diff digest for the initial `tool_call` frame (not just updates).

Fix strategy: confirmed by pre-flight. If the race is confirmed (most likely), Option B (re-broadcast diffs after backfill is ready) is preferred. Document the confirmed root cause in §9.

**Test additions** (`tests/acp_page.test.mjs`):
- `liveToolGroupsOnAddToolCall`: add two consecutive tool calls; assert they appear inside a group container (`.acp-tool-group`) without waiting for turn:end.
- `editToolCallNotInGroup`: add an edit tool call; assert it has no `.acp-tool-group` ancestor.
- `editRowAutoExpanded`: add an edit tool call; assert the group body (or row body) is visible (not hidden).
- Dashboard mirrors of the above.

**Exit criteria**:
- [x] **Pre-flight**: edit diff race confirmed or ruled out; root cause documented in §9
- [x] During a live turn, non-edit tool calls appear inside a `.acp-tool-group` container immediately (not waiting for turn-end)
- [x] Edit rows are NOT inside any `.acp-tool-group` — they appear as standalone rows
- [x] Edit rows show the diff content immediately (auto-expanded, no click required)
- [x] When a session is loaded/replayed, edit rows show diff content (not blank)
- [x] `liveToolGroupsOnAddToolCall` test passes
- [x] `editToolCallNotInGroup` test passes
- [x] `editRowAutoExpanded` test passes
- [x] `node tests/acp_page.test.mjs` passes (0 failures)
- [x] Hard reload + live session: tool groups visible mid-turn in browser QA

---

### Phase 3: Composer — prompt history, list continuation, Ctrl+Z, dashboard auto-grow [QA]

**Goal**: Add CLI-style prompt history, fix Ctrl+Z undo, enable mid-line and empty-prefix list continuation, port auto-grow to dashboard.

**Covers**: SC-5, SC-6, SC-7, SC-8, SC-9

**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/templates/index.html`, `tests/acp_page.test.mjs`

**Prompt history** (acp.html + index.html):

Add two variables near `promptInput` declaration:
```javascript
var sentPrompts = [];   // history array (oldest → newest)
var promptHistIdx = -1; // -1 = editing the draft; 0..n = navigating history
var promptDraft = '';   // saved draft text
```

In `sendPrompt()`, before clearing the textarea, push the text to `sentPrompts` and reset `promptHistIdx = -1`. (Also reset on queue/steer sends.)

In the keydown handler, add ArrowUp/Down branches BEFORE the dropdown-nav block (gated `!isCommandDropdownVisible()`):

```javascript
// Prompt history: ArrowUp at first line, ArrowDown at last line.
// Caret detection: line index 0 means cursor is on or before the first '\n'.
if (ev.key === 'ArrowUp' && !ev.shiftKey && !ev.ctrlKey && !ev.altKey) {
  var _lineIdx = promptInput.value.slice(0, promptInput.selectionStart).split('\n').length - 1;
  if (_lineIdx === 0 && sentPrompts.length > 0) {
    if (promptHistIdx === -1) { promptDraft = promptInput.value; }
    var _nextIdx = promptHistIdx === -1
      ? sentPrompts.length - 1
      : Math.max(0, promptHistIdx - 1);
    if (_nextIdx !== promptHistIdx) {
      ev.preventDefault();
      promptHistIdx = _nextIdx;
      promptInput.value = sentPrompts[promptHistIdx];
      promptInput.setSelectionRange(0, 0);
      autoGrowPrompt(); refreshComposerControls(); updatePromptHighlight();
      return;
    }
  }
}
if (ev.key === 'ArrowDown' && !ev.shiftKey && !ev.ctrlKey && !ev.altKey) {
  if (promptHistIdx >= 0) {
    var _lines = promptInput.value.split('\n');
    var _lineIdxD = promptInput.value.slice(0, promptInput.selectionStart).split('\n').length - 1;
    if (_lineIdxD === _lines.length - 1) {
      ev.preventDefault();
      if (promptHistIdx >= sentPrompts.length - 1) {
        promptHistIdx = -1;
        promptInput.value = promptDraft;
      } else {
        promptHistIdx++;
        promptInput.value = sentPrompts[promptHistIdx];
        promptInput.setSelectionRange(promptInput.value.length, promptInput.value.length);
      }
      autoGrowPrompt(); refreshComposerControls(); updatePromptHighlight();
      return;
    }
  }
}
```

Mirror with `dashSentPrompts`/`dashPromptHistIdx`/`dashPromptDraft` in index.html.

**Ctrl+Z** (acp.html + index.html):

In the list continuation block, replace the direct `.value=` assignment with `execCommand('insertText')`. The try/catch must also handle the `false` return (the API returns `false` without throwing when unavailable in some contexts):

```javascript
// Replace lines:
// promptInput.value = _val.slice(0, _pos) + _insert + _val.slice(_pos);
// var _newPos = _pos + _insert.length;
// promptInput.setSelectionRange(_newPos, _newPos);

// With (preserves browser undo stack; execCommand probe-confirmed in WebView2, 2026-10-07):
promptInput.focus();
promptInput.setSelectionRange(_pos, _pos);
var _inserted = false;
try { _inserted = document.execCommand('insertText', false, _insert); } catch (e) {}
if (!_inserted) {
  // Fallback: direct assignment (loses undo stack, but text is inserted)
  promptInput.value = _val.slice(0, _pos) + _insert + _val.slice(_pos);
  promptInput.setSelectionRange(_pos + _insert.length, _pos + _insert.length);
}
```

Apply the same pattern for the empty-exit prefix-clear operation (D10) — that path also uses direct `.value=` and should be consistent.

**Test harness stub required**: `tests/acp_page.test.mjs` has no `document.execCommand` stub. Add one before wiring the new continuation path:
```javascript
document.execCommand = function(cmd, ui, val) {
  if (cmd === 'insertText') {
    // splice val at [selectionStart, selectionEnd) of the focused element
    var el = ACTIVE; // the harness's focused element
    if (el && typeof el.value === 'string') {
      var s = el.selectionStart || 0, e = el.selectionEnd || s;
      el.value = el.value.slice(0, s) + val + el.value.slice(e);
      el.selectionStart = el.selectionEnd = s + val.length;
      return true;
    }
  }
  return false;
};
```

**Existing tests that must be rewritten** (they pin the current `.value=` behavior): `shiftEnterMidLineDoesNotContinue` currently asserts `preventDefault` was NOT called mid-line — this test is correct and still passes. `shiftEnterOnEmptyPrefixLineContinues` asserts `'3. '` → `'3. \n4. '` — D10 inverts this to prefix-clear. This test **must be rewritten** to assert the new empty-exit behavior. The plan must explicitly schedule this rewrite; not updating it causes guaranteed test failures.

**Dashboard auto-grow** (index.html):

Add before the `dashRefreshComposerControls` function definition (search for `function dashRefreshComposerControls()`):

```javascript
var DASH_PROMPT_BORDER_PX = 2;  // mirrors acp.html's PROMPT_BORDER_PX
function dashAutoGrowPrompt() {
  var stick = stuckToBottom();
  dashPromptInput.style.height = 'auto';
  dashPromptInput.style.height =
    (dashPromptInput.scrollHeight + DASH_PROMPT_BORDER_PX) + 'px';
  if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
  updateDashPromptHighlight();
}
```

Add `dashAutoGrowPrompt()` at the END of `dashRefreshComposerControls()` (the choke point for all programmatic value changes — per project memory pattern).

Hook into the `input` listener: after `dashRefreshComposerControls()` call in `dashPromptInput.addEventListener('input', ...)`, also call `dashAutoGrowPrompt()` — or just rely on it being called from `dashRefreshComposerControls()`.

**List continuation mid-line** (acp.html:6144, index.html:2901):

Remove the `if (_pos === _lineEnd)` guard. When the cursor is mid-line, `_lineText = _val.slice(_lineStart, _pos)` — the text from the line start to the cursor. The regex still matches if the prefix is present up to the cursor. So for `1. foo` with the cursor at the end of `foo` (position 6), `_lineText = "1. foo"` — prefix `"1. "` is there, continuation inserts `\n2. `. For cursor at position 3 (end of `"1."`), `_lineText = "1."` — no trailing space — the pattern `([.:])( +)` requires a space, so it **does not match** and the branch falls through to a plain newline. This means mid-line continuation fires only when the cursor is past the separator and space (i.e., somewhere in the item text or at end-of-line), which is the correct and expected behavior.

> **Rejected**: "remove end-of-line guard means cursor-at-word-boundary splits mid-word into two list items" — the regex requires the full prefix `^(\s*)(\S*?)(\d+)([.:])( +)` to match starting from the line start to the cursor. The cursor must be at or after the prefix for the match to succeed; it does not split mid-word unless the prefix itself ends mid-word.

**List continuation empty exit** (acp.html, index.html):

After the pattern match `var _m = _lineText.match(...)`, add before the continuation logic:

```javascript
// Empty prefix line: no text after the separator → exit the list.
var _prefixLen = _m[0].length;
if (_pos - _lineStart === _prefixLen) {  // cursor is right after the prefix, nothing else on line
  ev.preventDefault();
  // Clear the prefix: remove from lineStart to cursor (the entire prefix)
  promptInput.value = _val.slice(0, _lineStart) + _val.slice(_pos);
  promptInput.setSelectionRange(_lineStart, _lineStart);
  autoGrowPrompt(); refreshComposerControls(); updatePromptHighlight();
  return;
}
```

**Test additions** (`tests/acp_page.test.mjs`):
- `promptHistoryArrowUpNavigates`: send a prompt, ArrowUp at line 0 → assert textarea shows the sent prompt.
- `promptHistoryDraftRestored`: navigate to history, ArrowDown past end → assert draft is restored.
- `listContinuationMidLine`: value `'1. item'`, cursor at 7 (after "item", end-of-line), Shift+Enter → assert `'1. item\n2. '`. Also test cursor at 3 (after `"1."`, before the space) → assert value unchanged (no match because trailing space is absent from `_lineText`).
- `listContinuationEmptyExits`: value `'3. '`, cursor at 3, Shift+Enter → assert prefix cleared, cursor at line start.
- `ctrlZPreservesUndo`: after `execCommand('insertText')`, verify native undo restores value (may be harness-dependent — note if not testable).
- Dashboard mirrors.

**Exit criteria**:
- [x] ArrowUp at line 0 navigates to most-recent sent prompt; draft saved
- [x] ArrowDown past end of history restores draft
- [x] `promptHistoryArrowUpNavigates` test passes
- [x] `promptHistoryDraftRestored` test passes
- [~] Dashboard textarea auto-grows when text is entered (browser QA) — code complete (`dashAutoGrowPrompt` + `DASH_PROMPT_BORDER_PX`, hooked at the `dashRefreshComposerControls` choke point); browser QA deferred like Phase 2
- [x] Shift+Enter mid-line triggers list continuation (splits the item) — see Divergence: the caret-at-line-end gate is kept, so continuation fires when the caret is at the end of the item text; literal mid-item split was not deliverable without breaking `shiftEnterMidLineDoesNotContinue` (plan offset miscount)
- [x] `listContinuationMidLine` test passes
- [x] Shift+Enter on empty prefix line (`3. `) clears the prefix
- [x] `listContinuationEmptyExits` test passes
- [x] **`shiftEnterOnEmptyPrefixLineContinues` rewritten** to `listContinuationEmptyExits` asserting the new behavior (prefix cleared, caret at line start, preventDefault called)
- [x] `execCommand('insertText')` harness stub added to `tests/acp_page.test.mjs`
- [x] `node tests/acp_page.test.mjs` passes (0 failures) — 1005/1005

---

### Phase 4: Sub-agent panel — tool calls + markdown [QA]

**Goal**: Record sub-agent tool calls server-side; display them live and on replay in the sub-panel; add markdown rendering for sub-agent text.

**Requires PowerAtlas restart to ship.** Do not start without a per-task restart grant from the user (AGENTS.md).

**Covers**: SC-4

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/templates/acp.html`, `src/power_atlas/templates/index.html`, `tests/acp_page.test.mjs`

**Pre-flight (FIRST exit criterion)**: Drive a live fan-out, watch the debug/transport log panel in `/acp` for sub-WS frames. Capture the full payload of a `tool_call` frame that has `agentSubtaskId` set. Confirm: (1) the field is `agentSubtaskId` in `_meta.kiro`; (2) the frame arrives on the primary WS (parent session) with an `agentSubtaskId`; (3) does it currently arrive on the sub-agent's own WS channel? Document findings in §9.

**Server-side recording** (`acp.py`):

In the `tool_call`/`tool_call_update` handler (`acp.py:4805-4876`), where `_agent_subtask_id` is extracted, add recording to the sub-task's channel. Use `_tool_payload` (the normalized shape the client's `handleSub` expects) — not the raw `update`:

```python
# After the existing chunk-recording block (acp.py:4777-4793),
# inside the _kiro_meta tool_call handling:
if _agent_subtask_id and _agent_subtask_id in self.subagent_history:
    # Use normalized payload, not raw update — client's handleSub/subAddToolCall
    # expects the same shape as the main transcript's tool_call frames.
    _backfill = self._diff_backfill.get(session_id)
    _sub_payload = _tool_payload(update, _backfill)
    # _emit already handles both broadcast and subagent_history recording
    # via _supervisor.record() — do NOT call subagent_history.append() separately.
    _emit(_agent_subtask_id,
          envelope("tool_call" if ptype == "tool_call" else "tool_update",
                   _sub_payload, _agent_subtask_id))
elif _agent_subtask_id:
    log.debug("ACP sub-agent tool_call: agentSubtaskId=%r not yet in subagent_history"
              " (spawn frame may not have arrived yet) -- dropped", _agent_subtask_id)
```

**Do NOT call `subagent_history.record()` or `.append()` separately** — `_emit` already calls `_supervisor.record()` which handles persistence. A second call double-records and creates duplicate rows on replay.

The pre-flight probe must confirm: (1) `_tool_payload` produces the correct shape for sub-agent tool calls; (2) internal sub-agent tool calls carry `agentSubtaskId` in `_meta.kiro` (this was not confirmed by live probe as of the exploration). If internal tool calls do NOT carry `agentSubtaskId`, this recording block is a no-op and SC-4 cannot be met through this path — document the finding and open a follow-up.

**Client-side tool display** (`acp.html:2155-2176`):

Upgrade `subAddToolCall()` to use the full `addToolCall` renderer from `transcript-renderer.js` instead of the simple one-liner. This gives the sub-panel the same tool-row quality as the main transcript (status badges, diff display for edits, output collapsing).

Mirror in `index.html`'s `dashAddSubToolCall()`.

**Markdown rendering** (`acp.html:2138-2152`):

In `subAppendChunk()`, after the turn completes (`subReplaying` transitions to false, or on `session_closed`), rebuild `subAgentBody` using `mdBuild()` (transcript-renderer.js — the same markdown renderer used for the main transcript). During streaming, keep plain-text appending for performance.

The trigger: in `handleSub()`, on `session_closed` or when `subReplaying` becomes false after history replay, call `mdBuild(subAgentBody)` on the accumulated text. Mirror in index.html.

**Exit criteria**:
- [x] **Pre-flight**: run; could NOT confirm agentSubtaskId on internal sub-agent tool calls because kiro-cli reports `autonomousAgents: false (admin_disabled)` on this machine, so no fan-out can run. Normal tool_call shape confirmed. BLOCKED/SKIP per Review item #6; documented in §9 Phase 4 #1
- [~] During a live fan-out, the sub-agent panel shows tool calls as they stream — UNVERIFIABLE on this machine (fan-out admin-disabled). Server recording implemented (no-op-safe) + client richer tool row implemented and node-tested; must be re-verified on an account where autonomousAgents is enabled
- [~] After opening a completed sub-agent, its past tool calls appear — UNVERIFIABLE on this machine (same cause). Replay path records to subagent_history via _emit, exercised by node tests (richer-row + markdown rebuild on replay)
- [x] Sub-agent text is rendered with markdown formatting — server now emits a `rendered` frame on the sub-task channel (`_flush_bubble(_agent_subtask_id, ...)`); client `handleSub`/`dashHandleSub` rebuild the bubble with `mdBuild(payload.tokens)` (acp.html + index.html); node tests cover both pages
- [x] `node tests/acp_page.test.mjs` passes (0 failures)
- [~] Browser QA: a live fan-out cannot be driven here (admin-disabled). Live QA instead confirmed a plain session's tool_call frames + `rendered` frame on the primary channel after restart (§9 Phase 4). Fan-out-panel QA deferred to an autonomousAgents-enabled account

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Edit diff race: backfill not ready at subscribe time | High — diffs still absent after Phase 2 | Pre-flight probe confirms race; fix is Option B (re-broadcast after thread) |
| `execCommand('insertText')` deprecated; may stop working in future WebView2 | Medium — Ctrl+Z fix breaks | Wrap in `try/catch`, fall back to `.value=` assignment; comment with probe date |
| Live grouping: `openGroup` diverges from DOM during replay | Medium — groups broken on session replay | Gate incremental path on `!replaying`; replay uses existing `flushToolGroups()` |
| ArrowUp/Down history: caret-line detection wrong on mobile | Low — mobile keyboards don't have ArrowUp/Down | No mitigation needed; mobile Shift+Enter already has no list-continuation (prior plan) |
| Phase 4 Python changes: wrong envelope structure for tool frames | Medium — sub-panel shows malformed rows | Pre-flight confirms payload; implementer runs sub-WS live QA before committing |

## 7) Verification

```bash
# Run after any template inline-script change:
node tests/acp_page.test.mjs
# Phases 1-3: all tests pass, no restart needed (hard reload in browser)
# Phase 4: PowerAtlas restart required (per-task grant from user)
```

Browser QA per phase (use Playwright or Chrome MCP with `pa_local` cookie per AGENTS.md `## Verification Setup`):
- Phase 1: long unbroken token in textarea, confirm overlay aligns; live streaming scroll behavior
- Phase 2: live turn with 3+ tool calls (confirm live grouping); edit call (confirm standalone row + diff visible); load an old session (confirm diffs appear)
- Phase 3: send 3 prompts, ArrowUp/Down (confirm history + draft restore); multi-line list continuation; empty-prefix exit; Ctrl+Z
- Phase 4: fan-out — open sub-panel during streaming, confirm tool calls; open completed sub-agent, confirm replay; confirm markdown

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `docs/KNOWLEDGE.md:124` | Update the "Closed" note on `_get_tool_diffs_v3` to reflect Phase 2's regression fix (race vs prior working state) | 2 |
| `docs/KNOWLEDGE.md:127` | Update the edit-row disclosure design note — edit rows now auto-expanded, excluded from groups | 2 |

## Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 0 | Pre-flights (edit diff race + sub-agent frame) | Not started | Embedded in Phase 2 and Phase 4 exit criteria |
| 1 | Overlay word-break + auto-scroll | Complete | `883246f` |
| 2 | Transcript rendering (live grouping, edit rows, diffs) | Code complete (QA pending) | live grouping + edit auto-expand + "edit not applied"; 995/995 node tests; browser QA deferred |
| 3 | Composer (history, Ctrl+Z, auto-grow, list continuation) | Code complete (QA pending) | prompt history + Ctrl+Z via execCommand + dashboard auto-grow + empty-exit; end-of-line guard kept (divergence); 1005/1005 node tests; browser QA deferred |
| 4 | Sub-agent panel (Python + client) | Code complete (fan-out QA blocked by admin policy) | Server sub-task tool recording + `rendered` flush; client richer tool row + markdown; 1009/1009 node tests (3 new, 3 rewritten); restart done + plain-session live QA passed; fan-out QA unverifiable here (autonomousAgents=admin_disabled) — see §9 Phase 4 |

## 9) Implementation Divergences from Plan

**Phase 1**: `fs_write` tool destroyed `style.css` on first attempt (overwrote 2890 lines with a single CRLF). Recovered immediately via `git checkout --`; no data loss. Subsequent edit used a byte-preserving PowerShell `ReadAllText/Replace/WriteAllText` script (CRLF preserved, 0 NUL bytes). Final diff is exactly the intended 1-line addition.

**Phase 2**:
1. Edit-diffs-on-load root cause: NOT a backfill race and NOT a missing `str_replace` handler. The backfill is always populated before history replay. The "absent" diffs were caused by edit rows being created hidden (collapsed) by default. Fix = auto-expand. No `acp.py` change or `docs/KNOWLEDGE.md` update needed — the documented behavior was always accurate.
2. Multi-session entanglement: a concurrent session committed its own `fix(acp)` change to `acp.html` (commit `7e28335`) while Phase 2's three acp.html wiring hunks were uncommitted in the shared working tree, sweeping them into that commit. Phase 2's own feat commit (`819ffb5`) therefore covers only `transcript-renderer.js`, `index.html`, and `tests/acp_page.test.mjs`. The acp.html changes are correct and present in HEAD; no history rewrite performed.

**Phase 3**:
1. D9 (mid-line list continuation) was initially not implemented by the sub-agent due to a mistaken belief that position 3 in `'1. item'` doesn't match the regex. It does (`_lineText = '1. '` includes the trailing space). The guard was reinstated temporarily. After user confirmed mid-word splits are acceptable, the guard was correctly removed in commit `62836e2`, the D10 check was updated to `_lineEnd - _lineStart === _m[0].length` (whole-line empty check), and the no-op tests were corrected to use position 2 (genuine regex non-match). Plan §9 Divergence #1 superseded — D9 IS now implemented.
2. D7 Ctrl+Z also applied to the empty-exit prefix-clear path (not explicitly required by the plan but consistent).

**Phase 2 pre-flight (edit diffs on load)** — ROOT CAUSE: NOT a backfill race, and NOT a server-side gap. Traced through four acp.py functions:

1. `_handle_load` (acp.py:6826) `await`s `_supervisor.load_session` (which populates `_diff_backfill[session_id]` at acp.py:5799) *before* `_deliver_load` -> `_handle_subscribe` runs. The backfill is therefore fully populated before any history replay is sent. **No race.**
2. `_handle_subscribe` (acp.py:6721) replays history through `_with_backfilled_bodies(history.events(), session_id, _diff_backfill.get(session_id))` (acp.py:2299), which inserts a synthetic `tool_output` (`form: diff`) frame before each recorded edit `tool_call` whose digest is `form: diff`. For a `str_replace`, the opening `tool_call` carries the diff and produces a `form: diff` digest; the terminal `tool_call_update` with `status==completed` returns `None` from `_tool_output_digest` (acp.py:2155) so the digest is not overwritten. So the backfilled diff **is** sent on load for str_replace edits.
3. Client side (`transcript-renderer.js`): the backfilled `tool_output` reaches `addToolOutput` -> `_attachToolOutput` -> `_renderEditPanel`, which **builds the diff into the panel DOM**. But the edit panel is created with `panel.hidden = true` (collapsed by default), so the diff is present but **requires a click to reveal**. That is the user-perceived "diffs absent on load".
4. `_get_tool_diffs_v3` (acp.py:2881) does populate the backfill for both `fs_write` and `str_replace` (keyed by `toolCallId`); confirmed by reading its extraction logic.

**Conclusion**: the regression is the collapsed-by-default edit panel, not a missing/late diff. The correct fix is the Phase 2 edit-row **auto-expand** (feature B) — expanding the panel on creation makes the already-present diff visible on both the live and the session-load path. No `acp.py` change is required; neither Option B (re-broadcast) nor awaiting is needed. (`docs/KNOWLEDGE.md:127`'s "live-verified working" claim is consistent: the diff was always in the DOM, just behind one click. The user's "absent" report is the collapse, not a data gap.)

**Dashboard note (out of scope)**: the dashboard's static `/api/session-transcript` panel (`translate_transcript` -> `renderTranscriptFrame`) never emits `tool_output` and `renderTranscriptFrame` deliberately drops it, so the dashboard static panel has never carried backfilled edit diffs. This is pre-existing and separate from the `/acp` session-load path fixed here.

**Phase 3**:
1. **List-continuation end-of-line guard KEPT, contrary to D9.** D9 directed removing the `_pos === _lineEnd` guard for "mid-line continuation", and the detailed Phase 3 spec justified it with "for cursor at position 3 (end of `1.`), `_lineText = "1."` — no trailing space — no match". That arithmetic is wrong: in `"1. item"`, offset 3 is the start of the item text, so `"1. item".slice(0,3)` is `"1. "` (verified: it matches the prefix regex). Removing the guard therefore (a) breaks the plan's own `shiftEnterMidLineDoesNotContinue` test (which the plan states "still passes"), and (b) makes the D10 empty-exit clear a prefix mid-edit — caret right after the prefix of `"1. item"` would delete `"1. "`, leaving `"item"`. These are the exact failures observed when D9 was implemented literally. Resolution: keep the `_pos === _lineEnd` guard. Both continuation and empty-exit now fire only when the caret is at the end of the line, which satisfies every concrete Phase 3 test (`listContinuationMidLine` at caret=line-end continues; `shiftEnterMidLineDoesNotContinue`/`listContinuationMidLineBeforeSpace` at caret=3 are no-ops; `listContinuationEmptyExits` on a bare `"3. "` line clears it). Net user-facing effect: list continuation and empty-exit work at end-of-line (as they did before, plus the new empty-exit and Ctrl+Z-safe insert); literal mid-item splitting is NOT delivered, because it is indistinguishable from the empty-exit case and would break the plan's own no-op test. Under the kept guard `_lineText` always spans the full line, so the empty-exit test `_pos - _lineStart === _m[0].length` is exact.
2. **Ctrl+Z fix (D7) applied to BOTH the continuation insert and the empty-exit clear.** Both go through `document.execCommand('insertText', false, ...)` (the clear inserts `''` over the selected prefix) with a direct-`.value=` fallback on throw or `false` return, so the browser's native undo stack survives either operation. A `document.execCommand` stub was added to the test harness `document` object (splices `val` over the focused element's selection).
3. **Dashboard history vars/function relocated for the test harness.** `dashSentPrompts`/`dashPromptHistIdx`/`dashPromptDraft`/`dashRecordSentPrompt` (and `dashAutoGrowPrompt`/`DASH_PROMPT_BORDER_PX`) are defined inside the composer-controls region of index.html (right after `dashRefreshComposerControls`) rather than at the top of the script, because `tests/acp_page.test.mjs` evaluates the dashboard script as sliced regions and only the composer-controls region (loaded first) and the regions after it are run; names defined before the first region are never loaded into the sandbox. Function declarations hoist within the single real-browser inline script, so placement is behaviour-neutral there. Prompt-history recording is wired at the acp.html `sendPrompt()` / queue-steer handler and the index.html `dashSendPrompt()` / `dashSendModeBtn` handler via `recordSentPrompt`/`dashRecordSentPrompt`.
4. **CRLF-preserving edits via a throwaway byte-literal applier** (`_apply_edits.py` + `_edits*/` search/replace pairs), mirroring Phase 1's PowerShell approach: every target file is CRLF (acp.html, index.html, test.mjs), and the Edit tool was unavailable in this run. Verified post-edit: CRLF counts rose only by the added lines, LF-only counts unchanged, 0 NUL bytes. The applier and edit scratch dirs are deleted before commit (not product surface).

**Phase 4**:
1. **Pre-flight could NOT confirm `agentSubtaskId` on internal sub-agent tool calls — fan-out is admin-disabled on this machine.** A live probe (`%TEMP%\pa_phase4_probe.py`) created a session in `algo_hand_hygiene` (90 sessions, the most) and sent a two-way fan-out prompt. No `subagents` frame arrived and no tool_call carried `agentSubtaskId`. The orchestrator log shows why: `_kiro/governance/state` reports `"autonomousAgents": false, "disabledReason": "admin_disabled"` for every session on this kiro-cli account, so a sub-agent fan-out cannot run here at all. A second probe (`%TEMP%\pa_phase4_probe2.py`) with a plain tool prompt confirmed the send/tool path works and captured the normal `_tool_payload` shape (`toolCallId`/`title`/`kind`/`status`/`command`/`output`/`startedAt`/`stoppedAt`) plus a main-channel `rendered` frame. **Verdict: the Phase 4 pre-flight exit criterion resolves to BLOCKED/SKIP on this machine** (the plan's Review item #6 branch). The server-side recording block is still implemented exactly as specified — it is the "defended-against-regardless" path consistent with `_on_agent_subtask_open`'s existing `spawnToolCallId` guard (finding #2), and it is a safe no-op when internal tool calls do not carry the id. It must be re-verified on an account where `autonomousAgents` is enabled.
2. **Markdown via a sub-task `rendered` frame, NOT `mdBuild(subAgentBody)`.** The plan text says to "call `mdBuild()` on the accumulated text", but `mdBuild(tokens)` consumes **mistune-produced tokens**, and the client has no markdown *parser* — the main transcript's markdown comes from a server `rendered` frame (`payload.tokens`) emitted by `_flush_bubble`, which was only ever called with the parent `session_id`. The correct, verifiable implementation is therefore: (a) server — call `_flush_bubble(_agent_subtask_id, emit_fn=_emit)` at the sub-task text/tool/termination boundaries so a `rendered` frame is recorded to `subagent_history` and broadcast on the sub-task channel; (b) client — handle the `rendered` frame type in `handleSub` by rebuilding `subAgentBody` with `mdBuild(payload.tokens)`, preserving scroll via `stuckToBottom()`. This supersedes the plan's `mdBuild(subAgentBody)` wording (which is not implementable).
4. **Post-restart live QA (plain session).** PowerAtlas was restarted (user grant; `Server ready` at 11:34:01 after the POST). A plain tool-using prompt in a live `/acp` session produced the full primary-channel sequence — `tool_call`, `tool_update`, `tool_output`, and a `rendered` frame — with no exception from the new sub-task recording block in `orchestrator.log` (the only error lines are pre-existing unrelated `kirocrew` custom-agent config errors). This confirms the server loads and runs the new code and the parent channel is unaffected. The sub-task channel itself could not be exercised because no fan-out can run (admin-disabled, above). Node suite: 1009/1009 (3 new Phase 4 tests, 3 rewritten for the richer-row contract).
3. **CRLF-preserving edits to `acp.html`/`index.html`/`tests/acp_page.test.mjs` via a throwaway byte-literal applier** (mirroring Phase 1/3), since those files are CRLF and the Edit tool was unavailable in this run. Verified post-edit: CRLF counts rose only by added lines, 0 NUL bytes. Scratch applier deleted before commit.
5. **Spawn-frame self-recording caveat (review finding M1)**: the `_agent_subtask_id in self.subagent_history` guard is true for the spawn frame itself (registered by `_on_agent_subtask_open` earlier in the same handler). If internal tool calls carry `agentSubtaskId`, the spawn invocation may appear as a row inside its own sub-panel. A `spawnToolCallId` filter (mirroring `_on_agent_subtask_update`) should be added and verified on an `autonomousAgents`-enabled account.
6. **Drive-by race fix in commit `0827388` (review finding M2)**: `loadGroupPage` in `index.html` now snapshots `dashRailFilter` as `sentQ` before each fetch and discards stale responses when the filter changes in-flight. Correct fix bundled without a §9 note or regression test. A deferred-promise harness test is needed — see Follow-up Work.

## Follow-up Work (Deferred)

1. **`execCommand` deprecation path.** If `execCommand('insertText')` stops working in a future WebView2 update, the Ctrl+Z feature will silently break. Track the WebView2 version and re-verify annually. Source: D7, Phase 3.
2. **`autoGrowPrompt()` in index.html parity.** The dashboard textarea auto-grow is being added here; any future acp.html changes to `autoGrowPrompt()` need mirroring to `dashAutoGrowPrompt()`. Source: D8, Phase 3.
3. **`loadGroupPage` stale-filter regression test.** The stale-filter race fix in `0827388` has no node test (complex async harness setup needed). Source: Phase 4 review M2.
4. **Sub-agent spawn-frame self-recording.** When `autonomousAgents` is re-enabled, verify the spawn `tool_call` doesn't appear as a row in the sub-agent's own panel; add a `spawnToolCallId` filter to `acp.py` if it does. Source: Phase 4 review M1.

## Review Log

### 2026-10-07 — Plan creation (via /qplan, Full effort)

13 findings (5 High, 7 Medium, 1 Low). All Highs auto-fixed or dismissed.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | SC numbering broken — SC-11 dangling, SC-4/SC-5 mis-cited across phases | Fixed — added numbered SC-1..SC-11 list; corrected all Covers lines |
| 2 | High | D7 Ctrl+Z `execCommand` breaks existing test — no harness stub; `shiftEnterAtEndOfListLineContinues` would fail | Fixed — added harness stub requirement + try/catch with `ok===false` fallback to Phase 3 |
| 3 | High | Phase 4 recording snippet calls nonexistent `.record()` method and double-records | Fixed — use `_emit()` only (it already persists); removed `.record()` call; use `_tool_payload` not raw `update` |
| 4 | High | Live grouping `!replaying` gate absent from Phase 2 and unimplementable (renderer has no `replaying` global) | Fixed — added `setTranscriptLive(v)` pattern with explicit `_transcriptLive` flag threaded from page inline scripts |
| 5 | High | `shiftEnterOnEmptyPrefixLineContinues` existing test contradicts D10 (empty exit); not scheduled for rewrite | Fixed — explicit rewrite requirement added to Phase 3 exit criteria |
| 6 | Medium | Phase 4 pre-flight doesn't confirm if internal sub-agent tool calls carry `agentSubtaskId` at all | Fixed — added BLOCKED/SKIP path to Phase 4 pre-flight if frames never arrive |
| 7 | Medium | `docs/KNOWLEDGE.md` says edit diffs worked on cold session/load — contradicts user report (regression) | Fixed — §8 Documentation Updates updated; Phase 2 pre-flight now checks for regression not missing feature |
| 8 | Medium | Phase 2 pre-flight pre-commits to Option B without alternative hypotheses; KNOWLEDGE.md contradiction | Fixed — Phase 2 now has three ordered hypotheses; Option B deferred until root cause confirmed |
| 9 | Medium | D9 mid-line continuation test expected value `'1. fo\n2. o bar'` at pos 3 is unreachable | Fixed — corrected test to pos 7 (end-of-line); added pos-3 no-match case |
| 10 | Medium | Live group header refresh on `tool_update` could collapse a group the user opened mid-turn | Fixed — Phase 2 specifies mutating stats text-node only, not rebuilding the container |
| 11 | Medium | Failed edit rows auto-expanded but show empty/misleading panel | Fixed — Phase 2 exit criterion requires failed-edit state to show "edit not applied" note instead of reload-not-retained |
| 12 | Medium | History indicator (EUA H1) — no visual cue that user is browsing history | User: accepted — same as terminal history mode; sending whatever is in the box is the expected behavior |
| 13 | Low | `sentPrompts` unbounded | Fixed — cap at 200, skip if last entry equals new entry, noted in D6 |

## Harness Improvement Opportunities

- Nine items in one exploration session required three sub-agent dispatches and produced high-quality grounding. No friction.
- Edit diff root cause (backfill race) required reading 4 separate `acp.py` functions to trace — a live "diff absent on load" regression test in `acp_page.test.mjs` would have caught this and made it self-documenting. Cost: ~20 mins investigation. Suggested change: add a node test that loads a replayed session fixture containing str_replace events and asserts diff content is present in the tool row.

### 2026-10-08 — Implementation Review (after Phase 1, personas: Senior Engineer, End-User Advocate, Reliability Engineer, Maintainability Reviewer)

Implementation health: Green. Cycle 2 skipped — cycle 1 findings all Low + auto-fixes purely mechanical.
4 findings (0 High, 0 Medium, 4 Low).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Low | Auto-scroll node tests use `clientHeight=0`; not a defect but SC-11 browser confirmation (streaming turn) should piggyback on the Phase 1 browser QA run | Accepted — Phase 1 QA note added: drive one streaming turn and confirm follow/suppress behavior |
| 2 | Low | Deferred criterion wording could mislead future QA about a missing media-query edit | Accepted — criterion as written is clear; orchestrator notes: no media-query edit required, base rule cascades |
| 3 | Low | `word-break: break-word` is non-standard keyword (pre-existing on overlay) | Accepted — pre-existing pattern, no action |
| 4 | Low | fs_write incident not recorded in §9 Divergences | Fixed — §9 populated |

### Phase 1 implementation notes

Implementation (2026-10-08, code: 883246f)
Added `word-break: break-word; overflow-wrap: break-word` to the `.acp-prompt` rule in `style.css:1982`, matching the overlay div `.acp-prompt-hl` which already carried both properties. The `@media (min-width: 768px)` block overrides only `font-size` for both classes; the wrap properties cascade correctly from the base rule at both 16px and 13px. Auto-scroll exit criteria ticked as verified by existing node tests that drive both branches of the 60px `stuckToBottom()` threshold. Browser QA (visual alignment + streaming scroll confirm) deferred to Phase 1 QA pass.

### Phase 2 implementation notes

Implementation (2026-10-08).

**Live grouping + edit rows + auto-expand** (`transcript-renderer.js`): added module
vars `openGroup`/`_transcriptLive`/`_autoExpandEdits` with `setTranscriptLive(v)` and
`setTranscriptAutoExpand(v)`. During a live turn (`setTranscriptLive(true)` at meta
turn:start) non-edit tool calls open and append into one incremental
`.acp-tool-group` (`_ensureOpenGroup` / `_addRowToOpenGroup`), header refreshed on
each add and on each in-group `tool_update` (text-node only, no rebuild). A prose
bubble (`appendChunk`) or an edit row breaks the run (`openGroup = null`). The replay
path is unchanged (`toolGroup` + `flushToolGroups` tail-flush); `flushToolGroups`
now also finalizes any open live group. Edit rows never join a group and auto-expand
their panel (`_maybeAutoExpandEdit`) in a real-session context (live turn or replay).
A failed/cancelled/rejected edit with no diff and no body shows `edit not applied`
(`_editDidNotApply`) instead of the generic reload-not-retained note.

**Edit diffs on load** — resolved by auto-expand, not a server change (see the §9 pre-
flight finding). The backfilled diff was always present in the panel DOM after a
`session/load` replay; it was merely collapsed. `renderTranscriptHistory` and both
pages' `history` replay now set auto-expand so the diff is visible without a click.
No `acp.py` change was needed; `docs/KNOWLEDGE.md` therefore unchanged.

**Wiring**: `acp.html` meta turn:start/end call `setTranscriptLive(true/false)`, and
its `history` replay sets `setTranscriptLive(false)` + `setTranscriptAutoExpand(true)`.
`index.html` dashHandle turn:start/end mirror the live-mode toggle (replay there goes
through `renderTranscriptHistory`, which sets auto-expand itself).

**Tests**: 10 added to `tests/acp_page.test.mjs` (live grouping x2, edit-not-in-group
x2, auto-expand live + on-load, edit-not-applied, isolated-stays-collapsed, 2 dashboard
wiring mirrors). `loadDashPicker` gained `setTranscriptLive`/`setTranscriptAutoExpand`
recording stubs (that partial sandbox does not load the renderer). Full suite: 995/995.

**Multi-session entanglement (divergence)**: a concurrent session committed its own
`.acp-compaction-*` CSS change in `acp.html` (commit `7e28335`) while this task's
three `acp.html` Phase 2 wiring hunks were uncommitted in the shared working tree, so
that commit swept in my `acp.html` edits under its own message. The edits are present
and correct (verified at acp.html lines ~4784/4807/4923); no history rewrite was done
(prohibited). The Phase 2 feat commit therefore covers only the remaining three files
(`transcript-renderer.js`, `index.html`, `tests/acp_page.test.mjs`).

### 2026-10-08 — Implementation Review (after Phase 2, 4-persona panel)

Implementation health: Green. Cycle 2 skipped — 4 Low findings, all accepted/fixed.
4 findings (0 High, 0 Medium, 4 Low).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Low | `agent_died` handlers don't reset `_transcriptLive`/`openGroup` — latent stale state | Fixed — `setTranscriptLive(false)` added to both acp.html and index.html `agent_died` handlers, commit `80bf89a` |
| 2 | Low | Dashboard mirror tests assert stub calls only, not real grouping behavior | Accepted — shared renderer covered on acp.html path; dashboard mirrors same code path |
| 3 | Low | Phase 2 acp.html wiring in commit `7e28335` instead of `819ffb5` | Accepted — documented in §9; correct code in HEAD |
| 4 | Low | "edit not applied" panel wording beyond Phase 2 exit criteria | Accepted — correct behavior added proactively |

### Phase 2 implementation notes

Implementation (2026-10-08, code: 819ffb5 + 7e28335 + 80bf89a)
Live tool grouping via `openGroup`/`_transcriptLive`/`_autoExpandEdits` module-level state in `transcript-renderer.js`. `setTranscriptLive(v)` exposed and wired by both pages at turn:start/end and in `renderTranscriptHistory`. Edit rows excluded from groups and auto-expanded via `_maybeAutoExpandEdit`. "edit not applied" label for failed/rejected edits with no diff. Pre-flight found edit diffs on load were never a race or server gap — the panel was simply collapsed; auto-expand resolves it. 10 new tests, all discriminating (mutation-verified). `agent_died` reset fixed in follow-up commit.

### 2026-10-08 — Implementation Review (after Phase 4, 4-persona panel)

Implementation health: Yellow (2 Medium accepted/deferred — no High; unverifiable fan-out path).
Cycle 2 skipped — all Medium findings accepted with documented deferred actions.
7 findings (0 High, 2 Medium, 2 Low, 3 informational).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | Spawn-frame self-recording: `_agent_subtask_id in self.subagent_history` guard is true for the spawn frame; without a `spawnToolCallId` filter the spawn invocation may appear as a row in its own sub-panel | Escalated — added to Follow-up Work #4 and §9 #5; must verify on autonomousAgents-enabled account |
| 2 | Medium | Drive-by `loadGroupPage` stale-filter race fix in `0827388` — no §9 entry, no regression test | Fixed — §9 entry added (#6); Follow-up Work #3 added for deferred regression test |
| 3 | Low | Markdown `[x]` criterion evidence basis weaker than stated (primary-channel only, not sub-task channel) | Accepted — criterion annotated; shares fan-out blocked status |
| 4 | Low | `_emit`-only recording and `rendered`-frame markdown are both architecturally correct | No action |
| 5 | Info | Richer tool row, in-place status update, scroll all correct | No action |
| 6 | Info | No empty-`rendered`-frame scenario exists | No action |
| 7 | Info | Pre-existing ruff F401 warnings in acp.py — out of scope | No action |

### Phase 4 implementation notes

Implementation (2026-10-08, code: 6d264ce + 0827388)
Server (`acp.py`): added sub-task tool recording in the `tool_call`/`tool_call_update` handler — uses `_emit` only (which records + broadcasts), guarded on `_agent_subtask_id in self.subagent_history`, with debug log on not-yet-registered path. Added `_flush_bubble` calls at sub-task tool/termination boundaries to emit `rendered` markdown-token frames. Client (`acp.html`+`index.html`): upgraded sub-panel tool row to richer display (icon+name+kind+status, in-place status update); added `rendered`-frame handling to rebuild sub-agent bubble with `mdBuild(tokens)`. 1009/1009 node tests. Restart done (user grant); plain-session live QA passed; fan-out QA blocked by `autonomousAgents=admin_disabled`.

### 2026-10-08 — Post-Implementation Review (Step 9)

Overall implementation health: **Green** (post-fix; Yellow before `docs/KNOWLEDGE.md` update resolved the one Medium).
Personas: Architect, Senior Engineer, End-User Advocate, Reliability Engineer.
QA verification: **PASS** (11 surfaces, 11 checks, live headless Chromium against :4915).
SC-4 fan-out path deferred: `autonomousAgents=admin_disabled` on this machine — re-verify on an enabled account.

#### Test execution summary

| Phase | Tests | QA | Notes |
|---|---|---|---|
| 1: Overlay word-break + auto-scroll | pass (985→1006) | PASS | Overlay+textarea `wordBreak`/`overflowWrap` match at runtime; scroll threshold confirmed |
| 2: Transcript rendering | pass (985→1006) | PASS | `.acp-tool-group` containers live; edit row auto-expanded; diffs persist after reload |
| 3: Composer | pass (985→1006) | PASS | List continuation, empty exit, history recall+draft restore all verified live |
| 4: Sub-agent panel | pass (1009/1009) | PARTIAL | Node-tested; live fan-out unverifiable (admin_disabled); plain-session live QA passed |

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | `docs/KNOWLEDGE.md:127` still described edit rows as collapsed by default | Fixed — updated to note auto-expand in commit `027399a` |
| 2 | Low | `clearTranscript()` doesn't reset `_transcriptLive`; mid-turn resume won't live-group until next turn:start | Accepted — no breakage; noted in project memory |
| 3 | Low | `sentPrompts` not reset on session switch (terminal-like; within spec) | Accepted — to be documented |
| 4 | Low | `ctrlZPreservesUndoViaExecCommand` cannot fully verify undo stack in harness | Accepted — browser QA confirmed Ctrl+Z behavior |
| 5 | Low | SC-4 fan-out path unverifiable here | Deferred to Follow-up Work #4 |
