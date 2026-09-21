# Dashboard / ACP Feature Parity

> **Date**: 2026-09-21
> **Status**: Exploring
> **Scope**: Port all of `/acp`'s features missing from the main dashboard onto the dashboard, except the mobile drill-down view toggle.

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
- **SC8 — Sub-agent/crew read-only panel**: added to the dashboard, including a second independent WebSocket for the panel view (mirrors `/acp`'s existing dual-socket model), with test coverage comparable in density to `/acp`'s existing ~35 dedicated tests.
- **Already satisfied, no work needed** — the rail free-text filter: `dashRailFilter`/`dashRailMatches`/`dashRailMatchesFlat` already exist (`index.html:1757-1769`), wired to the dashboard's existing "Search workspaces" box (`index.html:136`), which already filters session rows, not just workspace cards.
- `/acp`'s own existing test suite (`tests/acp_page.test.mjs`) continues to pass **unmodified in its assertions** after the SC1-4 refactor — the refactor must be behavior-preserving on `/acp`.

### Scope boundaries & non-goals

- **Out of scope**: the mobile drill-down view toggle (`#acpViewToggle`) — stays `/acp`-only by design; `/acp` remains the sole mobile/remote-reachable surface.
- **Out of scope**: any backend (`acp.py`/`web.py`) changes — confirmed unnecessary. Three independent research passes traced WS frame routing (`_registry.broadcast()`, `acp.py:2099-2103`), origin/token gating (`web.py:1272-1330`), and image/command validation, and found zero page-aware branching anywhere. Every frame type these features need already exists in `CLIENT_TYPES`/`SERVER_TYPES` (`acp.py:161-197`) and already reaches the dashboard's socket today.
- **Out of scope**: fixing the `_kiro.dev/commands/execute`/`commands/options` kiro-cli RPC brokenness noted in `plans/done/260909-1127_ACP_V3_PRODUCTION_HARDENING.md` — pre-existing, separately tracked, not touched here.
- **Out of scope**: any new domain concepts, persistent data-model changes, or third-party/external dependencies.
- **Narrow in-scope exception**: one bug fix in `acp.html` itself (the sid/copy full-path behavior, SC3) — explicitly requested by the user, not a general `acp.html` bugfix sweep. No other pre-existing `/acp` bug found during research (e.g. the dead `commands_options` paths) is being fixed.

---

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

## Exploration Discovery

### Existing patterns & constraints

- **`acp.html`'s script is one IIFE**: `(function () {` at `acp.html:674` to `})();` at `acp.html:6516`. Every module-level `var`/`function` inside is private to that closure; only `send`, `logLine`, `removeAllCrewPanels`, `closeSubagentView` are explicitly exposed on `window` (`acp.html:805-815`) so `transcript-renderer.js` can reach them via `typeof X === 'function'` guards.
- **`index.html`'s script carries no IIFE** — a deliberate choice (comment at `index.html:709-710`) so `transcript-renderer.js` can reach `send()` as a true global. `dashConnect`/`dashHandle`/all `dashRail*` are plain top-level declarations.
- **Two conflicting prior precedents** for porting an `/acp` feature to the dashboard: (a) `transcript-renderer.js` — a genuinely shared, non-IIFE module with an explicit DI pattern (`initTranscriptDom({...})`, both pages call it with their own element ids); (b) the new-session picker (`plans/done/260919-1551_DASHBOARD_ACP_NEW_SESSION_PICKER.md`) — `acp.html`'s picker JS copied into `index.html` under `dash`-prefixed names, no shared file, no documented rationale for why JS (unlike its CSS) wasn't migrated.
- **The discriminator between the two precedents, grounded in code structure**: a feature is cheap to extract into a shared DI module when its functions touch only their own DOM refs plus one or two page-supplied primitives (session id, a send function) — `transcript-renderer.js`'s exact shape. A feature is expensive to extract when its functions read/write `acp.html`'s page-lifecycle state directly (`turnActive`, `ws`, `replaying`, `_steerPending`, `crews`, `subViewSid`) — no DI seam exists for that state today.
- **Per-feature entanglement verdicts** (full detail from three sub-agent reports, available on request — this is the distilled table):

  | Feature | Verdict | Key reason |
  |---|---|---|
  | Context indicator (SC2) | Cheapest | 3 DOM refs, 1 frame field (`meta.contextPercent`) |
  | Sid/copy widget (SC3) | Cheap | ~70 lines, only `sessionId` + 2 DOM refs |
  | Debug log (SC4) | Cheap alone, high fan-in | Self-contained, but `logLine()` is called 38× unconditionally elsewhere in `acp.html` |
  | Slash-command palette (SC1) | Cheap-moderate | Own catalogue state + generic `ws`/`sessionId`/`send`/`turnActive` reads |
  | Image attach (SC6) | Moderate | Pure encode pipeline, but threaded through `sendPrompt()`, structurally different from `dashSendPrompt()`'s lazy-attach flow |
  | Queue/Steer + Stop (SC5) | Moderate, inseparable pair | `refreshComposerControls()` governs both from shared turn-state |
  | Reconnect (SC7) | Most expensive "protocol" feature | `onclose` resets everything the page currently tracks — cost scales with feature count present |
  | Sub-agent panel (SC8) | Most expensive of all | Reaches into `transcript-renderer.js`'s internal `toolRows`; opens a second independent WebSocket; ~35 existing tests |

- **CSS location**: features SC1/SC5's entire CSS lives only in `acp.html`'s own page-scoped `<style nonce>` block (`acp.html:426-652`), not in `style.css` at all. `index.html` sets no CSP header and takes no nonce (confirmed via `web.py`, only `acp_page()` at `web.py:1508-1509` sets `Content-Security-Policy`) — so an inline `<style>` block on `index.html` is now mechanically possible, unlike before, but the chosen approach (per Resolved decisions) is migration to `style.css`, matching the `.acp-taskmode-*` precedent. Most other features' CSS (context, sid/copy, tray, subpanel, log body) is already class-based and page-agnostic in `style.css`; confirmed ID-gated exceptions: `#acpLogToggle[aria-expanded="false"]`/`:hover` (`style.css:1533-1534`), `#acpSend`/`#acpStop` hover/disabled rules (`style.css:1498-1515`).
- **`dashHandle()` (`index.html:883-1011`) currently drops, with no case**: `commands`, `skills`, `steer_ack`, `steer_sent`, `steer_status`, `subagents`, `history_truncated`, `compaction`, `commands_options_result`, `commands_execute_result`, `title`, `agent_died` — and critically, **any `meta` frame that isn't a turn marker** (the `if` only checks `payload.turn === 'start'|'end'`), which silently drops the `connected` meta (`maxPromptImages`/`maxPromptImageBytes`, needed by SC6) and the `contextPercent` meta (needed by SC2) that already arrive on the dashboard's socket today. The `meta` branch needs restructuring, not just additive cases.
- **No-innerHTML convention**: `acp.html` enforces `createElement`+`textContent` only, no `innerHTML`, anywhere (`acp.html:657-673`) — specifically because agent-authored text is attacker-influenced. `index.html` does **not** follow this globally (12 `innerHTML` sites found), but the existing live-attach region (`index.html:690-1011`) already avoids it, consistent with `transcript-renderer.js`'s own rule. Any ported code rendering agent-controlled text (palette descriptions, steer echoes, sub-agent transcript) must keep the safe pattern regardless of the rest of the page — this is a security constraint, not a stylistic preference.
- **Two existing test-harness patterns** in `tests/acp_page.test.mjs` (10,723 lines): (1) full-template render harness, used for nearly all `/acp`-side logic; (2) region-extraction harness (`dashPickerSource()`/`loadDashPicker()`, `tests/acp_page.test.mjs:10370-10496`) — string-slices a named region out of raw `index.html`, runs it in a sandboxed `vm` context with stubbed cross-region dependencies. Pattern (2) is what the dashboard's new duplicated features (SC5, SC7, SC8) should mirror; it's already proven for the picker.
- **`MAX_CONNECTIONS = 8`** (`acp.py`) budgets WebSocket connections **per viewer**, not per session-type — already covers `/acp`'s own main+sub-agent dual-socket case today, so SC8's second socket from the dashboard is the same class of usage, not new architecture server-side.
- **AGENTS.md governance for this project**: ACP UI iteration (template/CSS/JS changes) needs no PowerAtlas restart — `acp.html`/`index.html` are Jinja templates served with `Cache-Control: no-store` + `auto_reload=True`; `style.css` is served from the plain `/static` mount. A hard reload (Ctrl+Shift+R) picks up changes. **Python changes do require a restart** — none are anticipated in this plan (no backend changes, per Scope boundaries). Never restart PowerAtlas autonomously regardless.

### Risks & mitigations

- **Regression risk on `/acp`** from refactoring SC1-4 to consume the new shared module — `/acp`'s own existing test suite (20+ palette tests, etc.) must continue passing unmodified against the refactored code; this is the regression gate, not a new test-writing exercise.
- **`MAX_CONNECTIONS=8` budget pressure** once both `/acp` and the dashboard can open sub-agent panels concurrently — pre-existing cap, not new, but worth a runtime check during `/qdev`'s QA pass rather than assuming it never bites.
- **`_dash*` state coordination** — 11+ existing module-level `_dash*` variables already govern dashboard turn/session/picker state (`_dashAttachedSid`, `_dashTurnActive`, `_dashOrigin`, `_dashSent`, `_dashPendingSend`, `_dashLoadingSid`, `_dashPendingCreate`, etc., full inventory with reader/writer sites available from research). SC5/SC7/SC8's new code must thread into this existing state machine rather than inventing parallel state — risk of subtle coordination bugs if it doesn't.
- **SC7 sequencing risk** — reconnect's restore logic scales with how much state exists to restore; building it before SC5/SC6/SC8 land would mean redoing it. Must be built last.
- **`dashHandle`'s `meta` branch restructuring** (see Existing patterns) is a prerequisite for SC2 and SC6, not an independent nice-to-have — if skipped, both features silently receive no data despite the frames already arriving.

### Resolved decisions

- Q1: Extraction vs. duplication strategy for the 9 in-scope features? — A: hybrid, decided per feature by entanglement evidence — Decision: extract SC1 (palette), SC2 (context), SC3 (sid/copy), SC4 (log) into a new shared module; duplicate SC5 (Queue/Steer/Stop), SC7 (reconnect), SC8 (sub-agent panel) as `dash`-prefixed additions directly in `index.html`.
- Q2: Is the sub-agent panel (SC8) worth its cost (second WebSocket, ~35-test parity target, deepest coupling) or should it be deferred out of scope? — A: "build it, this is critical." — Decision: SC8 is in scope at full parity target, not deferred.
- Q3: Where should SC1/SC5's CSS (currently 100% inline in `acp.html`'s own `<style>` block) live? — A: accepted recommendation — Decision: migrate to `style.css`, matching the `.acp-taskmode-*` precedent (`plans/done/260919-1551_DASHBOARD_ACP_NEW_SESSION_PICKER.md`).
- Q4: Is the rail free-text filter actually missing, or already covered by `dashRailFilter`/the existing "Search workspaces" box? — A: accepted recommendation, no new work — Decision: dropped from scope as already satisfied; no dedicated in-rail search box added.
- Q5: `/acp`'s sid/copy button has a comment/code mismatch (comment claims full-path copy, code copies the short name) — replicate the actual current behavior, or the comment's claimed intent? — A: "b and fix the bug in acp.html" — Decision: implement full-path-copy behavior on the dashboard AND fix the same bug in `acp.html`, so both pages copy the full path (SC3).
- Q6: Should known-dead code paths in the slash-command palette (`commands_options` send, `_cmdOptionsTimer`, `applyCommandOptions()`, `.acp-cmd-placeholder`) be replicated for fidelity, or skipped? — A: accepted recommendation — Decision: skip all dead paths when extracting the palette (SC1).
- Q7: How should the four extracted features be organized — one shared file, four separate files, or folded into `transcript-renderer.js`? — A: accepted recommendation — Decision: one new shared file (working name `composer-chrome.js`), each feature with its own `initXxxDom()` call, mirroring `transcript-renderer.js`'s pattern but scoped to composer/topbar chrome rather than transcript content.
- Q8: Should ported features get test coverage matching `/acp`'s existing density everywhere, or a uniform lighter-touch approach? — A: accepted recommendation — Decision: differentiate — SC1-4 (extracted) get wiring-level tests only (core logic already covered by `/acp`'s existing suite against the now-shared module); SC5/SC7/SC8 (duplicated) get coverage comparable in density to what `/acp` carries for the same feature, via the region-extraction harness pattern already proven for the picker.
- Q9: For the four extracted features, does `acp.html` get refactored to consume the shared module (true single-sourcing), or keep its own inline copy (dashboard-only consumer, cosmetic "extraction")? — A: "a. acp.html remains to provide remote functionality only, but features will remain and be maintaied" — Decision: refactor `acp.html` to call the new shared module for SC1-4 — true single-sourcing. `acp.html` retains full feature parity throughout (never reduced), continuing to serve as the remote/mobile-reachable surface.

### Open items

- Exact naming/shape of the shared module's per-feature `initXxxDom()` functions — deterministic, `/qplan` can resolve by mirroring the existing `initTranscriptDom()` precedent (`transcript-renderer.js:56-61`).
- Whether SC1's `acp.html` refactor surfaces hidden coupling beyond what research found (rated "cheap-moderate," not "cheapest") — execution-contingent; if extra coupling appears, the likely resolution is one more DI-passed primitive, not a strategy change.
- Exact phase count/boundaries for `/qplan` — deterministic; research recommends extracted features (SC1-4) first (lower risk, higher confidence), then duplicated features (SC5, SC6, SC8), reconnect (SC7) last.
- Whether the `MAX_CONNECTIONS=8` budget needs a runtime check once both surfaces can open sub-agent panels concurrently — execution-contingent; flag as a `/qdev`/QA item, not a design change.

### Assumptions (unconfirmed)

None outstanding at end-of-interview — the three consequence-significant assumptions raised (composer-chrome.js follows `transcript-renderer.js`'s exact DI pattern; `acp.html`'s IIFE can call the new module's globals with no structural change; no backend changes are needed) were all stress-tested against direct code citations and survived, and the user reviewed and accepted the full checkpoint (including the residual/lower-impact defaults: no migration/rollback complexity, UX mirrors `/acp` exactly unless noted, ARIA semantics preserved, CSS class names unchanged when relocated, duplicated features implement against `index.html`'s existing `_dash*` state) without objection.

### Recommended approach

Phased build (exact phase boundaries and numbering are `/qplan`'s job; this is the shape, not the plan):

1. **Shared-module phase**: create the new shared module (working name `composer-chrome.js`) holding SC1 (palette), SC2 (context), SC3 (sid/copy, with the full-path bug fix), SC4 (log), ported from `acp.html`'s current inline code, following `transcript-renderer.js`'s DI pattern. Wire `index.html` to consume it (markup + init calls). Refactor `acp.html` to consume it too, replacing its inline implementations and dropping the dead `commands_options` paths. Migrate SC1/SC5's CSS to `style.css`. Widen `dashHandle()`'s `meta`-frame handling so `contextPercent` and the `connected` meta (`maxPromptImages`/`maxPromptImageBytes`) are no longer dropped. Verify `/acp`'s existing test suite passes unmodified; add wiring-level tests for the dashboard side using the region-extraction harness.
2. **Queue/Steer/Stop phase (SC5)**: `dash`-prefixed addition to `index.html`, threaded through the existing `_dash*` turn-state variables. Dense test coverage matching `/acp`'s.
3. **Image attach phase (SC6)**: `dash`-prefixed addition, integrated into `dashSendPrompt()`. Dense test coverage.
4. **Sub-agent panel phase (SC8)**: `dash`-prefixed addition including the second WebSocket. Coverage comparable in density to `/acp`'s ~35 tests.
5. **Reconnect phase (SC7)**: built last, scoped to restore whatever dashboard state exists after phases 1-4. Dense test coverage.

### QA environment

PowerAtlas is a local desktop web app (FastAPI/Starlette + Jinja2, tray-managed). Never restart it autonomously — always defer to the user. For this entire plan (templates, `style.css`, a new static JS file), no restart is needed: `acp.html`/`index.html` are served with `Cache-Control: no-store` and `auto_reload=True`; `style.css` and the new JS file are served from the plain `/static` mount. A hard reload (Ctrl+Shift+R) in the browser picks up every change. No Python changes are anticipated (no backend changes in scope) — if any turn out to be needed during implementation, that requires a user-approved restart, flagged at the time.

Test commands: `node tests/acp_page.test.mjs` (JS template harness — not part of the pytest suite or CI, run manually when touching either template's inline script). The pre-commit hook (`_check_test_names.py`, reinstalled per-clone via `cp _pre_commit_hook.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit`) guards against duplicate test names in `tests/acp_page.test.mjs`. No `pytest tests/test_web.py` impact expected (no backend changes), but worth a baseline run before/after given the scale of this plan.

Live QA: open `/acp` and `/` side by side in a browser against the same live or held kiro-cli-v3 session, and compare each in-scope feature's behavior directly — this is a runtime-verification-heavy plan (`/qqa`/`/qbrowser-test` per AGENTS.md), not one where code-level inspection alone suffices, since the whole point is dashboard-vs-`/acp` behavioral parity.
