# 260922-1327 — Dashboard eager connect on textarea focus

> **Date**: 2026-09-22
> **Status**: Complete
> **Last Updated**: 2026-09-22 15:55
> **Scope**: Start ACP session on textarea focus instead of first send, for `available` kiro-cli-v3 sessions in the dashboard panel.

---

## Intent

The dashboard session panel lazily starts an ACP session only when the user
presses Send (`dashSendPrompt()`, `index.html:2096`). For `available` sessions
(cold starts), this means the `commands`/`skills` catalogue frames — which
arrive after kiro-cli's agent starts post-load — are never populated before the
first keystroke. The slash-command palette (`/`) has nothing to show on the very
first prompt.

The fix: trigger `dashConnect()` + `send('load', sid)` on textarea **focus**
instead of on send. The agent starts earlier; commands/skills arrive before the
user types. The textarea stays enabled throughout — no visible UX change at
focus time.

Scope: `available` sessions only. `held` sessions already attach via
`dashMaybeAttach()`'s subscribe path and are unaffected.

### Success criteria

- SC-1: Focusing `dashPromptInput` on an `available` kiro-cli-v3 session
  triggers a `load` request over the WebSocket; `_dashLoadingSid` is set,
  `_dashOrigin = 'dashboard'`, `_dashEagerLoadPending = true`. No visible UI
  change (textarea stays enabled, no note, no disabled state).
- SC-2: Slash-command palette receives `commands`/`skills` frames for a session
  that was focused and held idle for a few seconds before the first send (agent
  had time to start post-load).
- SC-3: Navigating away from a focused-but-unsent session closes the slot via
  the stale-arrival guard (`index.html:2785`) when the `session` frame arrives
  after navigate-away (`_dashCloseIfAbandoned` does not send `close` because
  `_dashAttachedSid` is null at navigate-away time; the stale-arrival guard is
  the sole close path for in-flight eager loads).
- SC-4: Sending before `session` frame arrives does not double-load; a single
  `load` completes and delivers the prompt via `_dashPendingSend` through the
  `history` frame handler.
- SC-5: A focus-triggered load failure (capacity, throttled) shows no Reload
  button and re-enables the textarea silently; `_dashEagerLoadPending` is
  cleared; the send path retries the load normally.
- SC-6: `held` sessions, already-attached sessions, and sessions with a pending
  create in flight are unaffected; the focus listener exits early for all three.

## Context

All changes are in `src/power_atlas/templates/index.html` (inline JS, ~6673
lines). No Python, no CSS, no other files.

The current lazy-attach state variables at `dashSendPrompt()` lines 2081–2096:
`_dashOrigin = 'dashboard'`, `_dashLoadingSid = sid`, `_dashPendingSend = text`,
`_dashPendingImages`, `dashPromptInput.disabled = true`, `dashSendBtn.disabled =
true`, `dashSetComposerNote('Starting the agent…')`, `dashConnect(load)`.

The design splits these into:
- **Focus time**: `_dashOrigin`, `_dashLoadingSid`, `_dashEagerLoadPending`,
  `dashConnect(load)` — move here.
- **Send time**: `_dashPendingSend`, `_dashPendingImages` — stay here (content
  only exists when user sends). The `disabled` state and composer note stay at
  send time too, only appearing if the session frame hasn't yet arrived when the
  user sends.

**`held`-session timing**: `dashMaybeAttach()`'s `held` path enables
`dashPromptInput` **synchronously** inside the `fetch().then()` callback
(`index.html:1603–1606`), then sets `_dashOrigin = 'joined'` and calls
`dashConnect(subscribe)`. The `session` frame arrives **later** over the
WebSocket. During the window between textarea-enable and `session` frame arrival,
`_dashLoadingSid` is null and `_dashAttachedSid` is null — both focus guards
would pass without an explicit `_dashOrigin === 'joined'` check. The focus
listener must guard on `_dashOrigin === 'joined'` to avoid overwriting it with
`'dashboard'` and triggering an unsolicited `load` for a `held` session.

**Programmatic `focus()` in image handler**: `dashStageOne` (image-attachment
pipeline) calls `dashPromptInput.focus()` at line 1859 after inserting an
`[Image N]` marker. This is programmatic — not a user intent to start a session
— and must not trigger the eager load.

## Files to modify

| File | Change |
|---|---|
| `src/power_atlas/templates/index.html` | Add focus listener; add `_dashEagerLoadPending` var; add guard in `dashSendPrompt()`; modify error handler |
| `tests/acp_page.test.mjs` | Add tests for focus-triggered load, guards, error suppression |

## External Dependencies

None. JS-only change to `index.html`; no restart needed (hard reload suffices
per AGENTS.md).

## Rollout / Migration / Cleanup

None.

## Step-by-step

### 1. Declare `_dashEagerLoadPending` variable and add clear sites

Near the other `_dash*` variable declarations at `index.html:1254–1300`.

```javascript
// true while a focus-triggered (eager) load is in flight for _dashLoadingSid.
// Cleared in sync with every _dashLoadingSid = null assignment. Read by the
// error handler to suppress the Reload button for focus-triggered load failures
// the user did not explicitly initiate.
var _dashEagerLoadPending = false;
```

Add `_dashEagerLoadPending = false;` **immediately before** `_dashLoadingSid = null`
(or `_dashLoadingSid = null` equivalent) at each of these sites. The clear must be
unconditional — never inside an `if (_dashWasLazyAttaching)` or similar block:

| Site | Location | Notes |
|---|---|---|
| `_dashWs.onclose` | line 1061 | Place **before** `var _dashWasLazyAttaching = _dashLoadingSid !== null;` |
| `session` frame stale-arrival guard | line 2792 | Add alongside `_dashLoadingSid = null` |
| `session` frame normal attach | line 2849 | Add alongside `_dashLoadingSid = null` |
| `session` frame `created:true` path | line 2756 | Add alongside `_dashLoadingSid = null` |
| `agent_died` handler | line 3416 | Add alongside `_dashLoadingSid = null` |
| error handler `_dashLoadingSid` block | line 3541 | Add before `_dashLoadingSid = null`; captured as `var wasEager = _dashEagerLoadPending;` first (Step 4) |
| `session_closed` handler | line ~3269 | Defense-in-depth: add even though `_dashAttachedSid` is normally set before `session_closed` fires |

**Not** a clear site: `dashCloseIfAbandoned()` — this function does NOT clear
`_dashLoadingSid` (confirmed by probe; the stale-arrival guard handles the
in-flight case), so `_dashEagerLoadPending` must not be cleared there.

**Exit criterion**: grep for every `_dashLoadingSid = null` in `index.html`; confirm
each adjacent line also contains `_dashEagerLoadPending = false`.

**Covers**: SC-1, SC-3, SC-5, SC-6

### 2. Add the focus listener on `dashPromptInput`

After the existing `dashPromptInput.addEventListener('input', ...)` at
`index.html:1446`.

```javascript
// Eager-load trigger (SC-1): start the ACP session as soon as the user
// focuses the textarea, so the agent has time to start and deliver
// commands/skills frames before the first keystroke.
//
// Guards — skip if any of the following are true:
//   _dashLoadingSid      -- a load is already in flight; avoids double-load.
//   _dashAttachedSid     -- already attached (reconnect / previously held session).
//   _dashOrigin==='joined'-- a held session's subscribe is in flight but
//                           _dashAttachedSid is not yet set; do not overwrite
//                           _dashOrigin with 'dashboard' (held-session race).
//   _dashPendingCreate   -- a close-then-create flow is in progress; do not
//                           consume a slot that conflicts with the pending create.
//   !_viewingSid         -- no session selected.
//   !ACP_TOKEN           -- ACP not available.
//   _dashEagerFocusSuppressed -- programmatic focus (e.g. dashStageOne's
//                           image handler at line 1859); set/cleared around
//                           that call (see Step 3).
dashPromptInput.addEventListener('focus', function () {
  if (_dashLoadingSid || _dashAttachedSid) return;
  if (_dashOrigin === 'joined') return;
  if (_dashPendingCreate) return;
  if (!_viewingSid || !ACP_TOKEN) return;
  if (_dashEagerFocusSuppressed) return;
  var sid = _viewingSid;
  _dashOrigin = 'dashboard';
  _dashLoadingSid = sid;
  _dashEagerLoadPending = true;
  dashConnect(function () { send('load', {}, sid); });
});
```

Declare `_dashEagerFocusSuppressed` near the other flags:

```javascript
// true during a programmatic dashPromptInput.focus() call (e.g. the image-
// attachment handler at line 1859) to suppress the eager-load focus listener.
var _dashEagerFocusSuppressed = false;
```

**Covers**: SC-1, SC-2, SC-6

### 3. Suppress programmatic focus in `dashStageOne`

In `dashStageOne` at line 1859, wrap the `dashPromptInput.focus()` call:

```javascript
// Suppress the eager-load focus listener for this programmatic focus
// (user is attaching an image, not expressing intent to start a session).
_dashEagerFocusSuppressed = true;
dashPromptInput.focus();
_dashEagerFocusSuppressed = false;
```

**Covers**: SC-6

### 4. Add double-load guard in `dashSendPrompt()`'s lazy-attach path

In `dashSendPrompt()`, just before the existing lazy-attach block at
`index.html:2077`, add:

```javascript
  // Eager-load already in flight for this sid: a focus listener started a load
  // before the user sent. Set _dashPendingSend/Images so the history frame
  // handler delivers the prompt, disable the input while the load completes,
  // and return without issuing a second load.
  if (_dashLoadingSid === sid) {
    _dashPendingSend = text;
    _dashPendingImages = dashAttachments.length
      ? dashAttachments.map(function (item) { return {mimeType: item.mimeType, data: item.data}; })
      : null;
    dashPromptInput.disabled = true;
    dashSendBtn.disabled = true;
    dashSetComposerNote('Starting the agent\u2026');
    return;
  }
  // Cross-session guard: an eager load for a different sid is in flight (user
  // focused A, clicked B, sent in B). The in-flight A load will be closed by
  // the stale-arrival guard when its session frame arrives. Do not issue a
  // second load for B while A is unresolved -- the stale-arrival guard fires
  // quickly (< 1s typically). Set _dashPendingSend and wait.
  if (_dashLoadingSid && _dashLoadingSid !== sid) {
    _dashPendingSend = text;
    _dashPendingImages = dashAttachments.length
      ? dashAttachments.map(function (item) { return {mimeType: item.mimeType, data: item.data}; })
      : null;
    dashPromptInput.disabled = true;
    dashSendBtn.disabled = true;
    dashSetComposerNote('Starting the agent\u2026');
    return;
  }
```

The existing lazy-attach block at lines 2081–2096 follows unchanged for the
case where `_dashLoadingSid` is null (no eager load in flight).

> **Note on cross-session guard**: when the cross-session guard fires, `_dashPendingSend`
> is set for `sid` (the current session B), but `_dashLoadingSid` is set to A's
> sid. The `history` frame handler checks `_dashPendingSend !== null` and
> `_dashAttachedSid === sid` — for A's session frame, `_dashAttachedSid` will
> not equal B's sid, so the pending send is not consumed spuriously. When the
> stale-arrival guard fires for A (clears `_dashLoadingSid`, `_dashEagerLoadPending`),
> the user can retry their send from B's composer, which will now take the
> standard lazy-attach path. Alternatively, add a check after the stale-arrival
> guard fires to auto-retry the pending send if `_dashPendingSend` is set for
> `_viewingSid`.

**Covers**: SC-4

### 5. Modify error handler to suppress Reload button for eager-load failures

In the error handler's `_dashLoadingSid` cleanup block at `index.html:3540–3558`,
change:

Before:
```javascript
    if (sid && sid === _dashLoadingSid) {
      _dashLoadingSid = null;
      _dashPendingSend = null;
      _dashPendingImages = null;
      dashReloadBtn.hidden = false;
      dashPromptInput.disabled = false;
      dashSetComposerNote('');
      dashRefreshComposerControls();
    }
```

After:
```javascript
    if (sid && sid === _dashLoadingSid) {
      var wasEager = _dashEagerLoadPending;
      _dashEagerLoadPending = false;
      _dashLoadingSid = null;
      _dashPendingSend = null;
      _dashPendingImages = null;
      if (!wasEager) dashReloadBtn.hidden = false;  // suppress for focus-triggered failures
      dashPromptInput.disabled = false;
      dashSetComposerNote('');
      dashRefreshComposerControls();
    }
```

**Covers**: SC-5

### 6. Update `tests/acp_page.test.mjs`

The dashboard region of `index.html` is loaded and exercised by the test
harness (confirmed: `_dashLoadingSid`, `dashConnect`, `dashSendPrompt`,
`dashReloadBtn` are all exercised at lines 11062, 11090, 14406, 14452–14461).
Add tests alongside the existing reconnect/lazy-load tests:

a. **Focus triggers eager load**: set `_viewingSid = 'sess-1'`, `ACP_TOKEN = 'tok'`,
   all guards false. Dispatch a `focus` event on `dashPromptInput`. Assert
   `_dashLoadingSid === 'sess-1'`, `_dashEagerLoadPending === true`,
   `_dashOrigin === 'dashboard'`, and a `load` frame was sent.

b. **Focus guard: `_dashLoadingSid` already set**: set `_dashLoadingSid = 'sess-1'`.
   Dispatch focus. Assert no second load sent, `_dashLoadingSid` unchanged.

c. **Focus guard: `_dashAttachedSid` already set**: set `_dashAttachedSid = 'sess-1'`.
   Dispatch focus. Assert no load sent.

d. **Focus guard: `_dashOrigin === 'joined'`** (`held`-session race): set
   `_dashOrigin = 'joined'`, `_viewingSid = 'sess-1'`. Dispatch focus. Assert
   no load sent, `_dashOrigin` unchanged.

e. **Focus guard: `_dashPendingCreate` set**: set `_dashPendingCreate = {cwd: '/foo', mode: 'default'}`.
   Dispatch focus. Assert no load sent.

f. **Focus guard: `_dashEagerFocusSuppressed`**: set `_dashEagerFocusSuppressed = true`.
   Dispatch focus. Assert no load sent.

g. **Double-load guard same sid**: set `_dashLoadingSid = 'sess-1'`,
   `_viewingSid = 'sess-1'`, `dashPromptInput.value = 'hello'`. Call
   `dashSendPrompt()`. Assert `_dashPendingSend === 'hello'`, no load sent,
   `dashPromptInput.disabled === true`.

h. **Cross-session guard**: set `_dashLoadingSid = 'sess-A'`, `_viewingSid = 'sess-B'`,
   `dashPromptInput.value = 'hello'`. Call `dashSendPrompt()`. Assert
   `_dashPendingSend === 'hello'`, no new load sent for sess-B, textarea disabled.

i. **Eager-load failure suppresses Reload button**: set `_dashLoadingSid = 'sess-1'`,
   `_dashEagerLoadPending = true`. Feed an error frame with `sid = 'sess-1'`.
   Assert `dashReloadBtn.hidden === true`, `dashPromptInput.disabled === false`.

j. **Non-eager load failure still shows Reload button**: same as (i) but
   `_dashEagerLoadPending = false`. Assert `dashReloadBtn.hidden === false`.

k. **`close_in_progress` with eager load suppresses Reload**: same as (i) but
   `payload.code = 'close_in_progress'`. Confirm the `_dashLoadingSid` block
   runs before the early return and suppresses Reload.

**Covers**: SC-1, SC-4, SC-5, SC-6

## Verification

1. Run `node tests/acp_page.test.mjs` — must pass.
2. Grep `index.html` for every `_dashLoadingSid = null`; confirm each site has
   an adjacent `_dashEagerLoadPending = false`.
3. Hard-reload the dashboard (`Ctrl+Shift+R`). Click an `available` kiro-cli-v3
   session row, then click into the textarea. Observe in the ACP debug log that
   a `load` request fires at focus. Confirm textarea stays enabled and no
   "Starting the agent…" note appears.
4. With the same session focused, wait ~2s, type `/`. Confirm the slash-command
   palette populates.
5. Navigate away before the eager load completes (click a different session
   row). The close does NOT fire immediately — wait for the `session` frame to
   arrive (visible in the ACP debug log as an inbound `session` frame for the
   first session's sid), then confirm a `close` for that sid was sent. This is
   the stale-arrival guard at line 2785, not `dashCloseIfAbandoned`.
6. At the 8-session cap, focus an `available` session. Confirm no Reload button
   appears; textarea stays enabled.
7. Paste an image into the composer on an `available` session (before focusing
   the textarea deliberately). Confirm no `load` request fires (the programmatic
   `focus()` in `dashStageOne` is suppressed).

## Documentation updates

None.

---

## Exploration Discovery

1. **What was built**: No code written. Exploration only.

2. **Files in scope**: `src/power_atlas/templates/index.html` (~6673 lines, inline
   JS). No changes to `acp.py`, `composer-chrome.js`, or any other file.

3. **Key reference points**:
   - `dashMaybeAttach()`: line 1588 — shows composer for `available`/`held`; sets
     `_dashOrigin = 'joined'` for `held` path at line 1608.
   - `dashSendPrompt()` lazy-attach path: lines 2077–2096.
   - `dashConnect()`: line 978.
   - `dashCloseIfAbandoned()`: line 2674 — does NOT clear `_dashLoadingSid`.
   - Stale-arrival guard: line 2785.
   - Error handler load-failure block: lines 3540–3558.
   - `dashStageOne` programmatic focus: line 1859.

4. **Step 1.5**: Code-tracing trio dispatched (three parallel sub-agents). In-scope
   files were predominantly `index.html` inline JS.

5. **Risks and mitigations**:
   - *Slot leak on navigate-away mid-load*: stale-arrival guard at line 2785
     sends `close` when `session` frame arrives. Probed and confirmed.
   - *`held`-session race*: `dashMaybeAttach` enables textarea synchronously
     before `session` frame arrives; mitigated by `_dashOrigin === 'joined'`
     guard in focus listener (Step 2).
   - *Double-load same-session*: mitigated by `_dashLoadingSid === sid` guard
     in `dashSendPrompt()` (Step 4).
   - *Double-slot cross-session race*: mitigated by cross-session guard in
     `dashSendPrompt()` (Step 4).
   - *Spurious focus from image handler*: mitigated by `_dashEagerFocusSuppressed`
     flag around `dashStageOne`'s `dashPromptInput.focus()` (Step 3).
   - *`_dashPendingCreate` conflict*: mitigated by `_dashPendingCreate` guard in
     focus listener (Step 2).
   - *Commands/skills for cold sessions*: catalogue arrives only after agent
     starts post-load. Eager load shrinks the window but cannot guarantee
     catalogue arrival before the first keystroke. This is an improvement, not
     a guarantee.

6. **Resolved decisions**:
   - Q1: Trigger — `focus` event on `dashPromptInput`
   - Q2: Scope — `available` sessions only
   - Q3: Load-failure UX — silent absorption via `_dashEagerLoadPending` flag
   - Q4: Textarea state during eager load — stays enabled
   - Q5: Double-load guards — both added

7. **Open items**: None.

8. **Assumptions (unconfirmed)**: None.

## Follow-up Work (Deferred)

1. **Auto-retry pending send after cross-session stale-arrival guard fires.** When the
   user focuses session A (eager load fires), clicks session B, and sends in B — the
   cross-session guard in `dashSendPrompt` holds the send until A's `session` frame
   triggers the stale-arrival guard and clears `_dashLoadingSid`. After that, the user
   must manually re-press Send. An optional improvement: after the stale-arrival guard
   fires, check whether `_dashPendingSend` is set for `_viewingSid` and auto-trigger
   the send. Deferred — the case is uncommon and the fix has its own edge cases (source:
   Step 4 note).

## Review Log

### 2026-09-22 — Plan creation review (full effort, 4 personas)

Running full-effort review (4 personas: Senior engineer, Architect, Reliability engineer, Frontend specialist). 11 findings (3 High, 5 Medium, 3 Low). 8 auto-resolved. 1 finding refuted. 2 deferred.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `dashCloseIfAbandoned` is not a `_dashLoadingSid` clear site — plan incorrectly listed it, causing `_dashEagerLoadPending` to be cleared while the in-flight load continues, making the error handler show a spurious Reload button. | Fixed — removed from clear-site list in Step 1; added note "Not a clear site"; stale-arrival guard is the correct sole cleanup path for in-flight loads. |
| 2 | High | `held`-session race: `dashMaybeAttach` enables textarea synchronously before `session` frame arrives; focus guard only checked `_dashLoadingSid`/`_dashAttachedSid`, missing the window where `_dashOrigin === 'joined'`. | Fixed — added `_dashOrigin === 'joined'` guard to focus listener in Step 2. |
| 3 | High | `dashStageOne`'s programmatic `dashPromptInput.focus()` at line 1859 would spuriously trigger the eager load on image paste. | Fixed — added `_dashEagerFocusSuppressed` flag (declared + wrapped in Step 3). |
| 4 | High | Test harness claimed infeasible for dashboard region — refuted: `tests/acp_page.test.mjs` loads the full dashboard region and exercises `_dashLoadingSid`, `dashConnect`, `dashSendPrompt`, `dashReloadBtn`. | Refuted — no change needed. |
| 5 | Medium | `session_closed` handler not listed as a `_dashEagerLoadPending` clear site. | Fixed — added to Step 1 clear-site table. |
| 6 | Medium | `session.created:true` path (line 2756) clears `_dashLoadingSid` but was missing from Step 1 clear-site list. | Fixed — added to Step 1 clear-site table. |
| 7 | Medium | Cross-session race: focus A, click B, send B → two loads in flight, transiently over slot budget. | Fixed — added cross-session guard to `dashSendPrompt()` lazy-attach path in Step 4. |
| 8 | Medium | `_dashPendingCreate` guard missing from focus listener (close-then-create flow in progress). | Fixed — added `if (_dashPendingCreate) return;` to focus listener in Step 2. |
| 9 | Medium | `_dashEagerLoadPending = false` placement in `onclose` must be before `_dashWasLazyAttaching` capture — inside that block would be conditional. | Fixed — Step 1 now specifies: insert **before** `var _dashWasLazyAttaching = _dashLoadingSid !== null;`. |
| 10 | Low | Verification step 4 described immediate close on navigate-away; actual close fires via stale-arrival guard when `session` frame arrives, which is async. | Fixed — verification step 5 now correctly describes the stale-arrival close path and notes the async timing. |
| 11 | Low | No grep-gate exit criterion for all `_dashLoadingSid = null` sites. | Fixed — verification step 2 is now a mandatory grep-gate. |

## Harness Improvement Opportunities


### Implementation (2026-09-22, code: 45426a3)

Implemented all 6 steps in-session (Light tier). All plan steps match the implementation:
Step 1: `_dashEagerLoadPending` and `_dashEagerFocusSuppressed` variables declared; 7 clear sites added (plus `session_closed` defense-in-depth); `_dashEagerLoadPending = false` placed before `var _dashWasLazyAttaching = _dashLoadingSid !== null` in `onclose` per plan spec.
Step 2: Focus listener added after `dashSendBtn.addEventListener('click', dashSendPrompt)` with all 7 guards.
Step 3: `_dashEagerFocusSuppressed` set/cleared synchronously around `dashPromptInput.focus()` in `dashStageOne`.
Step 4: Double-load guard (`_dashLoadingSid === sid`) and cross-session guard (`_dashLoadingSid && _dashLoadingSid !== sid`) added in `dashSendPrompt` before the existing lazy-attach block.
Step 5: Error handler modified to capture `wasEager = _dashEagerLoadPending` before clearing; `if (!wasEager) dashReloadBtn.hidden = false`.
Step 6: 12 new test cases in `acp_page.test.mjs`; sandbox pre-declares `_dashEagerLoadPending` and `_dashEagerFocusSuppressed`.
667/667 tests passing on initial commit.

### 2026-09-22 — Implementation review (full effort, 4 personas, cycle 1)

Full-effort review: 4 personas (Senior engineer, Architect, Reliability engineer, Frontend specialist). 11 findings (1 High → reclassified Medium, 4 Medium, 6 Low). 9 auto-resolved in cycle 1 (commit 0de456c). 1 Low resolved in cycle 2 inline. 1 Low (SC-2 untestable in harness) accepted.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High→Medium | Cross-session stale-arrival: textarea left permanently disabled after A's session frame fires while B's prompt is pending; user input silently lost | Fixed (0de456c) — stale-arrival guard now restores `dashPromptInput.value` from `_dashPendingSend` and calls `disabled = false` + `dashRefreshComposerControls()` before return |
| 2 | Medium | `_dashPendingSend` (for B) silently discarded by stale-arrival guard with no feedback | Fixed (0de456c) — restored to textarea before clearing |
| 3 | Medium | BOM prepended to both files | Fixed (0de456c) — stripped |
| 4 | Medium | `session_closed` handler did not clear `_dashLoadingSid` — could block focus listener after idle-TTL race | Fixed (0de456c) — added null clears to `session_closed` handler |
| 5 | Medium | `_dashLoadingSid !== sid` branch in focus listener blocks B while A's load is in flight (Architect A-2) | Fixed by Fix 1 — once stale-arrival guard fires and re-enables textarea, B can proceed |
| 6 | Low | `onclose` `_dashEagerLoadPending` placement after `_dashWasLazyAttaching` capture deviated from plan spec | Fixed (0de456c) — moved before capture |
| 7 | Low | No `dashStageOne` end-to-end test | Accepted — the guard mechanism is tested via `_dashEagerFocusSuppressed` pre-set test; a full `dashStageOne` drive would require image encode pipeline setup |
| 8 | Low | `!ACP_TOKEN` guard untested | Fixed (0de456c) — test added |
| 9 | Low | No cross-session stale-arrival lifecycle test | Fixed (0de456c) — new test added verifying textarea re-enable + prompt restore |
| 10 | Low | Test (h) label imprecise / inert `_dashEagerLoadPending` pre-set | Fixed (0de456c) — label updated, pre-set removed |
| 11 | Low | SC-2 untestable in harness | Accepted — SC-2 is runtime-only (live agent needed); manual verification step 4 covers it |

### 2026-09-22 — Implementation review (full effort, cycle 2)

1 Low finding (redundant inner `sid !== _viewingSid` in stale-arrival restore — dead code). Applied inline (no commit). Ready state reached.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Low | Redundant `sid !== _viewingSid` in stale-arrival restore guard (always true given outer gate) | Fixed inline — removed dead condition; no behavior change |



### 2026-09-22 — Final review (Step 9, full effort, 2 personas)

Senior engineer + Reliability engineer. No findings. Health: Green. 670/670 tests passing.

All 6 SC items satisfied. Clear-site discipline confirmed mechanically. Harness integration verified. No documentation violations. Step 9b SKIP — runtime surface requires PowerAtlas running (AGENTS.md "Never restart PowerAtlas autonomously"); harness covers all mechanically testable surfaces.



## Completion Summary

Dashboard eager-connect feature shipped. The dashboard session panel now starts
an ACP session when the user clicks into the prompt textarea (instead of waiting
for the first send), so the slash-command catalogue is populated before the first
keystroke. All 6 SC items verified: harness (670/670) + live runtime QA (PASS).

5 commits shipped (45426a3 impl, 0de456c cycle-1 fixes, 562ffef docs, 7ea305f
final review, 4546daa FW-1 auto-retry fix).

### Acknowledged at archival

- Accepted (harness opportunity): SC-2 runtime coverage gap — slash-command
  catalogue arrival requires a live agent; harness covers all mechanically
  testable paths. Manual verification step 4 confirmed at runtime.

