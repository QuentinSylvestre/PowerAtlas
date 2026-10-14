# Remove Session Locked/Greyed UI for Non-ACP Providers

> **Date**: 2026-10-14
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Remove availability="locked" and resume_locked UI for all non-ACP session rows
> **Memory relied on**: none
> **Estimated effort**: 1 day

---

## Intent

### Problem statement & desired outcomes

PA only directly controls ACP sessions. For every other provider — terminal kiro-cli v3
sessions, Claude Code, Codex, Kiro IDE — the `availability = "locked"` and `resume_locked`
states are best-effort guesses. When they misfire (stale mtime, psutil unavailable, cwd
unreadable), they silently strand users: the row is greyed out, unclickable, with no recovery
path except waiting.

The guesses are also redundant: if a session is genuinely held, the provider's own CLI
(`kiro-cli`, `codex resume`) or PA's own `session/load` call will refuse the request and
surface a clear error to the user.

**Desired outcome**: remove the grey-out for all non-PA-held session rows. Every row is
always clickable. Liveness dots (working/idle/waiting/errored) remain. The delete gate
(which is a write-safety check, not a UI hint) remains.

### Success criteria

- SC-1: All non-held session rows in the dashboard rail and `/acp` rail are always
  clickable regardless of their `session.json` status or Codex writer-lock state.
- SC-2: `availability = "locked"` is never returned from `_acp_availability` or any
  listing endpoint; every non-held session returns `"available"`.
- SC-3: `resume_locked` field is no longer emitted in any session row JSON.
- SC-4: "Locked elsewhere" bucket removed from status-grouped view in both
  `index.html` and `acp.html`.
- SC-5: Liveness dot (working/idle/waiting/errored), ACP `availability = "held"` state,
  and the delete gate (`_acp_delete_many`) are unchanged.
- SC-6: All existing tests pass; updated tests reflect the removed locked states.

### Scope boundaries & non-goals

**In scope:**
- `web.py`: simplify `_acp_availability`; remove `_mark_resume_locked` and its call sites.
- `acp.py`: `_lock_holder_v3` and `_V3_SESSION_STALE_SECONDS` kept (still used by delete gate).
- `data_codex.py`: `session_writer_locked` kept (still used by `resume.py`).
- `index.html`: remove locked row state, "Locked elsewhere" bucket, `resume_locked` Resume guard.
- `acp.html`: remove locked row state, "Locked" bucket, locked Delete-button disable.
- `style.css`: remove `.dash-ov-tile.is-locked` and any other locked-specific rules.
- `tests/test_web.py`: update `TestAcpAvailabilityV3`, `TestSessionAvailabilityAPI`,
  `TestAcpListingEndpoint`, `TestCodexLiveDotAndResumeLock`.

**Out of scope:**
- Delete gate: `_acp_delete_many` still calls `_lock_holder_v3` and refuses deletion of a
  held session — this is a write-safety gate, not a UI hint.
- `resume.py`: backend still refuses resume-now for a Codex thread whose writer lock is held
  (`resume.py:247`); `open_terminal` field in schedule response stays.
- Liveness dot feature (working/idle/waiting/errored).
- `_lock_holder_v3` function itself — kept for the delete gate.
- `data_codex.session_writer_locked` function — kept for `resume.py`.

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4) Existing patterns & constraints

- `_acp_availability` (`web.py:3275`) currently returns "held"/"locked"/"available". After
  change, the "locked" branch is removed and the function simplifies to:
  `{sid: "held" if sid in held else "available" for sid in session_ids}`.
  The `any_live_process` short-circuit (added in plan 261014_STALE_LOCK…) becomes dead code
  and should be removed alongside. All callers pass output directly to JSON — no Python-side
  branching on "locked" exists.
- `availability = "locked"` is ONLY produced by `_acp_availability` for `sess_`-prefixed
  kiro-cli v3 sessions. Claude Code, Kiro IDE, Codex never receive this value.
- `resume_locked = true` appears only in `include_provider=True` responses
  (`api_dashboard_sessions`), never in `api_acp_sessions`.
- `_lock_holder_v3` has exactly two callers: `_acp_availability` (to remove) and
  `_acp_delete_many` (`web.py:5025`, to keep).
- `session_writer_locked` has callers in `web._mark_resume_locked` (to remove),
  `resume.py:247` (resume-now gate, keep), `resume.py:354` (schedule `open_terminal`, keep).
- `AGENTS.md` rule: never restart PowerAtlas autonomously unless the user grants it.
  Python changes to `web.py` require a restart; JS/template changes need only a hard reload.
- `tests/acp_page.test.mjs` has no locked-state tests (nothing to remove there).
- `acp.html` has its own locked-state code (`RAIL_AVAILABILITY.locked`, `RAIL_LOCKED_REASON`,
  `STATUS_BUCKET_LABEL.locked`, `railBucketKey` locked branch, `railRowNode` locked disable,
  `railMenuNode` Delete button disabled-when-locked at `acp.html:3543-3545`).

### 5) Risks & mitigations

- **Removing `any_live_process` short-circuit**: The short-circuit in `_acp_availability`
  was added in 261014_STALE_LOCK… specifically to short-circuit before `_lock_holder_v3`.
  If `_lock_holder_v3` is also removed from `_acp_availability`, the short-circuit is dead
  code and should be deleted too. Mitigation: remove both in Phase 1.
- **`api_session_availability` endpoint**: currently returns `availability: "locked"` for
  non-held kiro sessions that are held. After change, returns "available" → the dashboard
  transcript composer tries to attach to the session → kiro refuses via `session/load`.
  This is the intended UX per the stated goal. No mitigation needed.
- **`TestCodexLiveDotAndResumeLock` class**: 6 tests test `resume_locked` behavior;
  most will need to be deleted or rewritten. The class may need renaming (the `ResumeLock`
  part no longer exists). The `data_codex.session_writer_locked` function itself is still
  tested in `tests/test_data.py` (~line 5137) and those tests should be unaffected.

### 6) Resolved decisions

- Q1: Should the delete gate in `_acp_delete_many` also be removed? — A: No. Keep the gate.
  Deleting a session mid-turn would corrupt the turn. — Decision: `_lock_holder_v3` stays in
  `_acp_delete_many`; removed only from `_acp_availability`.

### 7) Open items

- None.

### 8) Recommended approach

**Phase 1 (Python backend)**: Simplify `_acp_availability` to remove the `_lock_holder_v3`
call and the `any_live_process` short-circuit; always return "available" for non-held
sessions. Remove `_mark_resume_locked` function and its two call sites in `_acp_listing`
and `_acp_flat_listing`. Update tests: `TestAcpAvailabilityV3` (remove 3 locked-path tests,
update 2 others), `TestSessionAvailabilityAPI`, `TestAcpListingEndpoint`,
`TestCodexLiveDotAndResumeLock` (remove or rewrite 5 of 6 tests).

**Phase 2 (Frontend)**: Remove locked-state code from `index.html` (row disabled, click
guard, "Locked elsewhere" bucket, `resume_locked` Resume guard, Overview tile inert,
composer locked state), `acp.html` (same + Delete button disable), `style.css`
(`.dash-ov-tile.is-locked`).

The two phases are parallelizable (non-overlapping file scopes).

### 9) QA environment

- Automated: `pytest tests/test_web.py` (3451 tests), `node tests/acp_page.test.mjs` (1119 tests).
- Browser: PA running at `http://127.0.0.1:4915`; sign in via tray or `pa_local` cookie
  (see `AGENTS.md § Verification Setup`). Hard reload picks up JS/template changes.
  PA restart required for Python changes.
- Manual SC-1 check: find a session that was previously greyed out (any terminal kiro-cli
  session or a recently-crashed session); confirm row is clickable; click it and confirm PA
  attempts to open (kiro may refuse if legitimately held — that's correct).
