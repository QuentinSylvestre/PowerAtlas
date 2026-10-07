# ACP Transcript and Composer Improvements

**Status**: Exploring
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

- During a live turn, tool calls are grouped dynamically as each call arrives (not flat one-by-one).
- Edit tool calls are never included in tool groups — they appear as standalone rows, auto-expanded to show the diff immediately, separate from the group header count.
- When a session is opened or replayed, edit rows show the actual diff content (not blank).
- The sub-agent panel shows tool calls (both live and replayed from history), and the agent's text is rendered as markdown.
- Pressing ArrowUp on the first line of the prompt textarea navigates to the previous sent prompt (draft saved and restorable via ArrowDown at end of history).
- Dashboard textarea auto-grows after list continuation.
- Shift+Enter mid-line triggers list continuation if the line matches the prefix pattern.
- Shift+Enter on an empty prefix line (`3. ` with nothing after) clears the prefix and leaves a plain newline.
- Ctrl+Z after a list continuation undoes the continuation cleanly.
- Overlay text aligns with the textarea caret at both font-size breakpoints (16px/13px).

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
