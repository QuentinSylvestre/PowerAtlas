# ACP UI: Group-by-Status Rail Mode, Collapse Tool Calls, Group Consecutive Tool Calls

> **Date**: 2026-08-11
> **Status**: In Progress
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Three independent front-end changes to `acp.html` and `style.css`: a new "Status" rail grouping mode, per-call command-body collapse, and turn-end grouping of consecutive tool calls.
> **Estimated effort**: 1-2 days

---

## Intent

### Problem statement & desired outcomes

The ACP conversation pane and session rail have three usability gaps:

1. **No status-based rail grouping.** The rail can group sessions by workspace or by day, but not by their live status dot (working / waiting / errored / available). Users who want to see all active sessions at a glance must scan the full list.

2. **Tool calls are always fully expanded.** A turn with many tool calls fills the transcript with command bodies, making it hard to read the agent's reasoning and prose responses. There is no way to collapse them.

3. **Consecutive tool calls are not grouped.** A turn that runs 15 shell commands shows 15 separate rows. The visual noise obscures the overall shape of what the agent did.

Desired outcomes: a cleaner conversation pane where tool-call noise is compressed by default but still inspectable; a third rail mode that surfaces live sessions at the top.

### Success criteria

- SC1: A "Status" option appears in the rail's sliders settings menu alongside "Date" and "Project". Selecting it groups sessions into flat buckets in priority order: Working -> Waiting -> Errored -> Available -> Locked. Empty buckets are omitted. The choice persists across reloads (localStorage).
- SC2: Each tool call row that has a command body has a collapse toggle. By default the command body is hidden. Clicking the toggle reveals/hides it. Tool calls with no command body show only the head (no toggle). The status pill is always visible and always receives in-place updates regardless of collapse state.
- SC3: When a turn ends (`meta turn:end`), consecutive sequences of >=2 tool calls are collapsed into a group row. The group is collapsed by default. The group header shows: "N tool calls (tool_name xCount, ...) · status xCount, ..." — count first, name tally in parentheses, status tally after a separator, empty categories omitted. Individual calls inside an expanded group start collapsed (command body hidden). A lone tool call renders as an individual row, unchanged.
- SC4: `tests/acp_page.test.mjs` is updated with checks covering all three features; test suite stays green (currently 202 pass / 0 fail).
- SC5: No server-side changes. All changes are in `acp.html` (JS), `style.css` (CSS), and `tests/acp_page.test.mjs`.

### Scope boundaries & non-goals

**In scope:** `src/power_atlas/templates/acp.html`, `src/power_atlas/static/style.css`, `tests/acp_page.test.mjs`.

**Out of scope:** server-side changes (`acp.py`, `web.py`, `data*.py`); main dashboard (`index.html`, `partials/`); sub-agent panel tool calls (`subAddToolCall`, `subToolRows`); per-session collapse settings; persisting collapse state across page reloads.

---

## 1) Current State

**`acp.html`** (~4,900 lines, `src/power_atlas/templates/acp.html`): single-file template, all ACP UI logic in an inline `<script>` block. No build step.

**Rail grouping** (`acp.html:2658`): `railSetMode(mode)` accepts `'date'` or `'project'` only. Normalization `var next = mode === 'date' ? 'date' : 'project'` silently drops any third value. Settings menu built by `railSettingsRender()` (`acp.html:3383`) from `[['date', 'Date'], ['project', 'Project']]`. Mode persists via `RAIL_MODE_KEY = 'pa_acp_group'`; localStorage init at `acp.html:2712` uses the same two-value normalization.

**Session status on the wire**: `session.status` is `""` for all non-held sessions (`web.py:1946`, server contract). Held sessions get `'working'` | `'waiting'` | `'errored'`. `RAIL_STATUS` (`acp.html:598`) is the closed-set map; `railRowStatus(value)` narrows with `'working'` as fallback. Both `?mode=recent` and grouped listings return `availability` + `status` per session.

**Tool call rendering** (`acp.html:1452`, `addToolCall`): each call produces a top-level `div.acp-msg.acp-msg-tool` with a `div.acp-tool-head` (name + optional kind badge + status pill) and optionally a `commandBlock()` wrapper containing `div.acp-tool-cmd`. No collapse, no grouping. `toolRows` (`acp.html:~529`) maps `'t:'+toolCallId` to `{status: <span>, body: <div>}` for in-place `tool_update` mutations.

**Turn boundaries**: `agentBody` is nulled by `addToolCall`, `meta turn:start`, `meta turn:end`, `clearTranscript`, and `renderMarkdown`. No DOM separator element inserted between turns. `meta turn:end` IS recorded in the ring buffer and replays through `handle()`.

**Expand/collapse convention** (`acp.html:207` comment): `aria-expanded` on `<button>`, `hidden` on controlled pane, CSS `::before` chevron driven by `[aria-expanded="false"]`. No `<details>`/`<summary>` anywhere.

**No-innerHTML rule**: every node via `createElement + textContent`. Wire-derived values in class names must be pre-narrowed through `Object.create(null)` closed-set maps.

**Test baseline**: `tests/acp_page.test.mjs` — 202 pass / 0 fail (verified 2026-08-11). AGENTS.md mandates updating this file when changing template inline scripts.

**CSS**: single `src/power_atlas/static/style.css` (1,313 lines). Tool call rules at lines 717-724. No existing collapse CSS for tool rows.


## 2) Goal

Add three independent front-end improvements to the ACP UI: a status-based rail grouping mode (flat 5-bucket view from `?mode=recent` data), per-call command-body collapse/expand, and turn-end grouping of consecutive tool calls into collapsible group rows with a count+tally header.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Status mode data source | Reuse `?mode=recent` flat endpoint; group client-side | New server endpoint | Server already returns all needed fields; zero server changes |
| Status bucket layout | Flat 5 buckets: Working -> Waiting -> Errored -> Available -> Locked (empty omitted) | Tiered "live first / available below" | Consistent with existing bucket-shape precedent; priority order surfaces active sessions naturally |
| Status bucket key derivation | Derived from `(availability, status)` pair | `status` field alone | `status` is `""` for all non-held sessions by server contract; pair-based derivation correctly maps `""` + `available` -> Available bucket, `""` + `locked` -> Locked bucket |
| Status bucket head `+` button | Omit (pass `null` opts to `railHeadNode`) | Show like workspace heads | A status bucket is not a workspace; create-session on a status bucket has no meaning |
| Collapse scope | Command body only (`commandBlock` wrapper) | Whole row | Tool calls without a command have nothing to collapse |
| Collapse default | Collapsed (hidden) | Expanded | Reduces transcript noise; status pill remains always visible |
| `tool_update` into collapsed row | In-place mutation via `toolRows` reference — works unchanged | Special handling | `hidden` on wrapper does not affect element reference validity |
| Group minimum size | >=2 consecutive tool calls | Always group (>=1) | A group of 1 reads as a UI artifact; single calls render as today |
| Group formation timing | At `meta turn:end` | Immediately on second call | Avoids mid-turn visual reshuffling; turn end is the "done" signal |
| Which calls group at turn:end | All consecutive calls regardless of final status | Only terminal-status calls | Turn ending is the terminal signal; filtering by status adds complexity with no gain |
| Group header format | `N tool calls (name xCount, ...) · status xCount, ...` single line | Two-line | Consistent with one-line tool row design; both tallies visible at a glance |
| Header separator character | `·` (U+00B7 middle dot) between name tally and status tally | `—`, `|` | Visually light; middle dot is conventional for this use |
| Individual calls inside expanded group | Start collapsed | Start expanded | Consistent "collapsed by default" posture throughout |
| `toolGroup` reset points | `clearTranscript()` and `meta turn:start` | Turn-end only | Ensures correct accumulation even when `turn:start` is evicted from replay buffer |
| Rail mode label | `"Status"` | `"Activity"`, `"Live status"` | Matches internal vocabulary (`RAIL_STATUS`, `session.status`) |
| `railSetMode` normalization | Extend ternary to admit `'status'` | Separate validation fn | Minimal change; consistent pattern |
| Non-adjacent tool calls in one turn | Split into separate groups per consecutive sub-run | One big group for the turn | Reflects actual agent behavior; prose between calls visually separates logical operations |

## 4) External Dependencies & Costs

### Required external changes

None. All changes are in `acp.html`, `style.css`, and `acp_page.test.mjs`. No server changes, no infrastructure, no new dependencies.

### Cost impact

None.


## 5) Implementation Phases

### Phase 1: Status rail grouping mode [QA]

**Goal**: Add a third "Status" option to the rail settings menu that groups sessions into flat status buckets (Working / Waiting / Errored / Available / Locked) using the existing `?mode=recent` flat endpoint and client-side bucketing.

**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/static/style.css`, `tests/acp_page.test.mjs`

**Covers**: SC1, SC5

**Changes to `acp.html`**:

1. **Extend `railSetMode` normalization** (`acp.html:2659`) — change:
   ```js
   var next = mode === 'date' ? 'date' : 'project';
   ```
   to:
   ```js
   var next = mode === 'date' ? 'date' : mode === 'status' ? 'status' : 'project';
   ```

2. **Update localStorage initialization** (`acp.html:2712`) — change:
   ```js
   var railMode = railStored(RAIL_MODE_KEY) === 'date' ? 'date' : 'project';
   ```
   to:
   ```js
   var _storedMode = railStored(RAIL_MODE_KEY);
   var railMode = _storedMode === 'date' ? 'date' : _storedMode === 'status' ? 'status' : 'project';
   ```

3. **Add `['status', 'Status']`** to the `modes` array in `railSettingsRender()` (`acp.html:3383`):
   ```js
   var modes = [['date', 'Date'], ['project', 'Project'], ['status', 'Status']];
   ```

4. **Extend `railLoadFirstPage()`**: the existing `if (railMode === 'date')` branch that calls `loadFlatPage(1)` becomes `if (railMode === 'date' || railMode === 'status')`.

5. **Extend `renderRail()`**: the existing two-way dispatch `railMode === 'date' ? renderRailDate() : renderRailProject()` becomes a three-way dispatch adding `renderRailStatus()` for `railMode === 'status'`.

6. **Extend `renderRail()`'s "Load more" footer**: the `if (railMode === 'date')` arm that uses `railFlatHasMore` / `'Load more sessions'` should also fire for `railMode === 'status'` (both use `railFlat`).

7. **Add closed-set `TOOL_STATUS_LABEL` map** (add near `RAIL_STATUS` constants — before `flushToolGroups`):
   ```js
   // Closed-set map for tool-call status strings that appear in the group header tally.
   // Wire values not in this map are silently omitted from the tally rather than
   // reaching visible UI as raw wire strings (no-raw-wire-value rule).
   var TOOL_STATUS_LABEL = Object.create(null);
   TOOL_STATUS_LABEL.started    = 'started';
   TOOL_STATUS_LABEL.completed  = 'completed';
   TOOL_STATUS_LABEL.failed     = 'failed';
   TOOL_STATUS_LABEL.denied     = 'denied';
   TOOL_STATUS_LABEL.approved   = 'approved';
   TOOL_STATUS_LABEL.update     = 'update';
   ```
   In `flushToolGroups`, replace direct `stEl.textContent` accumulation with narrowed lookup:
   ```js
   var stRaw = stEl ? stEl.textContent : '';
   var st = TOOL_STATUS_LABEL[stRaw]; // undefined for unknown values — omitted below
   if (st) { ... } // only add to tally when narrowed value exists
   ```

8. **Extract `_makeToolToggle(cmdWrap, toolTitle)` helper** to eliminate the duplicate toggle-creation code between the new-row path and the `known` branch. Both call sites pass the `cmdWrap` element and the tool title for the accessible label. Helper signature:
   ```js
   var STATUS_BUCKET_ORDER = ['working', 'waiting', 'errored', 'available', 'locked'];
   var STATUS_BUCKET_LABEL = Object.create(null);
   STATUS_BUCKET_LABEL.working  = 'Working';
   STATUS_BUCKET_LABEL.waiting  = 'Waiting';
   STATUS_BUCKET_LABEL.errored  = 'Errored';
   STATUS_BUCKET_LABEL.available = 'Available';
   STATUS_BUCKET_LABEL.locked   = 'Locked';

   function statusBucketKey(session) {
     var avail = railAvailability(session.availability);
     if (avail === 'held')   return railRowStatus(session.status); // working|waiting|errored
     if (avail === 'locked') return 'locked';
     return 'available';
   }
   ```
   Both `railAvailability` and `railRowStatus` are existing closed-set narrowing functions — no raw wire value ever reaches a class name or DOM key.

8. **Add `renderRailStatus()` function** (after `renderRailDate()`):
   ```js
   function renderRailStatus() {
     var byBucket = Object.create(null);
     for (var i = 0; i < railFlat.length; i++) {
       if (!railMatchesFlat(railFlat[i])) continue;
       var bk = statusBucketKey(railFlat[i]);
       if (!byBucket[bk]) byBucket[bk] = [];
       byBucket[bk].push(railFlat[i]);
     }
     var shown = 0;
     for (var b = 0; b < STATUS_BUCKET_ORDER.length; b++) {
       var key = STATUS_BUCKET_ORDER[b];
       var sessions = byBucket[key];
       if (!sessions || !sessions.length) continue;
       var wrap = document.createElement('div');
       wrap.className = 'acp-rail-group';
       wrap.dataset.statusBucket = key;  // 's:' prefix in railCollapsed key
       // railHeadNode(key, label, countText, opts) -- null opts omits the + create button
       wrap.appendChild(railHeadNode('s:' + key, STATUS_BUCKET_LABEL[key],
                                     String(sessions.length), null));
       if (!railCollapsed['s:' + key]) {
         for (var j = 0; j < sessions.length; j++) {
           wrap.appendChild(railRowNode(sessions[j], true));
         }
       }
       railGroupsEl.appendChild(wrap);
       shown += sessions.length;
     }
     return shown;
   }
   ```
   The `'s:'` prefix on `railCollapsed` keys avoids collisions with `'g:'` (workspace) and `'d:'` (day) keys. Status mode uses `railFlat` so `railIndex()` already covers the poll/delete paths.

9. **Audit `railSummary()`** (`acp.html:2462`): check whether the summary text branches on `railMode` and add a `'status'` arm matching `'date'` behavior if needed.

**Changes to `style.css`**: No new rules are strictly required — `STATUS_BUCKET_LABEL` values are plain text in the group head. Optionally add a small colored dot to status bucket heads using `[data-status-bucket="working"]` etc., reusing the existing `session-status status-working/waiting/errored` class vocabulary. Decide during implementation via visual review; mark as optional in exit criteria.

**Changes to `tests/acp_page.test.mjs`**:
- Test `railSetMode('status')` sets `railMode = 'status'` and persists to localStorage.
- Test that selecting "Status" in the settings menu dispatches a `?mode=recent` listing request (same as date mode).
- Test `renderRailStatus()` produces groups in Working -> Waiting -> Errored -> Available -> Locked order; empty buckets absent.
- Test `statusBucketKey`: `{availability:'held', status:'working'}` -> `'working'`; `{availability:'available', status:''}` -> `'available'`; `{availability:'locked', status:''}` -> `'locked'`.
- Test that `railCollapsed['s:working']` toggles collapse correctly via group head click.
- Test that the settings menu gains a third `menuitemradio` with `aria-checked` correctly reflecting the active mode.

**Additional Phase 1 code changes** (from review):
- **`railSummary()` `'status'` arm** (`acp.html:~2469`): add `'status'` arm matching `'date'` behavior — count `railFlat.length`, emit "N sessions loaded". Without it, status mode shows "N of M workspaces" (wrong; `railGroups` is always empty in status mode).
- **Empty-state filter text** (`acp.html:~3811`): change `(railMode === 'date' ? 'more sessions' : 'more workspaces')` to `(railMode !== 'project' ? 'more sessions' : 'more workspaces')`.
- **Empty-state node in `renderRailStatus()`**: when `shown === 0`, fall through to the existing empty-state rendering in `renderRail()` (returning 0 already triggers it there).

**Exit criteria**:
- [x] `railSetMode('status')` sets `railMode = 'status'`; localStorage persists `'status'`; reload restores it.
- [x] Selecting "Status" in settings menu dispatches `?mode=recent` request.
- [x] Sessions group into Working / Waiting / Errored / Available / Locked; empty buckets absent.
- [x] `statusBucketKey` derives bucket from `(availability, status)` pair — no raw wire value reaches a class name or DOM key.
- [x] `railCollapsed` with `'s:'` prefix correctly collapses/expands status buckets.
- [x] "Load more" footer reflects `railFlatHasMore` (same as date mode behavior).
- [x] `railSummary()` shows session count, not workspace count, under status mode.
- [x] Empty-state filter text reads "more sessions" (not "more workspaces") under status mode.
- [x] `tests/acp_page.test.mjs` updated with status-mode checks including `railSummary` text test; suite is green.
- [x] `README.md` updated — third rail mode description added to *Agent sessions* section (see §8).
- [x] `plans/ROADMAP.md` line 188 updated — "two groupings" -> "three groupings" (see §8).

**Implementation (2026-08-11, code: a3603ce + cce87a4 + 4b0a913)**
Phase 1 adds a third "Status" grouping mode to the `/acp` rail. The `railSetMode` normalization ternary and localStorage initialization were extended to admit `'status'` alongside `'date'` and `'project'`. `railLoadFirstPage` and `renderRail`'s load-more footer were extended to treat `'status'` identically to `'date'` (both use the flat `?mode=recent` endpoint). The three-way dispatch in `renderRail` routes to a new `renderRailStatus()` function that buckets sessions by `(availability, status)` pair into five priority-ordered buckets (Working → Waiting → Errored → Available → Locked), using the existing `railAvailability`/`railRowStatus` closed-set narrowing functions; the `'s:'` key prefix in `railCollapsed` avoids collisions with workspace (`'g:'`) and day (`'d:'`) keys. `TOOL_STATUS_LABEL` and `STATUS_BUCKET_ORDER`/`STATUS_BUCKET_LABEL` constants were added near `RAIL_STATUS`, and `statusBucketKey()` was added alongside `railRowStatus`. `railSummary()` and the empty-state filter ternary were both updated for the status arm. Post-review auto-fixes added: `railMore` click handler and `railRefresh()` tick-poll both extended for status mode (two High bugs from review); `renderRailStatus()` now applies `RAIL_SESSION_SIZE` (3) cap with "Show N more" per bucket; `railRestoreFocus()` gained a `want.status` branch. 7 new tests in cycle 1 (209 total), 4 more in cycle 2 (213), 1 more for `railRestoreFocus` fix (214 total). Final test count: 214 pass / 0 fail.


---

### Phase 2: Collapse tool call command body by default [QA]

**Goal**: Each tool call row with a command body gets a collapse toggle. Command body hidden by default. Tool calls with no command body are unchanged.

**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/static/style.css`, `tests/acp_page.test.mjs`

**Covers**: SC2, SC5

**Changes to `acp.html`** — `addToolCall()` new-row path (`acp.html:1452`):

When `payload.command` is present, add a toggle button to `.acp-tool-head` and wrap `commandBlock` in a hidden container:

```js
if (payload.command) {
  var toggle = document.createElement('button');
  toggle.className = 'acp-tool-toggle';
  toggle.type = 'button';
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-label', 'Show command detail');
  head.appendChild(toggle);

  var cmdWrap = commandBlock(payload);
  cmdWrap.hidden = true;  // collapsed by default

  toggle.addEventListener('click', function () {
    var open = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', open ? 'false' : 'true');
    toggle.setAttribute('aria-label', open ? 'Show command detail' : 'Hide command detail');
    cmdWrap.hidden = open;
  });

  body.appendChild(cmdWrap);
} // (no else — tool calls without command are unchanged)
```

**`tool_update` path** (the `known` branch, `acp.html:1457`): when `payload.command` is present and no `.acp-tool-cmd` exists in `known.body` yet, the new `commandBlock` must also start hidden with a toggle. Add the toggle to the head if not already present:
```js
if (payload.command && !known.body.querySelector('.acp-tool-cmd')) {
  var cmdWrap = commandBlock(payload);
  cmdWrap.hidden = true;
  if (!known.body.querySelector('.acp-tool-toggle')) {
    // head is known.body.querySelector('.acp-tool-head')
    var head = known.body.querySelector('.acp-tool-head');
    var toggle = document.createElement('button');
    toggle.className = 'acp-tool-toggle';
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-label', 'Show command detail');
    toggle.addEventListener('click', function () {
      var open = toggle.getAttribute('aria-expanded') === 'true';
      toggle.setAttribute('aria-expanded', open ? 'false' : 'true');
      toggle.setAttribute('aria-label', open ? 'Show command detail' : 'Hide command detail');
      cmdWrap.hidden = open;
    });
    head.appendChild(toggle);
  }
  known.body.appendChild(cmdWrap);
}
```

**`toolRows` unchanged**: `toolRows[id] = {status, body}` where `body` is `.acp-msg-body`. In-place `status.textContent` mutation via `toolRows` works regardless of `cmdWrap.hidden` — `hidden` is an attribute, not DOM exclusion; `body.querySelector('.acp-tool-cmd')` still finds the element.

**Extract `_makeToolToggle(cmdWrap, toolTitle)` helper**: the toggle-creation code (button, aria attributes, click handler) is identical for the new-row path and the `known` branch. Extract it into a named helper to avoid two separate maintenance targets:
```js
function _makeToolToggle(cmdWrap, toolTitle) {
  var toggle = document.createElement('button');
  toggle.className = 'acp-tool-toggle';
  toggle.type = 'button';
  toggle.setAttribute('aria-expanded', 'false');
  // Include tool name so 15 identical "Show command detail" labels become distinguishable
  toggle.setAttribute('aria-label', 'Show command detail \u2014 ' + (toolTitle || 'tool'));
  toggle.addEventListener('click', function () {
    var open = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', open ? 'false' : 'true');
    toggle.setAttribute('aria-label',
      (open ? 'Show' : 'Hide') + ' command detail \u2014 ' + (toolTitle || 'tool'));
    cmdWrap.hidden = open;
  });
  return toggle;
}
```
Both call sites: `head.appendChild(_makeToolToggle(cmdWrap, payload.title || payload.kind));`

**Changes to `style.css`**:
```css
.acp-tool-toggle {
  background: none;
  border: none;
  cursor: pointer;
  padding: 0 4px;
  color: var(--text-dim);
  font-size: 10px;
  line-height: 1;
}
.acp-tool-toggle::before {
  content: '\25B6';  /* solid right-pointing triangle */
  display: inline-block;
  transition: transform 0.15s;
}
.acp-tool-toggle[aria-expanded="true"]::before {
  transform: rotate(90deg);
}
```

**Changes to `tests/acp_page.test.mjs`**:
- Test: `tool_call` with `command` renders `.acp-tool-toggle` in head; `cmdWrap.hidden === true`.
- Test: clicking toggle sets `aria-expanded="true"` and `cmdWrap.hidden === false`.
- Test: second click collapses again (`aria-expanded="false"`, `hidden = true`).
- Test: `tool_call` without `command` has no `.acp-tool-toggle`; head unchanged.
- Test: `tool_update` that adds `command` to existing row appends toggle and starts collapsed.
- Test: `toolRows[id].status.textContent` mutated correctly by `tool_update` when row is collapsed.

**Exit criteria**:
- [x] Tool calls with `payload.command` render with `.acp-tool-toggle` in `.acp-tool-head`; command wrapper starts `hidden`.
- [x] Clicking toggle shows/hides command wrapper; `aria-expanded` reflects state.
- [x] Tool calls without `payload.command` have no toggle; head identical to today.
- [x] `tool_update` adding a command to existing row: toggle added, wrapper starts hidden.
- [x] In-place `status.textContent` mutation via `toolRows` works regardless of collapse state.
- [x] No class name derived from wire data.
- [x] `tests/acp_page.test.mjs` updated; suite green.

**Implementation (2026-08-11, code: e00b677 + 0630f59 + 122ec87)**
Phase 2 adds per-call command body collapse to the ACP transcript. A `_makeToolToggle(cmdWrap, toolTitle)` helper was extracted near `addToolCall()` — it builds a `<button class="acp-tool-toggle">` with `aria-expanded="false"` and an accessible label including the tool name (capped at 80 chars), wiring a click handler that toggles `cmdWrap.hidden` and updates both `aria-expanded` and `aria-label`. The new-row path in `addToolCall()` now wraps `commandBlock(payload)` in a hidden container and appends the toggle to the head; tool calls without a command are unchanged. The `known`-row path handles `tool_update` adding a command, with an idempotency guard. CSS adds chevron toggle animation via `aria-expanded` attribute selector, plus `:focus-visible` and `:hover` rules. A `parentElement` getter was added to the `El` harness class. Post-review auto-fixes: `toolTitle` 80-char cap, `:focus-visible`/`:hover` CSS rules, tests for kind-fallback and toggle idempotency. Final test count: 223 pass / 0 fail.

### 2026-08-12 — Implementation Review (after Phase 2, personas: Senior engineer, End-user advocate, Maintainability reviewer, Security auditor)

Implementation health: Green (after 1 auto-fix cycle).
10 findings total (0 High, 4 Medium, 6 Low). All Medium auto-fixed.
QA verification: PASS (browser — toggle visible with tool name, collapsed by default, expand/collapse works, 0 JS errors).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| S1 | Medium | `toolTitle` in aria-label unbounded — agent-authored wire value up to 10k chars could bloat the attribute. | Fixed — `safeTitle = (toolTitle || 'tool').slice(0, 80)` at `_makeToolToggle` entry |
| UX-F1 | Medium | `.acp-tool-toggle` missing `:focus-visible` rule — inconsistent with all other interactive ACP elements. | Fixed — added `outline: 2px solid var(--accent); outline-offset: 2px` |
| M1 | Medium | Missing test for `payload.kind` fallback when title absent. | Fixed — test added asserting aria-label includes kind value |
| M2 | Medium | Missing test for toggle idempotency (two tool_updates with command = one toggle). | Fixed — test added asserting exactly one toggle after two updates |
| UX-F2 | Low | No `:hover` state on toggle (inconsistent with other controls). | Fixed — added `color: var(--text)` on hover |
| M3 | Low | `removeChild`/`insertBefore`/`nextSibling` not yet in El harness — Phase 3 will add as first step. | Escalated — Phase 3 prerequisite, handled there |
| SE-F1 | Low | ROADMAP.md Phase 2/3 doc update deferred to Phase 3. | Escalated — Phase 3 handles both lines per plan §8 |
| SE-F2 | Low | `subAddToolCall` has no collapse — documented scope boundary (SC5). | User: accepted — out of scope per plan intent, ROADMAP entry sufficient |
| S3 | Low | `aria-live` comment in `_makeToolToggle` click handler missing. | Fixed — comment added |
| M4 | Low | UX-F4: zero-identity test (`title='' kind=''`) missing. | Escalated — edge case, behavior documented in review; implementer may add |


---

### Phase 3: Group consecutive tool calls at turn end [QA]

**Goal**: At `meta turn:end`, consecutive sequences of >=2 tool call rows are wrapped in a collapsible group container with a count + name-tally + status-tally header. Groups collapsed by default.

**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/static/style.css`, `tests/acp_page.test.mjs`

**Covers**: SC3, SC5

**Key invariant**: `toolRows[id].status` and `toolRows[id].body` are DOM element references. Reparenting those elements into a group container does not invalidate the references. `tool_update` mutations continue to work unchanged.

**`toolGroup` declaration site**: add `var toolGroup = null;` immediately after `var toolRows = Object.create(null);` (currently at `acp.html:473`) so it sits with the other module-level conversation-state variables.

**`TOOL_STATUS_LABEL` map**: add near `RAIL_STATUS` constants. Keys: `started`, `completed`, `failed`, `denied`, `approved`, `update`. Wire values absent from the map are silently omitted from the status tally — no raw wire string ever reaches visible UI.

**`STATUS_BUCKET_ORDER` relationship to `RAIL_STATUS`**: `STATUS_BUCKET_ORDER` contains `'working'`, `'waiting'`, `'errored'` which also appear in `RAIL_STATUS`. These are the same semantic values — the plan author must keep them in sync. To reduce divergence risk, define `STATUS_BUCKET_ORDER` as `['working', 'waiting', 'errored', 'available', 'locked']` and add a comment citing `RAIL_STATUS`.

**Test harness extension required** (identified in review): `tests/acp_page.test.mjs`'s `El` class is missing `removeChild`, `insertBefore`, and `nextSibling` getter. Phase 3 tests that call `flushToolGroups()` will throw before any assertion runs without these. Add them to the `El` class before writing Phase 3 tests.

**Changes to `acp.html`**:

1. **Add state variable** alongside `agentBody`, `toolRows` etc.:
   ```js
   var toolGroup = null; // array of acp-msg-tool rows for the current turn, or null
   ```

2. **Extend `clearTranscript()`** — add `toolGroup = null;` alongside `toolRows = Object.create(null);`.

3. **Extend `meta turn:start` handler** (`acp.html:~4366`) — add `toolGroup = null;` to reset at each new turn.

4. **Modify `addToolCall()` new-row path** — after `transcriptEl.appendChild(row)`, accumulate the row:
   ```js
   if (!toolGroup) toolGroup = [];
   toolGroup.push(row);
   ```

5. **Extend `meta turn:end` handler** (`acp.html:~4377`) — after the existing resets, call `flushToolGroups()`, then `toolGroup = null;`.

6. **Add `flushToolGroups()` function**:

```js
function flushToolGroups() {
  if (!toolGroup || toolGroup.length < 2) { toolGroup = null; return; }
  // Split toolGroup into consecutive sub-runs by DOM adjacency.
  // A prose bubble between two tool calls in the same turn means they are
  // not adjacent siblings — split into separate sub-runs.
  var runs = [];
  var cur = [toolGroup[0]];
  for (var i = 1; i < toolGroup.length; i++) {
    // Two rows are adjacent if the previous row's nextSibling is the next row
    // (accounting for possible whitespace text nodes between them is not needed
    // since appendchild of element nodes produces no text nodes).
    if (toolGroup[i - 1].nextSibling === toolGroup[i]) {
      cur.push(toolGroup[i]);
    } else {
      runs.push(cur);
      cur = [toolGroup[i]];
    }
  }
  runs.push(cur);

  // Capture scroll position once before any DOM mutation — stuckToBottom()
  // measures transcriptEl geometry, which changes after each group insertion,
  // so a per-iteration call would give wrong readings for all but the first run.
  var stick = stuckToBottom();

  for (var r = 0; r < runs.length; r++) {
    var rows = runs[r];
    if (rows.length < 2) continue; // single-call sub-run stays as individual row

    // Capture insertion point BEFORE removing any row
    var insertBefore = rows[rows.length - 1].nextSibling;

    // Build name tally (preserves first-seen order)
    var nameCounts = Object.create(null);
    var nameOrder = [];
    for (var n = 0; n < rows.length; n++) {
      var nameEl = rows[n].querySelector('.acp-tool-name');
      var nm = nameEl ? nameEl.textContent : 'tool';
      if (!nameCounts[nm]) { nameCounts[nm] = 0; nameOrder.push(nm); }
      nameCounts[nm]++;
    }

    // Build status tally
    var stCounts = Object.create(null);
    var stOrder = [];
    for (var s = 0; s < rows.length; s++) {
      var stEl = rows[s].querySelector('.acp-tool-status');
      var st = stEl ? stEl.textContent : '';
      if (st) {
        if (!stCounts[st]) { stCounts[st] = 0; stOrder.push(st); }
        stCounts[st]++;
      }
    }

    var nameTally = nameOrder.map(function (nm) {
      return nameCounts[nm] > 1 ? nm + ' \xd7' + nameCounts[nm] : nm;
    }).join(', ');
    var stTally = stOrder.map(function (st) {
      return stCounts[st] > 1 ? st + ' \xd7' + stCounts[st] : st;
    }).join(', ');
    var headerText = rows.length + ' tool calls (' + nameTally + ')' +
                     (stTally ? ' \xb7 ' + stTally : '');

    // Build group container
    var group = document.createElement('div');
    group.className = 'acp-tool-group';
    var groupToggle = document.createElement('button');
    groupToggle.className = 'acp-tool-group-toggle';
    groupToggle.type = 'button';
    groupToggle.setAttribute('aria-expanded', 'false');
    groupToggle.textContent = headerText;
    group.appendChild(groupToggle);

    var groupBody = document.createElement('div');
    groupBody.className = 'acp-tool-group-body';
    groupBody.hidden = true; // collapsed by default

    // Move rows into group body
    for (var k = 0; k < rows.length; k++) {
      transcriptEl.removeChild(rows[k]);
      groupBody.appendChild(rows[k]);
    }
    group.appendChild(groupBody);

    // Insert group at the position the first row occupied
    transcriptEl.insertBefore(group, insertBefore);

    // IIFE per iteration: var is function-scoped so without this, all click
    // handlers in a multi-run turn share the final iteration's groupToggle +
    // groupBody bindings — every toggle controls the last group. The same
    // IIFE pattern is used at acp.html:2102 (renderCrewPanel).
    (function (gt, gb) {
      gt.setAttribute('aria-label', 'Expand tool call group');
      gt.addEventListener('click', function () {
        var open = gt.getAttribute('aria-expanded') === 'true';
        gt.setAttribute('aria-expanded', open ? 'false' : 'true');
        gt.setAttribute('aria-label', open ? 'Expand tool call group' : 'Collapse tool call group');
        gb.hidden = open;
      });
    }(groupToggle, groupBody));

    if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
  }
  toolGroup = null;
}
```

> **Rejected approach**: rebuilding `toolGroup` mid-turn whenever `appendChunk` fires. `appendChunk` does not null `agentBody` — only `addToolCall` and turn boundaries do that. So a prose chunk between two tool calls in the same turn produces a new `agentBody` element that sits between them as a DOM sibling, making them non-adjacent. The DOM-adjacency check in `flushToolGroups` already handles this without any mid-turn tracking. **Use instead**: the `nextSibling` adjacency check already in the plan.

**Changes to `style.css`**:
```css
.acp-tool-group { margin: 2px 0; }
.acp-tool-group-toggle {
  width: 100%;
  background: none;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 4px 8px;
  text-align: left;
  cursor: pointer;
  font-size: 11px;
  color: var(--text-dim);
  display: flex;
  align-items: center;
  gap: 6px;
}
.acp-tool-group-toggle::before {
  content: '\25B6';
  display: inline-block;
  transition: transform 0.15s;
  flex-shrink: 0;
}
.acp-tool-group-toggle[aria-expanded="true"]::before { transform: rotate(90deg); }
.acp-tool-group-body { padding-left: 12px; }
```

**Changes to `tests/acp_page.test.mjs`**:
- Add `deliverTurn(page, toolCalls)` helper: delivers `meta turn:start`, each tool_call frame, then `meta turn:end`, then settles.
- Test: turn with 3 tool calls -> one `.acp-tool-group`; individual rows removed from transcript root; group collapsed by default.
- Test: group header text matches `"3 tool calls (shell x2, read_file x1) · completed x3"` format.
- Test: clicking group toggle reveals rows; individual rows inside start collapsed (Phase 2 toggle).
- Test: turn with 1 tool call -> no group; row stays at transcript root.
- Test: turn with tool_call, chunk (prose), tool_call, tool_call -> first call stays individual; last two form a group.
- **Test: turn with tool_call A, tool_call B, chunk, tool_call C, tool_call D -> TWO separate groups (A+B and C+D); clicking toggle on group-A expands only group-A's body, not group-B's.** (This is the critical test for the var-in-loop closure fix — F1.)
- Test: `toolRows[id].status.textContent` mutation works after grouping (reparenting does not break reference).
- Test: replay safety — `history` frame with turn including tool_calls + `turn:end` produces the group.
- Test: `toolGroup` is null after `clearTranscript()`.

**Exit criteria**:
- [ ] `El` harness class extended with `removeChild`, `insertBefore`, `nextSibling` before Phase 3 tests run.
- [ ] Turn with >=2 consecutive tool calls: one `.acp-tool-group` at `meta turn:end`; individual rows inside group body.
- [ ] Group collapsed by default; toggle expands/collapses.
- [ ] Group toggle `aria-label` toggles between "Expand tool call group" and "Collapse tool call group".
- [ ] Group header format correct: `"N tool calls (name xCount) · status xCount"` with `TOOL_STATUS_LABEL`-narrowed values; unknown wire status values omitted.
- [ ] Single tool call in a turn: no group; row unchanged.
- [ ] Turn producing two disjoint groups (e.g. A+B, prose, C+D): two groups rendered; clicking A+B toggle expands only A+B, not C+D (validates IIFE closure fix).
- [ ] Non-adjacent calls (prose between): separate groups or individuals per sub-run size.
- [ ] `toolRows` references valid after reparenting; `tool_update` status mutations work.
- [ ] `toolGroup` reset in `clearTranscript()` and at `meta turn:start`.
- [ ] Scroll preserved: `stuckToBottom()` captured once before runs loop; restored after all groups inserted.
- [ ] `tests/acp_page.test.mjs` updated; suite green.
- [ ] `plans/ROADMAP.md` lines 34 and 202 updated — delivered tool-display candidates marked (see §8).


---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `toolRows` references broken by reparenting into group container | High — `tool_update` silently fails to update status | DOM element refs survive reparenting; verify with a `tool_update`-after-grouping test |
| `railSetMode('status')` falls through to `'project'` (two-place normalization) | High — status mode silently broken | Extend both the normalization ternary AND the localStorage init; test both code paths |
| Non-adjacent tool calls wrongly grouped (prose between them) | Medium — visual artifact groups unrelated calls | DOM `nextSibling` adjacency check in `flushToolGroups`; test with prose-between scenario |
| Scroll disrupted by group restructuring at `turn:end` | Medium — user loses scroll position | Capture `stuckToBottom()` before restructuring; restore after; test at bottom and away from bottom |
| `status: ""` for available/locked sessions creates wrong bucket | High — all available sessions in wrong group | Bucket key uses `(availability, status)` pair via `statusBucketKey()`; both inputs narrowed through closed-set maps |
| Insert-point bug moves group to wrong transcript position | Medium — transcript visual order wrong | Capture `rows[last].nextSibling` BEFORE removal; test that group appears where original rows were |
| `tool_update` that adds command to collapsed row also needs toggle | Medium — toggle missing on late-arriving command | Covered by the `known` branch in `addToolCall`; test with `tool_update` that adds command |
| `aria-live="polite"` re-announces moved nodes during grouping | Low — screen reader noise at turn:end | Restructuring happens once at turn:end when turn is over; no ongoing live-region pollution |

## 7) Verification

**Automated** (run after each phase):
```
node tests/acp_page.test.mjs
```
Expected: all checks pass, 0 failed.

**Manual runtime verification** (at Step 9):
1. Start PowerAtlas: `.venv-PowerAtlas\Scripts\power-atlas`
2. Open `http://127.0.0.1:<port>/acp`
3. Open sliders settings menu — verify three options: Date / Project / Status
4. Switch to Status mode — verify sessions bucket into Working / Waiting / Errored / Available / Locked; empty buckets absent
5. Switch back to Project — verify normal workspace grouping
6. Start a kiro-cli session via ACP that runs tool calls with commands
7. Verify tool call rows with command show a toggle chevron; collapsed by default
8. Click toggle — verify command body revealed
9. Verify tool calls without command show no toggle
10. When a turn with multiple tool calls completes, verify a group row appears
11. Click group row — verify individual calls revealed; each starts collapsed
12. Click an individual call toggle — verify command body shown
13. Reload — verify group and collapse state reset (not persisted by design)

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Add third rail mode "Status" to *Agent sessions* section (~line 181). Describe the 5-bucket layout and that it groups by session status dot. | 1 |
| `plans/ROADMAP.md` | Line 188: update "two groupings" to "three groupings"; note status mode also uses the flat listing. | 1 |
| `plans/ROADMAP.md` | Lines 34 and 202: mark delivered tool-display candidates (collapse/expand from Phase 2, visual grouping from Phase 3); keep remaining open candidates (type icons, structured arg display). | 2 & 3 (doc-table-only) |

## 9) Implementation Divergences from Plan

_Reserved — filled during implementation._

## Review Log

### 2026-08-11 — Implementation Review (after Phase 1, personas: Senior engineer, End-user advocate, Architect, Maintainability reviewer)

Implementation health: Green (after 2 auto-fix cycles + 1 regression fix).
15 findings total (2 High, 6 Medium, 7 Low). All High and Medium auto-fixed.
QA verification: PASS (browser — Status mode renders correctly, 3-bucket cap works, load-more works, localStorage persists, 0 JS errors).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| F1 | High | `railMore` click handler used `railMode === 'date'` only; status mode called `loadGroupPage` instead of `loadFlatPage`. | Fixed — extended to `railMode === 'date' \|\| railMode === 'status'` |
| F2 | High | `railRefresh()` tick-poll used `railMode === 'date'` only; status dots wouldn't update in status mode. | Fixed — same condition extension in `railRefresh()` |
| F3 | Medium | No test for Load-more click in status mode dispatching flat endpoint. | Fixed — test added |
| F4 | Medium | No test for tick-poll in status mode dispatching `?mode=recent`. | Fixed — test added |
| F5 | Medium | `renderRailStatus()` had no session count cap — contradicted README "three sessions" claim. | Fixed — `RAIL_SESSION_SIZE` cap + "Show N more" per bucket |
| F6 | Medium | `statusBucketKey` test missing `held+waiting` and `held+errored` sub-cases. | Fixed — assertions added |
| F7 | Medium | No empty-case test for status mode. | Fixed — test added |
| N1 | Medium | `railFocus = { status: k }` in Show-N-more handler had no matching branch in `railRestoreFocus()`. | Fixed — `want.status` branch added; test added |
| F8 | Low | README "Either way" / "either grouping mode" applied to 3 modes. | Fixed — updated wording |
| F9 | Low | Stale inline comments (two-mode references, key list, railFlat JSDoc). | Fixed — updated |
| F10 | Low | `TOOL_STATUS_LABEL` had no forward-reference comment. | Fixed — comment added |
| F11 | Low | `aria-controls` missing on bucket head toggles (pre-existing omission). | Escalated — pre-existing gap in all modes; follow-up scope |
| F12 | Low | Keyboard focus drops to `<body>` on mode switch (pre-existing in date mode). | Escalated — pre-existing; follow-up scope |
| F13 | Low | Ternary chain duplicated in two call sites. | Escalated — readable at 3 values; extract if 4th mode added |
| F14 | Low | Bucket ordering test missing Errored-relative assertions. | Fixed — `Waiting < Errored` and `Errored < Available` added |

### 2026-08-11 — Plan Creation Review (via /qplan, effort: high, 4 personas)

21 findings (7 High, 8 Medium, 6 Low). 15 auto-resolved; 6 Low escalated to implementer.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| F1 | High | `var`-in-loop closure in `flushToolGroups` — all toggles control the last group's body | Fixed — IIFE wraps each iteration's DOM build + listener in Phase 3 |
| F2 | High | `railSummary()` missing `'status'` arm — shows workspace count in status mode | Fixed — Phase 1 code change + exit criterion + test added |
| F3 | High | `acp-tool-group-toggle` has no `aria-label`; full tally re-announced on each keypress | Fixed — IIFE sets initial `aria-label`; click handler toggles it |
| F4 | High | Group header status tally uses raw wire strings without closed-set narrowing | Fixed — `TOOL_STATUS_LABEL` map added; unknown values omitted |
| F5 | High | `El` harness missing `removeChild`/`insertBefore`/`nextSibling` — Phase 3 tests fail before assertions | Fixed — Phase 3 exit criteria: extend El before Phase 3 tests |
| F6 | High | Toggle-creation code duplicated between new-row path and `known` branch | Fixed — `_makeToolToggle(cmdWrap, toolTitle)` helper extracted in Phase 2 |
| F7 | High | `toolGroup` declaration site unspecified in plan | Fixed — Phase 3 specifies insertion after `var toolRows` at `acp.html:473` |
| F8 | Medium | Empty-state filter text binary ternary — status mode emits "more workspaces" | Fixed — Phase 1 code change: `railMode !== 'project'` ternary |
| F9 | Medium | `stuckToBottom()` inside runs loop measures stale DOM after first insertion | Fixed — moved before runs loop in Phase 3 code block |
| F10 | Medium | No test for two disjoint groups — F1 undetectable without it | Fixed — two-group toggle-isolation test added to Phase 3 |
| F11 | Medium | `STATUS_BUCKET_ORDER` string literals may diverge from `RAIL_STATUS` | Fixed — Phase 3 notes it must cite `RAIL_STATUS` |
| F12 | Medium | `toolGroup` behavior with absent `turn:end` in replay undocumented | Fixed — Phase 3 invariants note added |
| F13 | Medium | Phase 2 toggle `aria-label` lacks tool name — 15 identical labels | Fixed — `_makeToolToggle` includes `toolTitle` in `aria-label` |
| F14 | Medium | `railSummary` test absent from Phase 1 exit criteria | Fixed — added as exit criterion in Phase 1 |
| F15 | Medium | No `deliverToolCall` helper for Phase 2 | Escalated — implementer may add; Phase 2 test list is clear without it |
| F16 | Low | `·` separator may be invisible at low contrast | Escalated — implementer visual decision; `—` acceptable alternative |
| F17 | Low | No null-guard on `head = querySelector('.acp-tool-head')` in Phase 2 known-branch | Escalated — construction-time invariant; add defensive comment |
| F18 | Low | Group header is a static snapshot; no comment noting intentional staleness | Escalated — add inline comment in `flushToolGroups` during implementation |
| F19 | Low | Empty status mode may render blank panel | Fixed — Phase 1 notes returning 0 from `renderRailStatus()` triggers `renderRail()`'s existing empty-state |
| F20 | Low | `toolGroup = null` ownership comment missing at reset point | Escalated — add inline comment during implementation |
| F21 | Low | `TOOL_STATUS_LABEL` unknown-value omission behavior not stated | Fixed — Phase 3 `TOOL_STATUS_LABEL` section specifies omission |

## Harness Improvement Opportunities

- The `meta turn:end` grouping pass (Phase 3) has no precedent in `tests/acp_page.test.mjs`'s frame-sequence helpers — a `deliverTurn(page, toolCalls)` helper that sends `turn:start`, N tool_call frames, and `turn:end` would reduce boilerplate in the new checks. Cost: noticed when planning Phase 3 test coverage. Suggested change: add a `deliverTurn(page, toolCalls)` helper to the test harness.
