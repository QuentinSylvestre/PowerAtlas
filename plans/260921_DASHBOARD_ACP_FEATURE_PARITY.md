# Dashboard / ACP Feature Parity

> **Date**: 2026-09-21
> **Status**: Draft
> **Scope**: Port all of `/acp`'s features missing from the main dashboard onto the dashboard, except the mobile drill-down view toggle.
> **Estimated effort**: ~2-3 weeks

---

## Intent

### Problem statement & desired outcomes

The dashboard (`index.html`) carries a deliberately scoped-down "live-attach" chat panel — a prior effort (`dashboard-acp-merge`, ~20 commits, no formal plan file) ported transcript viewing and basic prompting, explicitly *not* full parity with `/acp` (see `index.html:280-286`, `:690-700`). Confirmed by direct comparison: the dashboard is missing 9 features `/acp` has — slash-command/skill autocomplete, Queue/Steer send-mode, Stop/cancel, image paste-to-attach, context-window usage indicator, a debug transport log, WebSocket reconnect-on-drop, a workspace/session-id tap-to-copy widget, and the sub-agent/crew read-only panel. (A 10th, the rail free-text filter, was believed missing but is not — see Success criteria.)

Desired outcome: the dashboard becomes functionally equivalent to `/acp` for every in-scope feature, while `/acp` itself keeps full functionality throughout — it remains the sole mobile/remote-reachable surface (the mobile drill-down toggle stays `/acp`-only), and where a feature's logic moves into a shared module, `/acp` is refactored to consume that same module rather than losing or duplicating the feature.

### Success criteria

- **SC1 — Slash-command/skill autocomplete palette**: works identically on the dashboard and on `/acp`, both consuming a new shared module. Known-dead paths in the current `/acp` implementation (`commands_options` is never sent; `_cmdOptionsTimer`/`applyCommandOptions()` are unreachable; `.acp-cmd-placeholder` is styled but never created) are not ported.
- **SC2 — Context-window usage indicator**: works identically on both pages via the shared module.
- **SC3 — Workspace/session-id tap-to-copy widget**: works identically on both pages via the shared module, and copies the **full workspace path** (not the short display name) on both pages — corrects a pre-existing `/acp` bug where the comment at `acp.html:1321-1323` claims full-path copy but the code copies `shortName(cwd)`.
- **SC4 — Debug/transport log panel**: works identically on both pages via the shared module.
- **SC5 — Queue/Steer send-mode toggle + Stop/cancel button**: added to the dashboard as `dash`-prefixed additions (these two are inseparable — `refreshComposerControls()` governs both from the same turn-state).
- **SC6 — Image paste-to-attach** (with the compression ladder): added to the dashboard, integrated into `dashSendPrompt()`.
- **SC7 — WebSocket reconnect-on-drop**: added to the dashboard. Built last — its restore-on-reconnect logic must be scoped to whatever dashboard state exists once SC1-6 and SC8 land.
- **SC8 — Sub-agent/crew read-only panel**: added to the dashboard, including a second independent WebSocket for the panel view (mirrors `/acp`'s existing dual-socket model), with test coverage comparable in density to `/acp`'s existing **~25** dedicated tests (`tests/acp_page.test.mjs:5519-5762`, `:6079-6223`, `:8415-8563` — corrected from an earlier "~35" estimate after direct recount during plan review).
- **Already satisfied, no work needed** — the rail free-text filter: `dashRailFilter`/`dashRailMatches`/`dashRailMatchesFlat` already exist (`index.html:1757-1769`), wired to the dashboard's existing "Search workspaces" box (`index.html:136`), which already filters session rows, not just workspace cards.
- `/acp`'s own existing test suite (`tests/acp_page.test.mjs`) continues to pass **unmodified in its assertions** after the SC1-4 refactor — the refactor must be behavior-preserving on `/acp`.

### Scope boundaries & non-goals

- **Out of scope**: the mobile drill-down view toggle (`#acpViewToggle`) — stays `/acp`-only by design; `/acp` remains the sole mobile/remote-reachable surface.
- **Out of scope**: any backend (`acp.py`/`web.py`) changes, including raising `MAX_CONNECTIONS` — confirmed unnecessary for the core protocol. Three independent research passes traced WS frame routing (`_registry.broadcast()`, `acp.py:2099-2103`), origin/token gating (`web.py:1272-1330`), and image/command validation, and found zero page-aware branching anywhere. Every frame type these features need already exists in `CLIENT_TYPES`/`SERVER_TYPES` (`acp.py:161-197`) and already reaches the dashboard's socket today. **Exception carved out during plan review**: SC8's second WebSocket must handle a client-side `too_many_connections`-style rejection gracefully (see Phase 5) — this is client-only error handling, not a backend change.
- **Out of scope**: fixing the `_kiro.dev/commands/execute`/`commands/options` kiro-cli RPC brokenness noted in `plans/done/260909-1127_ACP_V3_PRODUCTION_HARDENING.md` — pre-existing, separately tracked, not touched here.
- **Out of scope**: migrating `.acp-compaction-details`/`.acp-compaction-recap` CSS (`acp.html:548-594`) — inline-only today, not part of any Success Criteria here, and deliberately left as-is (see Phase 3's AGENTS.md edit, revised during plan review to name this as the sole remaining documented exception rather than claiming no exception remains).
- **Out of scope**: any new domain concepts, persistent data-model changes, or third-party/external dependencies.
- **Narrow in-scope exception**: one bug fix in `acp.html` itself (the sid/copy full-path behavior, SC3) — explicitly requested by the user, not a general `acp.html` bugfix sweep.

---

## 1) Current State

- **`acp.html`'s script is one IIFE**: `(function () {` at `acp.html:674` to `})();` at `acp.html:6516`. Every module-level `var`/`function` inside is private to that closure; only `send`, `logLine`, `removeAllCrewPanels`, `closeSubagentView` are explicitly exposed on `window` (`acp.html:805-815`) so `transcript-renderer.js` can reach them via `typeof X === 'function'` guards. **Load-bearing consequence for this plan**: any function moved out of this IIFE into a non-IIFE shared module loses access to every other IIFE-private variable it used to read as a free variable (e.g. `sessionId`, `replaying`, `turnActive`) — those must become explicit DI parameters in the new module, not bare references. This bit Phase 1 in an earlier draft of this plan (see Phase 1's DI design below) and is the single most important structural fact for every phase that ports code out of `acp.html`.
- **`index.html`'s script carries no IIFE** — deliberate (comment at `index.html:709-710`) so `transcript-renderer.js` can reach `send()` as a true global. `dashConnect`/`dashHandle`/all `dashRail*` are plain top-level declarations. **`index.html` already has its own `dashRailStored`/`dashRailStore` functions** (`index.html:1778-1779`) — distinct names from `acp.html`'s `railStored`/`railStore`, so no naming collision exists between them (an earlier draft of this plan incorrectly flagged a collision here; corrected during review).
- **`src/power_atlas/static/transcript-renderer.js` is the existing shared-module precedent**: not an IIFE, plain top-level script, every declaration becomes a `window` global. Host pages call `initTranscriptDom({transcriptEl, promptNavEl, promptUpBtn, promptDownBtn})` once (`acp.html:787-792`); the module's functions (`renderTranscriptFrame`, `clearTranscript`, etc.) are then called as bare globals from inside the host's own script, IIFE or not — this already works today exactly the way this plan needs it to for the new shared module. **`transcript-renderer.js` itself owns the `removeAllCrewPanels`/`closeSubagentView` guard mechanism** (`transcript-renderer.js:1899-1900`, called from `clearTranscript()`) — these two globals are assigned by whichever host page defines the underlying functions (`acp.html:814-815` today; `index.html`'s Phase 5 code after this plan ships), never by `composer-chrome.js`, which has no involvement in crew/sub-agent logic.
- **Served script-tag count on `/acp` is currently 4** (`tests/test_web.py:4163-4186`, `TestAcpContentSecurityPolicy.test_the_header_nonce_is_the_one_on_the_page`, assertions at `:4176-4181`): `htmx.min.js` (from `base.html`), the inline nonce-carrying IIFE, `prism.js`, `transcript-renderer.js`. The test hardcodes `assert len(tags) == 4, "a script tag was added without a nonce: %s" % tags`. **This plan adds a 5th script tag** (`composer-chrome.js`) — the count assertion must be updated deliberately in the same phase, or the whole `/acp` test suite fails.
- **`dashHandle()` (`index.html:883-1011`) currently drops, with no case**: `commands`, `skills`, `steer_ack`, `steer_sent`, `steer_status`, `subagents`, `history_truncated`, `compaction`, `commands_options_result`, `commands_execute_result`, `title`, **and `agent_died`**. Critically, **any `meta` frame that isn't a turn marker** is dropped too — the `if` only checks `payload.turn === 'start'|'end'` (`index.html:945-946`) — which silently discards the `connected` meta (`maxPromptImages`/`maxPromptImageBytes`, needed by SC6) and the `contextPercent` meta (needed by SC2) that already arrive on the dashboard's socket today. **`agent_died` specifically has no phase assigned to it in an earlier draft of this plan** — corrected below; it is wired in Phase 1 (baseline reset) and extended by Phases 3 and 5 as their own state comes into existence.
- **CSS location**: SC1 and SC5's entire CSS lives only in `acp.html`'s own page-scoped `<style nonce="{{ csp_nonce }}">` block (`acp.html:426-652`), not in `style.css` at all — this block also contains `.acp-compaction-details`/`.acp-compaction-recap` (`acp.html:548-594`), which is out of this plan's scope (see Scope boundaries). Most other in-scope features' CSS (context, sid/copy, tray, subpanel, log body) is already class-based and page-agnostic in `style.css`; confirmed ID-gated exceptions: `#acpLogToggle[aria-expanded="false"]`/`:hover` (`style.css:1533-1534`), `#acpSend`/`#acpStop` hover/disabled rules (`style.css:1498-1515`).
- **`MAX_CONNECTIONS = 8` (`acp.py:282`) is a flat, process-wide, global cap** — `len(_registry.connections) >= MAX_CONNECTIONS` (`acp.py:5081`) — **not** per-viewer or per-device (an earlier draft of this plan mischaracterized it as per-viewer; corrected during review, confirmed independently by three reviewers). It already covers `/acp`'s own main+sub-agent dual-socket case today, and already applies across every local and remote/mobile socket combined. SC8's second socket from the dashboard draws from this same shared, global budget alongside every other connection on the machine — see Risk Assessment and Phase 5 for the resulting mitigation.
- **`AGENTS.md:7`'s CSS-location note** currently reads (in relevant part): *"All JS for `/acp` is inline in `acp.html`... Exception: a few features keep their CSS inline in `acp.html`'s own `<style>` block instead — confirmed for the send-mode picker (`.acp-mode-*`)"* — both claims go stale as this plan proceeds (see §8 Documentation Updates).
- **11+ existing `_dash*` module-level state variables** already govern dashboard turn/session/picker state: `_dashWs`, `_viewingSid`, `_dashAttachedSid`, `_dashOrigin`, `_dashSent`, `_dashTurnActive`, `_dashLoadingSid`, `_dashPendingSend`, `_dashPendingCreate`, plus picker-only state. SC5/SC7/SC8's new code threads into this existing machine rather than inventing parallel state — **with one deliberate exception**: Queue mode gets its own dedicated state (`_dashQueuedPrompt`/`_dashQueuedPromptSession`), explicitly NOT sharing `_dashPendingSend`, since the two are flushed by different triggers (see Phase 3).

## 2) Goal

Bring the dashboard to feature parity with `/acp` for 8 in-scope features by extracting 4 self-contained features into a new shared module (`composer-chrome.js`) that both pages consume — refactoring `/acp` onto it too — and adding the other 4, more entangled features directly to the dashboard as `dash`-prefixed code, while fixing a small pre-existing bug on both pages and widening the dashboard's WS frame handling to stop dropping data it already receives.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Extraction vs. duplication strategy | Hybrid, decided per feature by entanglement with `acp.html`'s page-lifecycle state | Extract everything (forces a bigger state-DI framework nothing justifies); duplicate everything (leaves cheap, self-contained logic needlessly copied) | Grounded in code structure: `transcript-renderer.js`'s existing precedent only works because its functions touch DOM refs + 1-2 primitives; SC2/SC3/SC4/SC1 fit that shape, SC5/SC7/SC8 don't |
| Sub-agent panel (SC8) scope | Build it in this pass, full parity target | Defer out of scope given its cost (second WebSocket, ~25-test parity target, deepest coupling) | User: "build it, this is critical" |
| SC1/SC5 CSS location | Migrate to `style.css` | Give `index.html` its own inline `<style>` block (mechanically possible — `index.html` has no CSP/nonce — but diverges from precedent) | Matches the `.acp-taskmode-*` precedent (`plans/done/260919-1551_DASHBOARD_ACP_NEW_SESSION_PICKER.md`), single source of truth |
| Rail free-text filter (was SC8 in exploration, now dropped) | No new work | Add a dedicated in-rail search input matching `/acp`'s placement | `dashRailFilter`/`dashRailMatches` (`index.html:1757-1769`) already deliver identical behavior via the existing "Search workspaces" box |
| Sid/copy bug (SC3) | Implement full-path-copy behavior AND fix the same bug in `acp.html` | Replicate the buggy short-name-copy behavior for strict as-is parity | User: "b and fix the bug in acp.html" |
| Slash-palette dead paths (SC1) | Skip when extracting (`commands_options` send, `_cmdOptionsTimer`, `applyCommandOptions()`, `.acp-cmd-placeholder`) | Port them for perfect structural fidelity | No value in propagating unreachable code; `plans/done/260909-1127_ACP_V3_PRODUCTION_HARDENING.md:865` already documents these as known-dead |
| Shared-module organization | One new file (`composer-chrome.js`) holding all 4 extracted features, each with its own `initXxxDom()` | Four separate files; fold into `transcript-renderer.js` | Individually small (log panel ~40 lines, sid/copy ~70); one file keeps "shared composer chrome" together the way `transcript-renderer.js` keeps "shared transcript rendering" together |
| Test depth | Differentiate: extracted features (SC1-4) get wiring-level tests only; duplicated features (SC5/SC7/SC8) get coverage comparable in density to `/acp`'s existing tests for the same feature | Uniform test density (match `/acp` everywhere, or a uniform lighter touch everywhere) | Extracted features' core logic is the exact code `/acp`'s existing suite already exercises; duplicated features are genuinely new code that could drift or misport |
| `acp.html` refactor scope (SC1-4) | Refactor `acp.html` to consume the new shared module — true single-sourcing | Leave `acp.html`'s inline implementations untouched, dashboard-only consumer | User: "acp.html remains to provide remote functionality only, but features will remain and be maintaied" — leaving `acp.html` untouched would make the extract/duplicate distinction cosmetic and reintroduce the duplication extraction was chosen to avoid |
| DI shape for page-lifecycle-reading extracted functions | Live accessor **functions** (`getSessionId`, `isReplaying`, etc.), not raw values, passed into each `initXxxDom()` | Pass raw values at init time only | A raw value snapshot goes stale the moment a session switches; an accessor re-reads the host page's current state on every call, matching how Phase 2's palette DI (`cmdGetSessionId`/`cmdGetTurnActive`) already had to be designed — added during plan review after a reviewer found Phase 1's original "as-is" port would `ReferenceError` on both pages without this |

## 4) External Dependencies & Costs

### Required external changes

None — no cloud resources, CI/CD, IAM, DNS, secrets, or third-party services are touched. All rows removed.

### Cost impact

None. Purely local frontend/static-asset work; no new recurring cost. SC8's second WebSocket is loopback-only and counts against the existing `MAX_CONNECTIONS=8` **global** budget already in production use — not a new cost, but see Risk Assessment and Phase 5 for the resulting mitigation (client-side graceful rejection, not a cap increase).

## 5) Implementation Phases

**Sequencing note (no `[P:N]` annotations anywhere in this plan)**: every phase below modifies `index.html`, and the Parallel Phase Annotation rule requires non-overlapping file scopes for any `[P:N]` group. Because `index.html` is common to all 6 phases, none are parallel-eligible — this plan is inherently sequential. Phase order also encodes real dependencies: Phase 1 creates `composer-chrome.js` (Phase 2 extends it); Phase 6 (reconnect) must be last since its restore logic scopes to whatever state Phases 1-5 leave behind.

**`agent_died` handling is threaded across three phases, not owned by one**: Phase 1 adds a baseline `agent_died` case to `dashHandle()` (reset turn-active state, disable the composer with an explanatory message) since that state exists from the start. Phase 3 extends it to also restore/clear Queue/Steer state. Phase 5 extends it again to close any open sub-agent panel and clear crew state. Each phase's exit criteria call this out explicitly so the extension isn't missed.

**Vertical phasing note on Phase 1**: bundles three independently-shippable, independently-testable features (SC2, SC3, SC4) plus the one foundational step they share (creating `composer-chrome.js`, the `initTranscriptDom`-style DI scaffold, and the CSP script-tag-count fix). Each of the three remains a complete vertical slice on its own — bundling avoids touching the same foundational lines three separate times for features this small (15-70 lines each).

### Phase 1: Shared-module foundation + context indicator (SC2) + sid/copy widget (SC3) + debug log (SC4) [QA]

**Goal**: Create `src/power_atlas/static/composer-chrome.js`, port SC2/SC3/SC4 from `acp.html` into it — with explicit DI accessors for every piece of IIFE-private state they read, not just DOM refs — wire both `acp.html` and `index.html` to consume it, fix the sid/copy bug (SC3) on both pages, widen `dashHandle()`'s `meta` handling, add a baseline `agent_died` case, and land the CSP/script-count test fixes this introduces.

**File scope**: `src/power_atlas/static/composer-chrome.js` (new), `src/power_atlas/templates/acp.html`, `src/power_atlas/templates/index.html`, `tests/test_web.py`, `tests/acp_page.test.mjs`, `AGENTS.md`.

**1a. Create `composer-chrome.js`** — structural pattern mirrors `transcript-renderer.js` exactly: plain top-level script, no IIFE, every declaration becomes a `window`-implicit global (matches `transcript-renderer.js`'s own `toolRows`/`transcriptEl` precedent). Three DI init functions, one per feature:

```js
// Context-window usage indicator. No page-lifecycle reads needed —
// setContext(percent) only touches its own 3 DOM refs.
function initContextDom(refs) {
  contextEl = refs.contextEl;
  contextFill = refs.contextFill;
  contextLabel = refs.contextLabel;
}

// Sid/copy widget. `getSessionId` is REQUIRED, not optional: the ported
// renderSidLabel()/click handlers read `sessionId` as a free variable in
// acp.html today (acp.html:851,1287,1297,1309,1319,1323) — that variable
// does not exist once this code moves out of acp.html's IIFE. Without this
// accessor every call throws ReferenceError on both pages.
function initSidCopyDom(refs) {
  sidEl = refs.sidEl;
  copyBtn = refs.copyBtn;
  sidGetSessionId = refs.getSessionId; // () => current session id or null
}

// Debug/transport log panel. `isReplaying` is REQUIRED for the same reason:
// logLine() reads `replaying` as a free variable today (acp.html:876,1160)
// to suppress logging of replayed history. Before wiring this, check
// whether index.html's renderTranscriptHistory() / transcript-renderer.js
// already exposes an equivalent replay-in-progress flag — reuse it if so;
// otherwise index.html must introduce its own `_dashReplaying` boolean, set
# true for the duration of history-frame processing and false otherwise,
// mirroring acp.html's own `replaying` variable.
function initLogDom(refs) {
  logEl = refs.logEl;
  logToggle = refs.logToggle;
  logIsReplaying = refs.isReplaying; // () => bool
}
```

Port the following functions, **adapted to call the new accessors instead of the free variables they used inside `acp.html`'s IIFE** — this is not a verbatim copy for these three (unlike Phase 2's palette, which only needs accessors for session id and turn-active; these two additionally need the `isReplaying` accessor):
- `setContext(percent)` — currently `acp.html:1395-1409`; no free-variable reads, ports verbatim.
- `shortName(cwdPath)`, `renderSidLabel()`, the `sidEl`/`copyBtn` click handlers — currently `acp.html:1275-1345`; replace every bare `sessionId` read with `sidGetSessionId()`.
- `logLine(kind, text)`, `setLogOpen(open)`, `DEBUG_LOG_KEY` — currently `acp.html:1159-1197`; replace every bare `replaying` read with `logIsReplaying()`.
- `railStored`/`railStore` (the `localStorage` try/catch wrappers, `acp.html:2896-2906`) — both `SEND_MODE_KEY` (Phase 3) and `DEBUG_LOG_KEY` (this phase) depend on them; move them into `composer-chrome.js` too since they're now shared infrastructure, not `acp.html`-private. **No naming collision exists with `index.html`** — `index.html`'s existing rail-persistence functions are named `dashRailStored`/`dashRailStore` (`index.html:1778-1779`), a distinct name (an earlier draft of this plan incorrectly flagged a `railStored`/`railStore` collision here; corrected during review). `composer-chrome.js`'s `railStored`/`railStore` become new, additional globals on `index.html` — reconcile whether `index.html`'s picker code (which already calls `dashRailStored`/`dashRailStore`) should be left as-is (two co-existing persistence helpers, acceptable since they're genuinely different call sites) or migrated to the new shared names; either is fine, document which was chosen.
- **Generalize the collision check**: before wiring `index.html`, grep it for every new unprefixed global `composer-chrome.js` introduces — `contextEl`, `contextFill`, `contextLabel`, `sidEl`, `copyBtn`, `sidGetSessionId`, `logEl`, `logToggle`, `logIsReplaying`, `sidWorkspace`, `_sessionCwd`, `sidRevealed`, `DEBUG_LOG_KEY`, `railStored`, `railStore`, `setContext`, `shortName`, `renderSidLabel`, `logLine`, `setLogOpen` — not just `railStored`/`railStore`. Document the result even if (as expected) no collision is found.

**SC3 bug fix applied during the port**: `renderSidLabel()`'s copy-button click handler currently computes `value = showId ? sessionId : (sidWorkspace || sessionId)` (`acp.html:1318-1344`), where `sidWorkspace` is always `shortName(cwd)`. Change this to `value = showId ? sidGetSessionId() : (_sessionCwd || sidGetSessionId())` — copying the full cwd (`_sessionCwd`, the variable already tracked alongside `sidWorkspace` at every assignment site) instead of the short display name. **The displayed label is unchanged** — `sidEl.textContent` continues to show the short name via `sidWorkspace`; only the copied clipboard value changes. `_sessionCwd` becomes part of `composer-chrome.js`'s state alongside `sidWorkspace`/`sidRevealed`.

**1b. Wire `acp.html`**: add `<script nonce="{{ csp_nonce }}" src="/static/composer-chrome.js"></script>` immediately after the existing `transcript-renderer.js` tag (`acp.html:654`), before the inline script block. Delete the now-superseded local definitions of `setContext`, `shortName`, `renderSidLabel`, `logLine`, `setLogOpen`, `railStored`, `railStore` (and the `sidWorkspace`/`_sessionCwd`/`sidRevealed`/`DEBUG_LOG_KEY` state they own) from `acp.html`'s IIFE. Every existing bare call site inside `acp.html`'s `handle()`, `releaseSession()`, etc. (e.g. `acp.html:5310`, `:5330`, `:5640`, `:5645`, and the `releaseSession()` call sites around `acp.html:1447`-`1498` — verify exact current line numbers before editing, since this plan's own citations may have drifted by a line or two from independent research passes) needs **no changes** — they resolve to `composer-chrome.js`'s globals via the scope chain, exactly as `renderTranscriptFrame`/`clearTranscript` already do today. Add `initContextDom({...})`/`initSidCopyDom({sidEl, copyBtn, getSessionId: function(){ return sessionId; }})`/`initLogDom({logEl, logToggle, isReplaying: function(){ return replaying; }})` calls near the existing `initTranscriptDom({...})` call (`acp.html:787-792`).

**Remove the now-redundant `window.logLine = logLine;` line and its comment** (`acp.html:793-806` region) — `logLine` is no longer a private IIFE function that needs re-exposing; it's already a `composer-chrome.js` global, reachable the same way `renderTranscriptFrame` already is. Leave `window.send = send` as-is (still genuinely page-specific).

**1c. Wire `index.html`**:
- Markup: add a context-indicator span in the topbar cluster mirroring `acp.html:20-27` (dash-prefixed ids: `dashContext`/`dashContextFill`/`dashContextLabel`), a debug-log toggle + panel mirroring `acp.html:205-207`/`:326-327` (`dashLog`/`dashLogToggle`), and a sid/copy widget mirroring `acp.html:171-196` (`dashSid`/`dashCopy`) — placed in the transcript panel's header (`index.html:287-298`, alongside the existing "Transcript" label and `#dashCloseSession` button), since that's the dashboard's closest analogue to `acp.html`'s toolbar.
- Add `<script src="/static/composer-chrome.js"></script>` alongside `index.html`'s existing script includes, using the same conditional-nonce pattern already used for `transcript-renderer.js` (`index.html:342`, `{% if csp_nonce %} nonce="{{ csp_nonce }}"{% endif %}`) — harmless today since `index()` never sets `csp_nonce` (`web.py:1371-1395`), but keeps the two pages' script-tag markup consistent and future-proofs against `index.html` ever gaining a nonce-based CSP.
- Introduce `_dashReplaying` (or reuse an existing equivalent flag if one is found per the `initLogDom` comment above) and call `initContextDom({...})`/`initSidCopyDom({..., getSessionId: function(){ return _dashAttachedSid; }})`/`initLogDom({..., isReplaying: function(){ return _dashReplaying; }})` once at page load, passing the new `dash`-prefixed refs.
- In `dashHandle()`, widen the `meta` branch (`index.html:945-946`) from only checking `payload.turn` to also calling `setContext(payload.contextPercent)` when `'contextPercent' in payload`, and storing `payload.maxPromptImages`/`payload.maxPromptImageBytes` (from the `connected` meta) into dashboard-local variables Phase 4 (SC6) will consume — do not drop the existing turn-start/turn-end branches, only add cases alongside them.
- **Add a baseline `agent_died` case to `dashHandle()`**: reset `_dashTurnActive = false`, disable the composer, and show an explanatory message ("This session's agent process ended."), mirroring the turn/composer-reset portion of `acp.html`'s `agent_died` handling (`acp.html:5617-5662`) — the steer/crew-restoration portions of that handler are added by Phases 3 and 5 respectively, since their state doesn't exist yet at this point in the plan.
- Call `renderSidLabel()`-equivalent state updates (`sidWorkspace`/`_sessionCwd` assignment) wherever the dashboard currently tracks `cwd` per attached session (`dashMaybeAttach()`, `index.html:799-824`) and wherever `session`/`agent_died`/session-release paths already exist on the dashboard.
- Call `logLine(...)` from `index.html`'s own `send()` (`index.html:714-718`) and from `dashConnect()`'s `onmessage`/`onopen`/`onclose` (`index.html:731-738`) — this is new; the dashboard's `send()` currently has no log side effect at all (a confirmed divergence from `acp.html`'s `send()`, which always calls `logLine`). Without this, the newly-added log panel would show nothing for the dashboard's own traffic.

**1d. CSP/script-count test fixes** (both in this phase, since `composer-chrome.js`'s script tag is first introduced here):
- `tests/test_web.py:4163-4186` (`TestAcpContentSecurityPolicy.test_the_header_nonce_is_the_one_on_the_page`): add a named assertion for `composer-chrome.js` mirroring the existing `transcript-renderer.js` one at `:4176-4179` (`assert ('<script nonce="%s" src="/static/composer-chrome.js">' % nonce) in resp.text`), and update the count assertion at `:4180-4181` from `assert len(tags) == 4, ...` to `== 5`.
- `tests/acp_page.test.mjs:634-636` and `:1188-1193`: both contain stale prose ("three script elements, not two" / "two script elements") — the real served count on `/acp` is already 4 today (both comments already understated it before this plan) and will become 5 after Phase 1 ships. **Correct both comments to state five script elements** (not "five"/"four" respectively — both describe the same total count, just via different phrasing today; verify each test's own counting basis — `loadPage()`'s served-page total at `:1188-1193` vs. the template-render harness's own count at `:634-636` — before editing, since they may not be counting identically). The underlying functional checks (exactly one inline script, every external script nonce-tagged) are count-agnostic and need no logic change.
- Add `composer-chrome.js` to the VM-sandbox test harness's script-loading step (mirroring how `transcript-renderer.js` is already loaded, `tests/acp_page.test.mjs:51-64`/`:958-960`) — without this, every existing test that exercises a function now living in `composer-chrome.js` (context, sid/copy, log) will `ReferenceError` at first call.

**1e. AGENTS.md**: correct the claim "All JS for `/acp` is inline in `acp.html`" (`AGENTS.md:7`) to note that composer-chrome.js is now a shared static module loaded via `<script src>`, following the same "no restart needed, hard-reload picks up changes" rule already stated for `style.css`. Do NOT yet touch the "Exception" clause about `.acp-mode-*` — it remains accurate until Phase 3 migrates that CSS too (see Phase 3, and §8 Documentation Updates).

**Exit criteria**:
- [ ] `composer-chrome.js` created; `setContext`/`renderSidLabel`/`shortName`/`logLine`/`setLogOpen`/`railStored`/`railStore` live there, deleted from `acp.html`'s IIFE.
- [ ] `initSidCopyDom`/`initLogDom` accept and use `getSessionId`/`isReplaying` accessor functions — no bare `sessionId`/`replaying` reads remain in the ported code; confirmed by grepping the new file for both identifiers and finding zero unqualified references.
- [ ] Collision check performed against every new global name `composer-chrome.js` introduces (full list above), not just `railStored`/`railStore`; result documented.
- [ ] SC3 bug fix applied: copy button copies `_sessionCwd` (full path) via `sidGetSessionId()`/`_sessionCwd`, display still shows the short name, on both `acp.html` (refactored) and `index.html` (new).
- [ ] `acp.html` and `index.html` both load `composer-chrome.js` via `<script src>` (with matching conditional-nonce pattern) and call all three `initXxxDom()` functions with correctly-bound accessors.
- [ ] The now-redundant `window.logLine = logLine;` line and comment removed from `acp.html`.
- [ ] `dashHandle()`'s `meta` branch widened to handle `contextPercent` and `connected`/`maxPromptImages`/`maxPromptImageBytes` without removing existing turn-start/turn-end handling.
- [ ] `dashHandle()` has a baseline `agent_died` case (turn/composer reset); documented as extended by Phases 3 and 5.
- [ ] `index.html`'s `send()` and `dashConnect()` call `logLine(...)`, matching `acp.html`'s existing pattern.
- [ ] `_dashReplaying` (or an equivalent reused flag) exists and is threaded into `initLogDom`'s `isReplaying` accessor.
- [ ] `tests/test_web.py`'s CSP script-count assertion updated to 5 with a new named `composer-chrome.js` assertion; full `pytest tests/test_web.py -k TestAcpContentSecurityPolicy` passes.
- [ ] `tests/acp_page.test.mjs:634-636`/`:1188-1193` comments corrected to state five script elements (both), verified against each test's own actual counting basis.
- [ ] `composer-chrome.js` added to the VM-sandbox test harness's script-load step.
- [ ] `/acp`'s full existing test suite (`node tests/acp_page.test.mjs`) passes unmodified in its assertions — confirms the refactor is behavior-preserving.
- [ ] New wiring-level tests added for the dashboard's consumption of `initContextDom`/`initSidCopyDom`/`initLogDom`, using the region-extraction harness pattern (`tests/acp_page.test.mjs:10343-10482`) — including one that feeds a synthetic `meta` frame with `contextPercent` into `dashHandle()` and asserts `setContext` is called with the correct value, not just that DOM refs are wired.
- [ ] `AGENTS.md:7`'s "All JS for `/acp` is inline" claim corrected.
- [ ] Live QA: context bar, sid/copy widget (copies full path), debug log panel, and an `agent_died` frame (simulated) all work identically on `/acp` and `/`, verified against the same live/held session.

### Phase 2: Slash-command/skill autocomplete palette (SC1) [QA]

**Goal**: Extend `composer-chrome.js` with the slash-command palette, refactor `acp.html` to consume it, add it to the dashboard, migrate its CSS to `style.css`, and drop the known-dead paths.

**File scope**: `src/power_atlas/static/composer-chrome.js`, `src/power_atlas/templates/acp.html`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`, `tests/acp_page.test.mjs`.

**2a. Extend `composer-chrome.js`**: add `initCommandPaletteDom(refs)`:

```js
function initCommandPaletteDom(refs) {
  cmdDropdownEl = refs.cmdDropdownEl;
  cmdPromptInput = refs.promptInput;
  cmdGetSessionId = refs.getSessionId;   // () => current session id or null
  cmdGetTurnActive = refs.getTurnActive; // () => bool
  cmdSend = refs.send;                   // the host page's send() function
}
```

Live accessor functions, not raw values, since the answer changes as sessions switch (`acp.html`'s `sessionId`/`turnActive` are page-owned mutable state; the dashboard's equivalents are `_dashAttachedSid`/`_dashTurnActive`). `acp.html` passes `function(){ return sessionId; }`/`function(){ return turnActive; }`; `index.html` passes `function(){ return _dashAttachedSid; }`/`function(){ return _dashTurnActive; }`.

Port **as-is**, dropping the dead paths named in §3 Design Decisions: `sessionCommands`/`sessionSkills`/`_cmdSelectedIndex`/`MAX_CMD_PARTIAL_CHARS` state and `isCommandDropdownVisible`/`showCommandDropdown`/`renderCommandDropdown`/`updateCommandSelection`/`hideCommandDropdown`/`confirmCommandSelection`/`moveCommandSelection` (currently `acp.html:1961-2188`). **Do not port** `_cmdOptionsTimer`, `applyCommandOptions()`, or any reference to sending/handling `commands_options`/`commands_options_result` — the client never sends `commands_options` today and the RPC is documented broken (`plans/done/260909-1127_ACP_V3_PRODUCTION_HARDENING.md:865`); `commands_execute`/`commands_execute_result` (the reachable, working path) are ported normally.

Expose `setSessionCommands(list)`/`setSessionSkills(list)`/`resetCommandPalette()` as the module's public setters, called by each host page's own frame handler on `commands`/`skills`/`session` frames.

**2b. Wire `acp.html`**: delete the ported functions/state from the IIFE (same "bare call sites keep working via scope chain" mechanism as Phase 1); replace the `handle()` cases for `commands`/`skills`/`commands_execute_result` (currently `acp.html:5517-5601`) with calls to `setSessionCommands`/`setSessionSkills`/the module's result handler; the `/` keydown intercept and `input` listener (`acp.html:6339-6416`) call `showCommandDropdown` unchanged (bare global resolution).

**2c. Wire `index.html`**: add `#dashCmdDropdown` markup mirroring `acp.html:247-263`, positioned inside `#dashComposer` above `#dashPromptInput` the same way. Add `commands`/`skills`/`commands_execute_result` cases to `dashHandle()` calling `setSessionCommands`/`setSessionSkills`/the result handler. Wire the `/` keydown intercept and `input` listener on `#dashPromptInput`, gated on `cmdGetTurnActive()` the same way `acp.html`'s is gated on `!turnActive`. Add the `commands_execute` send call on selection (mirrors `acp.html:2171`).

**2d. CSS migration**: move `#acpCmdDropdown`/`.acp-cmd-*` rules (`acp.html:427-547`) from `acp.html`'s inline `<style nonce>` block to `style.css`, **excluding the `.acp-cmd-placeholder` rules (`acp.html:521-531`)** — these style the never-created placeholder row (see SC1's dead-path exclusion) and should be deleted outright, not migrated, to avoid shipping unused CSS for a feature that isn't implemented. Do not rename any migrated selector — both pages must reference identical class/id-derived names (the dashboard markup uses `#dashCmdDropdown` for its own element but shares the `.acp-cmd-*` class rules, matching how `.acp-taskmode-*` is already shared between both pages' differently-`id`'d pickers).

**Exit criteria**:
- [ ] `composer-chrome.js` carries the palette; dead paths (`commands_options`, `_cmdOptionsTimer`, `applyCommandOptions`, `.acp-cmd-placeholder`) not ported — confirmed by grep.
- [ ] `acp.html` refactored, its own existing ~20-23 palette-related tests in `tests/acp_page.test.mjs:8692-9446` still pass unmodified.
- [ ] Dashboard palette functional: `/` opens it, `commands`/`skills` frames populate it, selection sends `commands_execute`.
- [ ] `.acp-cmd-*` CSS (excluding `.acp-cmd-placeholder`) lives in `style.css`; `acp.html`'s inline `<style>` block no longer contains any of it.
- [ ] Wiring-level tests added for the dashboard side (region-extraction harness).
- [ ] Live QA: palette behaves identically on `/acp` and `/`, including keyboard nav and mouse selection.

### Phase 3: Queue/Steer send-mode toggle + Stop/cancel button (SC5) [QA]

**Goal**: Add `dash`-prefixed Queue/Steer and Stop controls to `index.html`, threaded through the dashboard's existing turn-state variables but with Queue mode's own dedicated pending-prompt state. Migrate this feature's CSS to `style.css`, revise (not remove) the AGENTS.md CSS-exception clause, and extend the `agent_died`/`error` handling this phase's state now requires.

**File scope**: `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`, `AGENTS.md`, `tests/acp_page.test.mjs`.

**3a. Markup**: add `#dashStop`, `#dashQueueSteer`/`#dashModeToggle`/`#dashModeMenu`/`#dashModeOptSteer`/`#dashModeOptQueue`/`#dashSendMode` mirroring `acp.html:278-311`, inside `#dashComposer`'s composer row alongside the existing `#dashSendBtn`.

**3b. JS** (new, `dash`-prefixed, `index.html`-only — this is a duplicated, not extracted, feature per §3 Design Decisions): port the governing logic from `acp.html`:
- `dashRefreshComposerControls()` mirroring `refreshComposerControls()` (`acp.html:1361-1372`), reading `_dashTurnActive` and `dashPromptInput.value` instead of `turnActive`/`promptInput.value`.
- `dashApplySendMode(mode)` mirroring `_applySendMode` (`acp.html:762-779`), persisted via `railStore`/`railStored` (now in `composer-chrome.js` after Phase 1 — reuse, do not reimplement) under its own dashboard-scoped key (e.g. `pa_dash_send_mode`, matching the existing `pa_dash_*` prefix convention already used for rail-stored keys, `index.html:1776`).
- `dashStopBtn` click sends `cancel` (mirrors `acp.html:5915-5933`).
- **Introduce dedicated `_dashQueuedPrompt`/`_dashQueuedPromptSession` state (mirroring `acp.html`'s own `queuedPrompt`/`queuedPromptSession`) — do NOT reuse `_dashPendingSend` for this.** `_dashPendingSend` is the dashboard's pre-existing lazy-load-pending mechanism (flushed by the `history` branch, `index.html:934-940`, for a session that hadn't finished attaching yet); Queue mode is a different mechanism (flushed by `meta turn:end`) that happens to also hold prompt text awaiting a later send. Sharing one variable between both risks a reconnect-triggered `history` frame (from Phase 6's re-subscribe) firing a prompt meant to wait for turn:end, or vice versa. `dashSendModeBtn` click: queue path stores text in `_dashQueuedPrompt`/`_dashQueuedPromptSession` + clears textarea; steer path sets a pending-steer flag, sends `steer` with `{message: text}` (exact field name required by `_handle_steer`, `acp.py:5857-5919`) and disables controls until `steer_ack`.
- Add `steer_ack`/`steer_sent`/`steer_status` cases to `dashHandle()`, and extend the existing `meta turn:end`/`turn:start` branches (`index.html:945-946`) to also flush `_dashQueuedPrompt` (if set) and toggle Queue/Steer/Stop visibility via `dashRefreshComposerControls()`.
- **Extend `dashHandle()`'s generic `error` case** (`index.html:984-1010`) to also restore/re-enable the composer when the error corresponds to a refused `steer` send — mirroring `acp.html`'s multiple restoration points (`onclose`, `error` at `acp.html:5671-5681`, and `agent_died`) rather than relying on `steer_ack` alone, which never arrives for a send refused before the agent acknowledged it.
- **Extend Phase 1's baseline `agent_died` case**: also restore any pending steer text into the textarea and re-enable `dashPromptInput`/`dashSendModeBtn`/`dashModeToggle`, mirroring `acp.html`'s `agent_died` handler (`acp.html:5617-5662`).
- Thread `_dashPendingSend`/`_dashTurnActive`/`_dashAttachedSid` — do not introduce parallel state beyond the deliberate `_dashQueuedPrompt`/`_dashQueuedPromptSession` split above.

**3c. Security — agent-controlled text rendering**: the `steer_status` three-state echo and the `steer_sent` dimmed-band message both render agent/session-controlled text. Use `createElement`+`textContent` exclusively — never `innerHTML` — mirroring `acp.html`'s `setSteerStatus` (`acp.html:1412-1414`, which carries an explicit `SECURITY: MUST use textContent` comment) and its `addMessage('steer', ...)` pattern. Do **not** reuse `index.html`'s existing `_escHtml`+`innerHTML` idiom (`index.html:1537` and 7+ other sites) for this content — that pattern is for locally-generated UI strings, not session-echoed text, and `acp.html`'s no-innerHTML rule exists specifically because this class of content is attacker-influenced. Port the specific `/acp` injection-safety test(s) confirming this (pattern at `tests/acp_page.test.mjs:10050`, and the related checks around `:10141`/`:10184`) as required parity tests for the new dashboard code — a generic "steer works" test is not sufficient coverage for this requirement.

**3d. CSS migration**: move `.acp-queue-steer`/`.acp-mode-toggle`/`.acp-mode-menu`/`.acp-mode-option`/`.sr-only` (`acp.html:596-652`) from `acp.html`'s inline block to `style.css`.

**3e. AGENTS.md**: with this migration, the `.acp-mode-*` exception no longer applies (SC1's `.acp-cmd-*` was migrated in Phase 2). **`.acp-compaction-details`/`.acp-compaction-recap` (`acp.html:548-594`) remains inline-only and out of this plan's scope** (see Scope boundaries) — it is now the sole surviving exception. **Revise, don't remove, `AGENTS.md:7`'s "Exception" clause**: update it to name `.acp-compaction-details`/`.acp-compaction-recap` as the confirmed inline-CSS exception in place of `.acp-mode-*`, following the same editing precedent `plans/done/260919-1551_DASHBOARD_ACP_NEW_SESSION_PICKER.md` Phase 1 set when it updated this line for `.acp-taskmode-*` — but do not claim no exception remains, since one genuinely does.

**Exit criteria**:
- [ ] Queue/Steer/Stop functional on the dashboard; Queue mode uses its own `_dashQueuedPrompt`/`_dashQueuedPromptSession` state, not `_dashPendingSend`.
- [ ] `dashHandle()`'s `error` case restores steer-pending composer state on a refused steer send.
- [ ] `agent_died` handling extended to restore steer state (builds on Phase 1's baseline case).
- [ ] All agent/session-controlled text in this phase's new rendering (`steer_status`, `steer_sent`) uses `createElement`+`textContent` exclusively; confirmed by code inspection and by porting `/acp`'s injection-safety test(s) for this content.
- [ ] `.acp-queue-steer`/`.acp-mode-*`/`.sr-only` CSS lives in `style.css`.
- [ ] `AGENTS.md:7`'s "Exception" clause updated to name `.acp-compaction-details`/`.acp-compaction-recap`, not removed outright.
- [ ] Dense test coverage added (region-extraction harness) comparable to `acp.html`'s existing Queue/Steer test density (`tests/acp_page.test.mjs:7678-8097`, `:9996-10050`, ~27-31 tests), including the ported injection-safety test(s).
- [ ] Live QA: Stop cancels a turn; Queue holds a prompt until turn end then sends it; Steer injects mid-turn; `steer_status` renders the three-state echo — all identical to `/acp`, including recovery after a refused steer send and after a simulated `agent_died`.

### Phase 4: Image paste-to-attach (SC6) [QA]

**Goal**: Add the image-attachment pipeline to the dashboard, integrated into `dashSendPrompt()` at both of its send-prompt call sites.

**File scope**: `src/power_atlas/templates/index.html`.

**4a. Port the pure encode pipeline** (`dash`-prefixed, `index.html`-only — no shared-module involvement; CSS is already page-agnostic in `style.css:1447-1460`, no migration needed here): `dashImagesSupported`, `dashImageFilesFrom`, `dashLoadImage`, `dashEncodeOnce`, `dashEncodeToBudget`, `dashBlobToBase64`, `dashStageOne`, `dashStageFiles`, `dashRevokeAttachment`/`dashClearAttachments`/`dashReleasePendingAttachments`/`dashRemoveAttachment`, `dashRenderTray`, `dashAttachmentChip` — ported from `acp.html:1604-1892` (the ladder constants `IMAGE_LADDER`/`IMAGE_FORMATS` — `acp.html:1604-1613` — copy verbatim; `imageMaxCount`/`imageMaxBytes` defaults copy from `acp.html:1597-1598` but must be overwritten from the `connected` meta's `maxPromptImages`/`maxPromptImageBytes` — the values Phase 1c already stored on the dashboard side).

**4b. Two send-prompt call sites need the `images` payload, not one**: `dashSendPrompt()`'s immediate path (its main body, `index.html:826-852`) AND the deferred lazy-attach flush inside `dashHandle()`'s `history` branch (`index.html:934-940`, the `send('prompt', ...)` call at `:937`). Introduce `_dashPendingImages` state, paralleling the existing `_dashPendingSend` for prompt text — populate it alongside `_dashPendingSend` whenever the immediate path defers to the lazy-attach flow, and consume it at the deferred `send('prompt', ...)` call site. Without this, a first image+message sent to a not-yet-attached session (the common case — a session the dashboard hasn't lazily attached to yet) silently drops the image, since only the second call site actually fires. At whichever call site actually executes, attach `payload.images = dashAttachments.map(({mimeType, data}) => ({mimeType, data}))`.

**4c. Markup**: add `#dashTray` mirroring `acp.html:234-246`, inside `#dashComposer` above the composer row.

**4d. Wiring**: `paste`/`dragover`/`drop` listeners on `#dashPromptInput`/`#dashComposer` calling `dashStageFiles`, mirroring `acp.html:6425-6452`. `meta turn:start` releases pending attachments (mirrors `acp.html:5138-5142`); an `error` response restores staged images for retry (mirrors `restorePendingPrompt`, `acp.html:5058-5090`).

**Exit criteria**:
- [ ] Paste/drag-drop stages images with the same compression ladder and count/byte limits as `/acp`, values sourced from the server's `connected` meta, not hardcoded client defaults.
- [ ] `[Image N]` markers inserted/renumbered in the textarea correctly.
- [ ] Images travel as `payload.images` on `send('prompt', ...)`, correctly attached whether the send fires immediately or via the lazy-attach flush (`_dashPendingImages`) — never through a `chunk` frame.
- [ ] A wiring-level test confirms a synthetic `connected` meta with `maxPromptImages`/`maxPromptImageBytes` updates the dashboard's image-budget state, not relying on Live QA alone.
- [ ] Dense test coverage added mirroring `acp.html`'s image-attach tests (`tests/acp_page.test.mjs:1391-1781`), including a test that stages an image before the session has attached, confirming it survives the lazy-attach flush.
- [ ] Live QA: paste an image, send with and without accompanying text — both to an already-attached session and to a fresh, not-yet-attached one — confirm server-side `_validate_images` accepts it (no `-32603`-style rejection) and the transcript renders `[Image N]` markers, not the image bytes.

### Phase 5: Sub-agent/crew read-only panel (SC8) [QA]

**Goal**: Add the sub-agent panel to the dashboard, including its own independent WebSocket with graceful handling of the global connection-budget cap.

**File scope**: `src/power_atlas/templates/index.html`.

**5a. Markup**: add `#dashSubPanel`/`#dashSubBack`/`#dashSubRole`/`#dashSubStatus`/`#dashSubTranscript` mirroring `acp.html:219-233`, placed in the transcript panel (`index.html:287-335`) alongside `#dashTranscriptWrap`/`#dashComposer` — the shared `[hidden]` display-none rule already in `style.css:1079` covers `.acp-subpanel` generically, no CSS work needed here.

**5b. Crew-panel rendering (in-transcript)** and **read-only sub-view (this panel)** are two different pieces `acp.html` keeps distinct (`renderCrewPanel`/`setCrew` vs. `openSubagent`/`closeSubagentView`) — port both, `dash`-prefixed: `dashCrewLabel`/`dashRenderCrewPanel`/`dashSubagentState`/`dashStopSlotTimer`/`dashRemoveSingleCrewPanel`/`dashRemoveAllCrewPanels`/`dashSetCrew` (from `acp.html:2241-2429`), and `dashOpenSubagent`/`dashCloseSubagentView`/`dashConnectSubWs`/`dashSubAppendChunk`/`dashSubAddToolCall`/`dashSubAddNote`/`dashHandleSub` (from `acp.html:2456-2602`). `dashSetCrew` reaches into `transcript-renderer.js`'s `toolRows` state exactly as `acp.html`'s `setCrew` does (`acp.html:2416`) — this cross-module reach is pre-existing, shared-module behavior, not new coupling introduced by this port. **Port `dashStopSlotTimer`'s cleanup path fully** — `acp.html`'s crew slots run a `setInterval` for elapsed-time ticking (`acp.html:2353-2429`), and every path that removes a crew slot must clear its timer; do not let a ticking interval survive an unmounted/removed crew row.

**5c. Security — agent-controlled text rendering**: the sub-agent panel renders live agent output (`dashSubAppendChunk`/`dashSubAddToolCall`/`dashSubAddNote`). Use `createElement`+`textContent` exclusively — never `innerHTML` — mirroring `acp.html`'s equivalents (`acp.html:2505-2554`). Port the specific `/acp` injection-safety test(s) for this content (same pattern as Phase 3's, `tests/acp_page.test.mjs:10050`-adjacent) as required parity tests.

**5d. The `window.removeAllCrewPanels`/`window.closeSubagentView` guards already exist** (`transcript-renderer.js:1899-1900`, called from `clearTranscript()`) and currently no-op harmlessly on the dashboard (confirmed: `dashHandle`'s `history` branch already calls `renderTranscriptHistory` → `clearTranscript()` on every session load, today). This phase makes those guards **live**: assign `window.removeAllCrewPanels = dashRemoveAllCrewPanels` and `window.closeSubagentView = dashCloseSubagentView` once both functions exist, so a `session` frame or history reload correctly tears down any open sub-agent panel on the dashboard — matching `acp.html`'s existing behavior (this coupling is directly tested on `/acp`, `tests/acp_page.test.mjs:5742`). **These globals are owned by `index.html`'s new code, not `composer-chrome.js`** — `transcript-renderer.js` is the file that calls them; it is not modified by this plan.

**5e. Second WebSocket, with graceful capacity handling**: `dashConnectSubWs(sid)` opens an independent `dashSubWs` (separate from `_dashWs`) exactly as `acp.html`'s `connectSubWs` does (`acp.html:2485-2503`) — no reconnect loop for this socket (same rationale as `/acp`: it only exists while the panel is open, one tap re-opens it). **`MAX_CONNECTIONS=8` (`acp.py:282`) is a global cap across every socket on the machine, not per-viewer** — a dashboard sub-agent socket competes for the same budget as `/acp`'s own sockets and any other open session. If the connection attempt is rejected (a `too_many_connections`-style close/error), `dashConnectSubWs` must fail gracefully: show a clear message in the panel ("Too many active connections — close another session or sub-agent view and try again") rather than leaving the panel in a stuck/blank loading state. This is client-side error handling only — no change to `MAX_CONNECTIONS` itself (out of scope, see Scope boundaries). `dashOpenSubagent`/`dashCloseSubagentView` toggle `#dashTranscriptWrap`/`#dashComposer` visibility independently of `dashRefreshComposerControls()` (Phase 3), mirroring the acp.html split between `refreshComposerControls` (which button shows) and `openSubagent`/`closeSubagentView` (whether the composer shows at all).

**5f. `handleSub(frame)` is a deliberately separate, smaller dispatcher** (mirrors `acp.html:2556-2602` reasoning) — do not thread sub-agent frames through `dashHandle()`'s main `if`-chain.

**5g. Extend Phase 1's baseline `agent_died` case**: also close any open sub-agent panel (`dashCloseSubagentView`) and clear crew state (`dashRemoveAllCrewPanels`), mirroring `acp.html`'s `agent_died` handler.

**Exit criteria**:
- [ ] Crew entries render inline in the transcript and are clickable; clicking opens the read-only sub-agent panel.
- [ ] Opening a sub-agent panel hides `#dashTranscriptWrap`/`#dashComposer`, shows `#dashSubPanel`; the back button reverses it and leaves `dashSubWs` open (reused on reopen, not recreated).
- [ ] A new `session` frame, history reload, or `agent_died` closes any open sub-agent panel and clears crew state (`removeAllCrewPanels`/`closeSubagentView` guards now live, correctly attributed to `index.html`'s new code).
- [ ] Crew-slot timers (`dashStopSlotTimer`) are cleared on every path that removes a crew slot — no orphaned `setInterval`.
- [ ] A rejected `dashConnectSubWs` connection attempt (simulated by exhausting `MAX_CONNECTIONS`) shows a clear message, not a stuck loading state.
- [ ] All agent-controlled text in the sub-agent panel uses `createElement`+`textContent`; confirmed by code inspection and by porting `/acp`'s injection-safety test(s).
- [ ] Coverage comparable in density to `/acp`'s **~25** dedicated crew/subagent tests (`tests/acp_page.test.mjs:5519-5762`, `:6079-6223`, `:8415-8563`), including the timer-cleanup and connection-rejection cases.
- [ ] Live QA: trigger a sub-agent fan-out in a live/held session, confirm the dashboard's crew panel and read-only sub-view match `/acp`'s behavior exactly, including reopening reusing the existing socket and correct teardown on `agent_died`.

### Phase 6: WebSocket reconnect-on-drop (SC7) [QA]

**Goal**: Add reconnect-on-drop to the dashboard, scoped to restore the **complete** state Phases 1-5 leave in place — this phase's earlier draft under-scoped this list and is corrected below. Built last by design (§3 Design Decisions) — its cost and correct scope depend on the full state surface existing first. Update README.md to reflect the complete shipped feature set.

**File scope**: `src/power_atlas/templates/index.html`, `README.md`.

**6a. Markup**: add `#dashReconnect`/`#dashReload` mirroring `acp.html:163-164`.

**6b. JS**: port `dashConnect()`'s `onclose` handling from `acp.html`'s `connect()` (`acp.html:5806-5882`) and `diagnoseRejectedHandshake()`/`reportStaleToken()` (`acp.html:5766-5804`) — scoped explicitly to the **full** state surface Phases 1-5 introduce:
- Reset `_dashTurnActive`, `_dashLoadingSid`, `_dashPendingSend`, `_dashPendingImages` (Phase 4), `_dashPendingCreate`, `_dashOrigin` to their pre-attempt defaults where a load/create was in flight at disconnect time — mirrors `acp.html`'s `onclose` resetting `pendingCreate`/`setLoading(null)` (`acp.html:5843-5849`). A drop during lazy-load must not leave the composer stuck under a "Starting the agent…" message with no recovery path.
- Restore `_dashQueuedPrompt`/`_dashQueuedPromptSession` (Phase 3's dedicated Queue-mode state, kept separate from `_dashPendingSend` — see Phase 3) if set, and re-enable Queue/Steer controls.
- Re-render the image tray from `dashAttachments`/`dashPendingAttachments` (Phase 4's state).
- Close any open sub-agent panel via `dashCloseSubagentView` **and explicitly close and null `dashSubWs`** (Phase 5) — do not just hide the panel; the sub-socket has no reconnect loop of its own and must not be left orphaned when the main socket drops.
- Confirm crew-panel `setInterval` timers (Phase 5's `dashSetCrew`/`dashStopSlotTimer`) are torn down via the same `dashRemoveAllCrewPanels`/`closeSubagentView` path a fresh `session`/`history` frame already triggers on reconnect's re-subscribe — add an explicit exit criterion verifying this fires on reconnect specifically, not only on an unrelated new-session event.
- Close the command palette (SC1) if open on disconnect, matching the palette's existing `hideCommandDropdown()` call on other turn-state transitions — simpler and safer than attempting to preserve palette state across a reconnect.
- Exponential backoff (1000ms, doubling, capped 30000ms) matching `acp.html`'s constants exactly. `reportLoadFailure()`'s reload-only path (`acp.html:1515-1527`) applies the same way for a `session/load` failure that never triggers `onclose`.

**6c. `README.md`**: locate the existing dashboard feature-scope description (the "### Features" section starting `README.md:57`, specifically the dashboard-scope bullet around `:66-70`) and update it to reflect the now-complete shipped feature set (SC1-8) rather than describing the dashboard as a scoped-down live-attach panel. Update deliberately once, here, rather than incrementally per phase, since the README's dashboard description is one cohesive bullet best rewritten against the finished surface rather than edited 6 times.

**Exit criteria**:
- [ ] `dashConnect()` schedules exponential-backoff reconnect on an unexpected close, matching `acp.html`'s timing constants.
- [ ] A stale-token handshake rejection shows Reload only (not Reconnect), matching `acp.html`'s `reportStaleToken()` reasoning (a new page load is required for a fresh `ACP_TOKEN`).
- [ ] On reconnect, **every** piece of dashboard state introduced by Phases 1-5 is restored or cleanly torn down: turn-active flag, lazy-load/pending-create state, Queue-mode's dedicated pending prompt, staged images, open sub-agent panel (with `dashSubWs` explicitly closed, not left orphaned), crew-panel timers, and any open command palette — no stale UI left showing a state the reconnected session doesn't confirm.
- [ ] Dense test coverage mirroring `/acp`'s reconnect tests (`tests/acp_page.test.mjs:8122-8193`: scheduled-on-close, delay-doubles, delay-capped, delay-resets, no-reconnect-when-not-opened), extended with cases covering each of the state-restoration points above.
- [ ] `README.md` updated to describe the dashboard's complete feature set.
- [ ] Live QA: kill the WS connection (e.g. restart PowerAtlas or block the port, with the user's confirmation per governance) while mid-turn with a queued prompt, staged image, and open sub-agent panel; confirm reconnect restores/clears each correctly and matches `/acp`'s behavior under the same test.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Refactoring SC1-4 into `composer-chrome.js` regresses `acp.html`, a stable, heavily-tested page with no known bug in this area | High — `/acp` is the sole remote/mobile surface; a regression there has no fallback | `/acp`'s own existing test suite (20+ palette tests, etc.) must pass unmodified against the refactored code in every phase touching it — this is the phase's own exit criterion, not a follow-up check |
| Functions moved out of `acp.html`'s IIFE silently lose access to free variables they depended on (`sessionId`, `replaying`, `turnActive`) | High if unaddressed — every call would `ReferenceError` on both pages | Every `initXxxDom()` in `composer-chrome.js` takes explicit accessor-function parameters for any page-lifecycle state the ported code reads, not just DOM refs (Phase 1/2 design, added during plan review) |
| `dashHandle()`'s `meta` branch restructuring done additively but incorrectly, breaking existing turn-start/turn-end handling | Medium — would silently break the dashboard's core live-attach flow, not just the new features | Exit criteria explicitly require the existing turn branches to remain intact; `/acp`'s and the dashboard's existing tests both re-run |
| `MAX_CONNECTIONS=8` is a **global**, not per-viewer, cap — SC8's second socket competes with `/acp`'s own sockets and every other connection on the machine for the same shared budget | Medium — a rejected connection attempt on a busy machine, not a rare edge case given the cap already covers `/acp`'s own dual-socket usage today | Phase 5 requires graceful client-side handling of a rejected connection (clear message, no stuck UI) rather than a cap increase (out of scope, a backend change) |
| SC5/SC7/SC8 are duplicated, not extracted, implementations — independently maintained in `acp.html` and `index.html`, prone to drifting apart over time | Medium — a future `/acp`-side fix or feature addition may not be mirrored to the dashboard, and vice versa | Add a paired cross-reference comment at each duplicated function's definition site on both pages (e.g. `// mirrors acp.html:XXXX — keep in sync`) so a future editor sees the counterpart; no automated drift-detection is in scope |
| Reconnect (Phase 6) restoring an incomplete inventory of dashboard state, leaving stuck UI after a disconnect | High if incomplete — the whole point of the feature is recovery; a partial restore is worse than no restore, since it looks recovered but isn't | Phase 6's restore list is explicit and enumerates every piece of state introduced by Phases 1-5 (see Phase 6); exit criteria test each restoration point individually, not just "reconnect works" |
| `acp_died` (kiro-cli process crash, independent of a WS drop) has no dashboard handling if left unaddressed | High — leaves the composer permanently disabled with no recovery but a full page reload | Phase 1 adds a baseline `agent_died` case; Phases 3 and 5 extend it as their own state comes into existence, each with an explicit exit criterion |

## 7) Verification

**Automated**:
- `node tests/acp_page.test.mjs` — full JS template harness; must pass after every phase (not part of pytest/CI per `AGENTS.md`, run manually).
- `pytest tests/test_web.py` — baseline run before Phase 1 and after Phase 1 specifically (the CSP/script-count change), then after every phase as a regression check even though no backend logic changes are anticipated.
- `.venv-PowerAtlas/Scripts/python _check_test_names.py` — pre-commit hook guard against duplicate test names in the expanded `tests/acp_page.test.mjs`; also runnable by hand.

**Manual / live QA** (this plan is runtime-verification-heavy — the entire point is dashboard-vs-`/acp` behavioral parity, which code-level inspection alone cannot confirm):
- After each phase, open `/acp` and `/` side by side against the same live or held kiro-cli-v3 session and compare the shipped feature's behavior directly.
- After Phase 5 specifically, exercise a real sub-agent fan-out (or a fixture session with crew data) on both pages, and simulate a `MAX_CONNECTIONS` rejection.
- After Phase 6 specifically, force a WS disconnect (block the port or restart PowerAtlas, with the user's confirmation per governance) mid-turn with queued/staged/open-panel state present, on both pages, and compare recovery behavior.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `AGENTS.md:7` | Correct "All JS for `/acp` is inline in `acp.html`" to note the shared `composer-chrome.js` module | 1 |
| `tests/test_web.py:4163-4186` | Update CSP script-tag-count assertion from 4 to 5; add named `composer-chrome.js` assertion | 1 |
| `tests/acp_page.test.mjs:634-636`, `:1188-1193` | Correct stale script-element-count comments (both should state five) while this area is already being touched | 1 |
| `AGENTS.md:7` | Revise the "Exception" clause to name `.acp-compaction-details`/`.acp-compaction-recap` (the sole remaining inline-only CSS) in place of `.acp-mode-*` — not removed outright | 3 |
| `README.md` (near `:66-70`, "### Features") | Update the dashboard's feature-scope description to reflect the complete shipped surface (SC1-8), replacing the "scoped-down live-attach panel" framing | 6 |

## Progress Tracker

| # | Phase/Task | Status | Notes |
|---|---|---|---|
| 1 | Shared-module foundation + SC2 + SC3 + SC4 [QA] | Not started | Includes DI-accessor fix and baseline `agent_died` handling |
| 2 | Slash-command palette (SC1) [QA] | Not started | Depends on Phase 1's `composer-chrome.js` |
| 3 | Queue/Steer + Stop (SC5) [QA] | Not started | Depends on Phase 1's `composer-chrome.js` (reuses `railStored`/`railStore`); extends `agent_died`/`error` handling |
| 4 | Image paste-to-attach (SC6) [QA] | Not started | Depends on Phase 1's `meta`-widening for `maxPromptImages`/`maxPromptImageBytes` |
| 5 | Sub-agent/crew panel (SC8) [QA] | Not started | Depends on `transcript-renderer.js`'s pre-existing `window.removeAllCrewPanels`/`closeSubagentView` guards (not `composer-chrome.js`); extends `agent_died` handling |
| 6 | WS reconnect-on-drop (SC7) [QA] | Not started | Must be last — scopes to the complete state surface from Phases 1-5 |

## Dependency Graph

```
Phase 1 (composer-chrome.js foundation + SC2/SC3/SC4 + baseline agent_died)
   |
   +---------------+---------------+
   |               |               |
Phase 2 (SC1)   Phase 3 (SC5)   Phase 4 (SC6)
   |               |               |
   +---------------+---------------+
                    |
               Phase 5 (SC8)
                    |
               Phase 6 (SC7) -- must be last
```

Phases 2/3/4 have no dependency on each other's *output*, only on Phase 1's foundation — but all four (2, 3, 4, and 1) share `index.html` as a file scope, so per the Parallel Phase Annotation rule none carry `[P:N]`; they execute sequentially in the order shown. Phase 5 depends on Phase 1's shared-module guards being live and Phase 3's `agent_died` extension pattern to follow. Phase 6 depends on the full state surface from Phases 1-5.

## Backwards Compatibility

| Item | Strategy | Safety effect |
|---|---|---|
| `acp.html` refactor (SC1-4 moved to `composer-chrome.js`) | Behavior-preserving by construction — bare call sites resolve to the same-named global function, now defined externally instead of locally, with explicit accessor parameters replacing the free variables that would otherwise be lost; `/acp`'s existing test suite must pass unmodified | Any regression is caught by the existing suite before merge, not discovered live on the sole remote-access surface |
| `dashHandle()`'s `meta` branch widened, not replaced | Additive — existing `payload.turn === 'start'/'end'` branches untouched, new cases added alongside | No behavior change to already-working dashboard turn handling |
| Sid/copy bug fix (SC3) | Deliberate behavior change on `/acp` (copy value changes from short name to full path), done at explicit user request | Low risk — a clipboard-convenience feature, not data-affecting; anyone relying on the old (buggy) short-name-copy behavior sees a different, arguably more useful, value |
| AGENTS.md's CSS-exception clause | Revised, not removed — still accurately documents the one remaining inline-only CSS block | Avoids leaving stale documentation that would send a future session looking for `.acp-compaction-*` in `style.css` |

## File Change Summary

### Created
- `src/power_atlas/static/composer-chrome.js`

### Modified
- `src/power_atlas/templates/acp.html`
- `src/power_atlas/templates/index.html`
- `src/power_atlas/static/style.css`
- `tests/acp_page.test.mjs`
- `tests/test_web.py`
- `AGENTS.md`
- `README.md`

### Deleted
- None (no files removed; `acp.html`'s inline implementations of SC1-4 are deleted as code within the file, not as files)

### Unchanged
- `src/power_atlas/acp.py`, `src/power_atlas/web.py`, `src/power_atlas/data*.py` — confirmed no backend changes needed beyond nothing (§ Scope boundaries and non-goals); `MAX_CONNECTIONS` is explicitly not raised
- `src/power_atlas/static/transcript-renderer.js` — its pre-existing `window.removeAllCrewPanels`/`closeSubagentView` guard-calling code is unmodified; only which functions those globals resolve to changes (from Phase 5's new `index.html` code, never from `composer-chrome.js`, which has no involvement in crew/sub-agent logic)

## 9) Implementation Divergences from Plan

<Reserved — filled during implementation>

## Follow-up Work (Deferred)

<None — the `MAX_CONNECTIONS` capacity concern raised during plan review is fully addressed within this plan's own scope (Phase 5's graceful-rejection exit criterion), not deferred.>

## Review Log

### 2026-09-21 — Plan Review (via /qplan, Standard effort, 4 personas)

22 distinct findings after merging across personas (3 High, 12 Medium, 7 Low — from Architect+gap-critic, Senior engineer, Security auditor, and Reliability engineer, each independently reviewing against Correctness/Completeness/Feasibility/Risk/External readiness/Cost impact). All 22 auto-resolved within this single review cycle (user requested 1 cycle; no findings required escalating a product/design decision beyond the plan's existing scope boundaries).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Phase 1's ported sid/copy and log functions read IIFE-private free variables (`sessionId`, `replaying`) that don't exist once moved out of `acp.html`'s IIFE — `ReferenceError` on both pages. | Fixed — added `getSessionId`/`isReplaying` DI accessor functions to `initSidCopyDom`/`initLogDom`, mirroring Phase 2's existing palette-accessor pattern. |
| 2 | High | `agent_died` was identified as a dropped frame type but no phase ever wired it into `dashHandle()`. | Fixed — added a baseline case in Phase 1, extended in Phases 3 and 5 as their state comes into existence. |
| 3 | High | Phases 3 and 5 render agent/session-controlled text with no explicit exit criterion enforcing `acp.html`'s no-innerHTML security convention. | Fixed — added explicit exit criteria to both phases citing the exact patterns to mirror, plus required porting of the specific injection-safety tests. |
| 4 | Medium | `MAX_CONNECTIONS=8` was mischaracterized as per-viewer; it's a global cap, understating SC8's connection-budget risk. | Fixed — corrected Current State and Risk Assessment; added a client-side graceful-rejection exit criterion to Phase 5 instead of a cap increase (kept out of scope). |
| 5 | Medium | The plan's `railStored`/`railStore` "naming collision" with `index.html` was factually wrong (real names are `dashRailStored`/`dashRailStore`) — no collision exists. | Fixed — corrected the claim; replaced the investigate-a-collision task with a reconciliation note. |
| 6 | Medium | `tests/acp_page.test.mjs`'s VM-sandbox harness has no load step for the new `composer-chrome.js`, so the existing suite would `ReferenceError` at first call into ported code. | Fixed — added an explicit exit criterion to Phase 1 mirroring `transcript-renderer.js`'s existing load step. |
| 7 | Medium | CSS migration scope missed `acp.html:548-594` (`.acp-compaction-details`/`.acp-compaction-recap`), contradicting the planned "no CSS remains inline" claim and premature AGENTS.md exception-clause removal. | Fixed — explicitly scoped this CSS out of the plan; revised (not removed) the AGENTS.md exception clause to name it instead. |
| 8 | Medium | Phase 4 named only one of two `send('prompt', ...)` call sites needing the image payload — the deferred lazy-attach flush was missed, risking a silently dropped image on first send to an unattached session. | Fixed — added `_dashPendingImages` state and an explicit exit criterion covering both call sites, plus a dedicated test case. |
| 9 | Medium | Phase 6's reconnect-restore list omitted several pieces of state (`_dashLoadingSid`, `_dashPendingCreate`, `_dashOrigin`, command-palette state, crew timers, explicit `dashSubWs` closure) introduced by earlier phases. | Fixed — expanded the restore list to the complete state surface, with a per-item exit criterion. |
| 10 | Medium | Phase 3 didn't extend `dashHandle()`'s generic `error` case to restore steer-pending state, unlike `acp.html`'s three restoration points. | Fixed — added the extension and an explicit exit criterion. |
| 11 | Medium | Phase 3 planned to reuse `_dashPendingSend` for Queue-mode text, risking a reconnect-triggered premature fire against the pre-existing lazy-load-pending mechanism. | Fixed — introduced dedicated `_dashQueuedPrompt`/`_dashQueuedPromptSession` state, kept separate. |
| 12 | Medium | SC8's "~35 tests" density target was inflated; direct recount found ~25. | Fixed — corrected the number everywhere it appears (Success criteria, Design Decisions, Phase 5, Progress Tracker). |
| 13 | Medium | Risk Assessment had no row for long-term behavioral drift between the duplicated (not extracted) SC5/SC7/SC8 implementations. | Fixed — added a drift-risk row with a lightweight cross-reference-comment mitigation. |
| 14 | Medium | Phase 1/4's `meta`-widening (context percent, image limits) had no automated test, only manual Live QA. | Fixed — added explicit wiring-level test exit criteria to both phases. |
| 15 | Medium | Phase 3/5's "dense test coverage" language didn't name the specific injection-safety tests required for parity. | Fixed — named the specific `/acp` test pattern (`tests/acp_page.test.mjs:10050` and adjacent) as a required parity test in both phases. |
| 16 | Low | Phase 1's stale-script-count-comment fix self-contradicted ("five" in body text vs. "five"/"four" in exit criteria). | Fixed — reconciled to a single, consistent instruction (both comments state five, verify each test's own counting basis first). |
| 17 | Low | `window.logLine = logLine;` and its comment become stale once Phase 1 deletes `logLine`'s local IIFE definition. | Fixed — added explicit removal to Phase 1's exit criteria. |
| 18 | Low | Progress Tracker and File Change Summary misattributed `removeAllCrewPanels`/`closeSubagentView` ownership to `composer-chrome.js` instead of `transcript-renderer.js`. | Fixed — corrected both tables. |
| 19 | Low | Five citations carried small (1-13 line) offsets from actual source (`test_web.py`, `acp.html`'s `reportStaleToken`/`IMAGE_LADDER` locations, `README.md`). | Fixed — tightened the citations that could be corrected with confidence; added a general verify-before-editing caution for the rest. |
| 20 | Low | The Phase 1 collision-check instruction was scoped only to `railStored`/`railStore`, not the module's other new global names. | Fixed — generalized to a full-name-list check. |
| 21 | Low | `index.html`'s new `composer-chrome.js` script tag lacked the conditional-nonce pattern already used for `transcript-renderer.js`. | Fixed — added the matching conditional pattern for consistency. |
| 22 | Low | Phase 2's CSS migration range inadvertently included the `.acp-cmd-placeholder` rules despite SC1's explicit "not ported" exclusion for that dead path. | Fixed — excluded those lines from the migrated range; marked for deletion instead. |

## Harness Improvement Opportunities

<Reserved — none encountered during planning>
