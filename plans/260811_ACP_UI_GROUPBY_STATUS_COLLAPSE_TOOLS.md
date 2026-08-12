# ACP UI: Group-by-Status Rail Mode, Collapse Tool Calls, Group Consecutive Tool Calls

> **Date**: 2026-08-11
> **Status**: Exploring
> **Scope**: Three independent front-end changes to `acp.html` (and `style.css`): a new "Status" rail grouping mode, per-call command-body collapse, and turn-end grouping of consecutive tool calls.

---

## Intent

### Problem statement & desired outcomes

The ACP conversation pane and session rail have three usability gaps:

1. **No status-based rail grouping.** The rail can group sessions by workspace or by day, but not by their live status dot (working / waiting / errored / available). Users who want to see all active sessions at a glance must scan the full list.

2. **Tool calls are always fully expanded.** A turn with many tool calls fills the transcript with command bodies, making it hard to read the agent's reasoning and prose responses. There is no way to collapse them.

3. **Consecutive tool calls are not grouped.** A turn that runs 15 shell commands shows 15 separate rows. The visual noise obscures the overall shape of what the agent did.

Desired outcomes: a cleaner conversation pane where tool-call noise is compressed by default but still inspectable; a third rail mode that surfaces live sessions at the top.

### Success criteria

- SC1: A "Status" option appears in the rail's sliders settings menu alongside "Date" and "Project". Selecting it groups sessions into flat buckets in priority order: Working → Waiting → Errored → Available → Locked. Empty buckets are omitted. The choice persists across reloads (localStorage).
- SC2: Each tool call row that has a command body has a collapse toggle. By default the command body is hidden. Clicking the toggle reveals/hides it. Tool calls with no command body show only the head (no toggle). The status pill is always visible and always receives in-place updates regardless of collapse state.
- SC3: When a turn ends (`meta turn:end`), consecutive sequences of ≥2 completed tool calls are collapsed into a group row. The group is collapsed by default. The group header shows: "N tool calls (tool_name ×count, …) · status ×count, …" — count first, name tally in parentheses, status tally after a separator, empty categories omitted. Individual calls inside an expanded group start collapsed (command body hidden). A lone tool call (not adjacent to another) renders as an individual row, unchanged.
- SC4: `tests/acp_page.test.mjs` is updated with checks covering all three features; test suite stays green (currently 202 pass / 0 fail).
- SC5: No server-side changes. All changes are in `acp.html` (JS) and `style.css` (CSS).

### Scope boundaries & non-goals

**In scope:**
- `src/power_atlas/templates/acp.html` — JS changes for all three features
- `src/power_atlas/static/style.css` — CSS for collapse toggle chevron, group container styling, and status-mode rail bucket heads
- `tests/acp_page.test.mjs` — new test checks for all three features

**Out of scope:**
- Server-side changes (`acp.py`, `web.py`, `data*.py`)
- The main dashboard (`index.html`, `partials/`)
- Sub-agent panel tool calls (`subAddToolCall`, `subToolRows`) — separate surface, not in scope
- Per-session or per-workspace settings for collapse behavior
- Persisting collapse state across page reloads

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

**Primary file:** `src/power_atlas/templates/acp.html` (~4,900 lines). All three changes are front-end only.

**No-innerHTML rule** (`acp.html:~540`): Every node built with `createElement + textContent`. Every class name built from wire-derived values must be pre-narrowed through a closed-set `Object.create(null)` map before reaching any attribute sink. Applies to all new class names.

**Expand/collapse convention** (`acp.html:207–209` comment): One pattern throughout the page — `aria-expanded` on a `<button>` trigger, `hidden` on the controlled pane, CSS-drawn chevron keyed off `[aria-expanded="false"]`. No `<details>`/`<summary>`. Must be followed for both the tool-call toggle (SC2) and the group toggle (SC3).

**`toolRows` map** (`acp.html:~529`): `Object.create(null)` keyed `'t:' + toolCallId` → `{ status: <span.acp-tool-status>, body: <div.acp-msg-body> }`. DOM element references — survive being moved into a group container. `tool_update` frames rewrite `status.textContent` and may append `commandBlock` into `body`. Must remain valid after any DOM restructuring for SC3.

**`agentBody` lifecycle**: Set null by `addToolCall`, `meta turn:start`, `meta turn:end`, `clearTranscript`, `renderMarkdown`. Set to a `div.acp-msg-body` element by `appendChunk` when role=`'agent'`. A consecutive tool call sequence is any run of `tool_call`/`tool_update` frames with no intervening agent `chunk`.

**`clearTranscript()`** (`acp.html:2069`): Resets `toolRows`, `agentBody`, `thinkingRow`. New `toolGroup` variable (SC3) must also be reset here.

**`RAIL_STATUS`** (`acp.html:598`): Closed set `{working, waiting, errored}`. `'working'` is the fallback. **`session.status` is `""` for non-held sessions** (`web.py:1946`) — by documented server contract. Status-mode bucketing (SC1) must derive from `(availability, status)` pair: `""` + `available` → Available bucket; `""` + `locked` → Locked bucket; held sessions bucket by `railRowStatus(session.status)`.

**`railSetMode` normalization** (`acp.html:2659`): `var next = mode === 'date' ? 'date' : 'project'` — currently swallows any third value. Must be extended to admit `'status'`. The localStorage initialization (`acp.html:2712`) must also recognize `'status'` or the persisted value falls through to `'project'`.

**`railIndex()`** (`acp.html:2747`): Builds a session-id → `{session, group, list, at}` map over both `railGroups` and `railFlat`. A status mode that uses the flat store (`railFlat`) is already covered by the existing `railFlat` arm of `railIndex()`. The poll/delete paths work for free.

**`railSettingsRender()`** (`acp.html:3383`): Populates the settings menu from a `modes` array: `[['date', 'Date'], ['project', 'Project']]`. Adding `['status', 'Status']` is the only change needed to surface the new mode in the menu.

**Settings menu structure** (`acp.html:116–132`): `role="menu"` + `menuitemradio` with `aria-checked`. Pattern is fully established.

**Tool call DOM structure** (built by `addToolCall`, `acp.html:1452`):
```
div.acp-msg.acp-msg-tool
  span.acp-msg-role            ← "tool"
  div.acp-msg-body
    div.acp-tool-head          ← name + kind badge + status pill
    [div (wrapper)]            ← only when payload.command present
      div.acp-tool-cmd
      [div.acp-tool-more]
```
The command wrapper (`commandBlock()`, `acp.html:1507`) is a plain `div` with no class on the outer wrapper. SC2's toggle hides/shows this wrapper.

**`stuckToBottom()`** (`acp.html:970`): Called before DOM mutations in `addToolCall` to decide whether to auto-scroll. New group-container insertion at turn:end must also check `stuckToBottom()` before restructuring the transcript.

**Test baseline:** `tests/acp_page.test.mjs` — 202 pass / 0 fail on HEAD. Must be updated per `AGENTS.md` Doc & Test Guidelines.

**CSS file:** Single `style.css` (1,313 lines). Relevant existing rules at lines 717–724: `.acp-msg-tool`, `.acp-tool-head`, `.acp-tool-name`, `.acp-tool-kind`, `.acp-tool-status`, `.acp-tool-cmd`, `.acp-tool-more`. No existing collapse CSS for tool rows.

**Step 1.5:** Dispatched the code-tracing trio — in-scope files are predominantly source code (`.html`, `.py`, `.mjs`).

### 5. Risks & mitigations

**R1 — `status: ""` for available/locked sessions breaks naïve status grouping.**
Non-held sessions always have `status: ""` (server contract, `web.py:1946`). A grouping key derived from `session.status` alone would put all available and locked sessions in one `""` bucket. Mitigation: derive bucket key from `(availability, status)` pair, mapping to five named buckets.

**R2 — `railSetMode` normalization silently swallows `'status'`.**
`acp.html:2659`: `var next = mode === 'date' ? 'date' : 'project'`. A `'status'` value falls through to `'project'`. Both the normalization and the localStorage initialization (`acp.html:2712`) must be updated. Same risk on the `railMode` variable initialization from localStorage.

**R3 — `toolRows` references after group restructuring.**
At turn:end, individual `acp-msg-tool` rows are moved into a group container. `toolRows[id].status` and `toolRows[id].body` are element references — they remain valid regardless of parent. However, `commandBlock` appended after the row is inside `body`, which is also a reference. No breakage, but must be verified in tests.

**R4 — `stuckToBottom()` and scroll during turn-end group restructuring.**
The turn-end grouping pass removes individual rows and replaces them with a group container. The scroll position check must happen before the restructuring, and scroll must be restored after if the user was at the bottom.

**R5 — Orphan tool calls in replay (evicted `turn:start` or `turn:end`).**
`meta turn:end` is in the ring buffer and replays normally. A turn evicted entirely leaves no tool calls in replay. The edge case — `turn:start` evicted but tool calls + `turn:end` survived — causes tool calls to replay without a preceding `turn:start`. The `toolGroup` accumulator starts null at `clearTranscript()` and at each `meta turn:start`, so orphan calls accumulate in a group that closes at the replayed `turn:end`. Correct behavior.

**R6 — `aria-live="polite"` on transcript + collapse.**
The transcript has `role="log" aria-live="polite"` (`acp.html:246`). Collapsing tool rows does not suppress live-region announcements of new content. New rows appended while others are collapsed still announce. Expected behavior, not a defect.

**R7 — Class names for status bucket heads must use closed-set map.**
`session.availability` and `session.status` come off the wire. If used in class names for bucket headers, they must be pre-narrowed. Pattern established by `RAIL_AVAILABILITY` and `RAIL_STATUS` maps.

### 6. Resolved decisions

- Q1: Preferred bucket layout for status mode? — A: ok with flat 5-bucket reco — Decision: Flat 5-bucket design: Working → Waiting → Errored → Available → Locked (empty buckets omitted).
- Q2: Server endpoint for status mode? — A: ok — Decision: Reuses `?mode=recent` flat endpoint, client-side grouping by `(availability, status)` pair. No server changes.
- Q3: Re-bucketing on poll — A: confirmed — Decision: 60s poll + `railRefreshStates()` + `renderRail()` naturally re-buckets sessions when status changes. No special handling needed.
- Q4: Collapse scope — A: ok — Decision: Collapse applies to command body only. Tool calls without a command show only the head (no toggle). Status pill always visible; receives in-place updates regardless of collapse state.
- Q5: Group header format — A: Option A + full list in parentheses like "read_file ×3, shell ×2" — Decision: "N tool calls (read_file ×3, shell ×2)" — count first, name tally in parentheses.
- Q6: Minimum group size — A: ok — Decision: Group wrapper only when ≥2 consecutive tool calls. Single calls render as individual rows unchanged.
- Q7: Individual calls inside expanded group — A: ok — Decision: Individual calls inside an expanded group start collapsed (command body hidden). Two-click path: expand group → expand individual call.
- Q8: Replay safety — A: ok confirmed — Decision: `toolGroup` reset in `clearTranscript()` and at `meta turn:start`. Grouping logic runs identically during replay. Orphan calls group at the next `turn:end` or end of history.
- Q9: Status mode label in settings menu — A: ok — Decision: Label is "Status".
- Q10: Live status updates to calls inside a collapsed group — A: Tool calls that are not completed are ungrouped/expanded until they complete — Decision: Grouping deferred to turn end. During the turn, all tool calls render as individual expanded rows.
- Q11: When do completed calls fold into groups? — A: ok — Decision: At `meta turn:end` (Option B — fold at turn end). Simpler, avoids mid-turn visual reshuffling.
- Q12: Which calls group at turn end? — A: ok, add status counts to header — Decision: All consecutive tool calls group at turn end regardless of final status. Group header includes status tally: "N tool calls (read_file ×3, shell ×2) · completed ×12, failed ×1".
- Q13: Single-line vs two-line group header — A: single line — Decision: Single line. Count, name tally in parentheses, status tally after separator (·). Empty status categories omitted.

### 7. Open items

- OI1 (execution-contingent): Exact character to use as separator between name tally and status tally in the group header (· vs — vs |). Decide during implementation based on visual fit.
- OI2 (execution-contingent): Whether the status-mode bucket heads use the `railHeadNode()` helper or a new render function — depends on whether the existing helper's `+` (create session) button should appear on status bucket heads (likely not, since a status bucket is not a workspace). Resolve during Phase 1 implementation.

### 8. Recommended approach

Three independent phases, each corresponding to one SC:

**Phase 1 — Status rail mode (SC1):**
- Extend `railSetMode()` to handle `'status'` as a valid third mode.
- Update the localStorage initialization and normalization to admit `'status'`.
- Add `['status', 'Status']` to the `modes` array in `railSettingsRender()`.
- Add `renderRailStatus()` — reads `railFlat`, groups sessions client-side by `(availability, status)` into up to 5 buckets (Working/Waiting/Errored/Available/Locked), renders using `railBucketNode`-style heads (without the `+` button). Empty buckets omitted.
- Status mode uses the same `railFlat` store as date mode. `railLoadFirstPage()` dispatches `loadFlatPage(1)` for both `'date'` and `'status'` modes.
- Add CSS for status bucket heads if needed (may reuse existing `.acp-rail-group` styling).
- Update `tests/acp_page.test.mjs` with status-mode checks.

**Phase 2 — Collapse tool call command body (SC2):**
- In `addToolCall()`, when `payload.command` is present, wrap the `commandBlock()` in a collapsible container. Add a toggle button to `.acp-tool-head` (after the status pill). Default: `hidden = true` on the command wrapper.
- Follow `aria-expanded` / CSS-chevron convention.
- When `tool_update` appends a `commandBlock` to an existing row (the `known` branch), the new block should also start hidden under the toggle.
- Add CSS for the toggle button and chevron in `.acp-tool-head`.
- Update `tests/acp_page.test.mjs`.

**Phase 3 — Group consecutive tool calls at turn end (SC3):**
- Add `var toolGroup = null` at module scope (reset in `clearTranscript()`, at `meta turn:start`).
- `addToolCall()` no longer groups directly — it appends individual rows as today and records the row in a pending list for the turn.
- At `meta turn:end`, scan the transcript's tool rows for the current turn. Group consecutive sequences of ≥2 into `div.acp-tool-group` containers. Compute name tally and status tally for the header. Insert the group container before the first row of the sequence, move the rows inside. Group is collapsed by default (`aria-expanded="false"`).
- Ensure `stuckToBottom()` check and scroll restoration wraps the restructuring.
- Update `tests/acp_page.test.mjs` with grouping checks (turn:end triggers group, header format, single call stays ungrouped, replay safety).

### 9. QA environment

- **Start command:** `.venv-PowerAtlas\Scripts\power-atlas` (Windows) from the checkout root.
- **URL:** `http://127.0.0.1:<port>/acp` (port shown in tray tooltip or log on startup).
- **Test harness:** `node tests/acp_page.test.mjs` — no server needed, runs against DOM stand-in.
- **Runtime verification:** Playwright against the live app at `/acp`. At least one live kiro-cli session needed to verify status dot and tool call rendering.
- **Test data:** The `fakeStore()` fixture in `tests/acp_page.test.mjs` generates synthetic sessions; extend it for status-bucketed sessions.

## Harness Improvement Opportunities

- The `meta turn:end` grouping pass (Phase 3) has no precedent in `tests/acp_page.test.mjs`'s frame-sequence simulation helpers — a `deliverTurn(calls)` helper that sends `turn:start`, N tool_call frames, and `turn:end` would reduce boilerplate in the new checks. Cost: noticed when planning Phase 3 test coverage. Suggested change: add a `deliverTurn(page, toolCalls)` helper to the test harness.
