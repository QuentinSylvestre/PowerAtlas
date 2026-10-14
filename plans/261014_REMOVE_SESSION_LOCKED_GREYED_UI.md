# Remove Session Locked/Greyed UI for Non-ACP Providers

> **Date**: 2026-10-14
> **Status**: Draft  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
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
- `data_codex.py`: `session_writer_locked` function — kept for `resume.py`.

---

## 1) Current State

**`_acp_availability`** (`web.py:3275`): computes "held"/"locked"/"available" for kiro-cli v3
sessions. Currently calls `presence.get_snapshot().any_live_process(...)` at line 3307 and
`acp._lock_holder_v3(sid, wh)` at line 3325 for non-held `sess_`-prefixed ids. Returns
`"locked"` when `_lock_holder_v3` is non-None. Runs under `asyncio.to_thread`; receives
`held = frozenset(supervisor.sessions)` as an immutable snapshot from the event loop.

**`_mark_resume_locked`** (`web.py:823`): adds `resume_locked: True` to Codex rows when
`data_codex.session_writer_locked(session.session_id)` returns True. Called at:
- `web.py:~4106` inside `_acp_listing` (grouped/project mode)
- `web.py:~4298` inside `_acp_flat_listing` (mode=recent)
Not called for any other provider. `resume_locked` never appears on `api_acp/sessions`
responses (tested and confirmed).

**`_lock_holder_v3` callers**: two sites only:
- `web.py:3325` inside `_acp_availability` → **to remove**
- `web.py:5025` inside `_acp_delete_many` → **to keep** (delete gate)

**`session_writer_locked` callers**:
- `web.py:834` in `_mark_resume_locked` → **to remove**
- `resume.py:247` in resume-now gate → **to keep**
- `resume.py:354` in schedule `open_terminal` field → **to keep**

**Frontend — `index.html`** locked-state code:
- Line 6954: `DASH_RAIL_AVAILABILITY.locked = 'locked'`
- Lines 6968, 6974: `'locked'` in `DASH_STATUS_BUCKET_ORDER` and `DASH_STATUS_BUCKET_LABEL`
- Line 6978: `if (avail === 'locked') return 'locked';` in `dashStatusBucketKey`
- Lines 2090, 2106-2110: Overview tile inert / `.is-locked` class / `tile.disabled` when locked
- Lines 7596-7600: `row.disabled = true; row.title = '...'` when `state === 'locked'`
- Line 7655: click guard `if (state === 'locked') return;`
- Lines 7665-7666: Resume button guard `state !== 'locked' && !session.resume_locked`
- Lines 3910-3917: composer `dashMaybeAttach` locked state (shows "open elsewhere" banner)
- Lines 8946-8948: `dashRailRefresh` diff check `!!s.resume_locked !== !!fresh.resume_locked`

**Frontend — `acp.html`** locked-state code:
- Line 943: `RAIL_AVAILABILITY.locked = 'locked'`
- Lines 997, 1003: bucket order/label
- Line 1007: `RAIL_LOCKED_REASON` constant
- Line 3058: `railBucketKey` locked branch
- Lines 3296-3409: `var locked = state === 'locked'` + `row.disabled`, `row.title`, click guard
- Lines 3543-3545: Delete button `disabled = true` when `state === 'locked'`

**CSS** (`style.css:1435-1436`): `.dash-ov-tile.is-locked { cursor: default; opacity: 0.55; }`
and hover reset.

**Tests needing change** (`tests/test_web.py`):
- `TestAcpAvailabilityV3` (line 21731): 8 tests; 5 specifically test the locked/v3 path
- `TestSessionAvailabilityAPI.test_locked_session_reports_locked_with_no_status` (~1265)
- `TestAcpListingEndpoint` (~21292, ~21325, ~21354): 3 tests
- `TestCodexLiveDotAndResumeLock` (line 43904): 6 tests, entire class to remove
- `test_workflow_state_session_availability_does_not_overlay_a_session_it_does_not_hold` (~44645)
- All-providers field set test (~43112): remove `"resume_locked"` from allowed fields set

---

## 2) Goal

Simplify `_acp_availability` to always return "available" for non-held sessions; remove
`_mark_resume_locked` and `resume_locked` entirely; remove all locked-state UI from
`index.html`, `acp.html`, and `style.css`.

---

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Keep or remove the `any_live_process` short-circuit | Remove — dead code once `_lock_holder_v3` call is gone | Keep it | Without `_lock_holder_v3` in `_acp_availability`, the snapshot call has no effect; dead code misleads. |
| Keep or remove `_lock_holder_v3` itself | Keep — `_acp_delete_many` still needs it | Remove | Delete gate is a write-safety check; deleting a live session would corrupt the turn. User confirmed at /qexplore. |
| Keep or remove `resume.py` Codex writer-lock gates | Keep | Remove | Backend refusals (`resume.py:247`, `open_terminal` flag) are provider-reported errors, not UI hints — consistent with the stated goal. |
| `_acp_availability` simplification | Dict comprehension: `{sid: "held" if sid in held else "available" for sid in session_ids}` | Leave existing control flow, just delete the locked branch | Function is now trivial; the comprehension form removes all dead branches cleanly. Preserve the docstring update. |
| `presence` import in `web.py` | Keep | Remove | `presence.get_snapshot()` is still called in `_acp_listing` and `_acp_flat_listing` for other purposes (liveness dot, Overview). |

---

## 4) External Dependencies & Costs

### Required external changes

None.

### Cost impact

Minor performance gain: `_acp_availability` no longer calls `presence.get_snapshot()` or
`acp._lock_holder_v3`, saving ~0.12-0.21 ms per id on listing refreshes.

---

## 5) Implementation Phases

### Phase 1: Remove locked state from Python backend [QA] [P:2]

**Goal**: Simplify `_acp_availability` to always return "available" for non-held sessions;
remove `_mark_resume_locked` and its two call sites; update all affected tests.

**Covers**: SC-2, SC-3, SC-5 (Python side), SC-6 (Python tests)

**File scope**: `src/power_atlas/web.py`, `tests/test_web.py`

**Details:**

1. **`web.py` — simplify `_acp_availability` (lines 3275-3330)**

   Replace the entire function body with:
   ```python
   # Sketch — final form uses the simplified comprehension
   def _acp_availability(session_ids, held,
                          workspace_hashes: dict[str, str] | None = None) -> dict[str, str]:
       """Map session ids to their availability state — 'held' or 'available'.

       'held': this PowerAtlas supervisor currently holds the session.
       'available': all other sessions — always available; the provider's own
       CLI or session/load call surfaces an in-use error if the session is
       genuinely held elsewhere.

       workspace_hashes parameter kept for API compatibility but no longer used.
       Runs under asyncio.to_thread; held is a snapshot from the event loop.
       """
       return {sid: ("held" if sid in held else "available") for sid in session_ids}
   ```

   Remove: `kiro_alive = presence.get_snapshot()...` (line 3307), `any_live_process` call,
   `if not kiro_alive` branch, `_lock_holder_v3` call block, the `try`/`except Exception`
   wrapper. Do NOT remove the `presence` import from `web.py` — it is still used elsewhere.

2. **`web.py` — remove `_mark_resume_locked` (line 823-835)**

   Delete the entire function. Then find and remove both call sites:
   - Search `web.py` for `_mark_resume_locked` to locate the exact lines in `_acp_listing`
     and `_acp_flat_listing` (approximately lines 4106 and 4298). Remove just the call line
     (`_mark_resume_locked(row, s, provider)` or similar).

3. **`tests/test_web.py` — update `TestAcpAvailabilityV3` (line 21731)**

   **Delete** these tests (test locked behavior that no longer exists):
   - `test_a_held_v3_session_is_reported_locked` (line 21737)
   - `test_workspace_hash_is_threaded_to_lock_holder_v3` (line 21785)
   - `test_missing_workspace_hash_omits_the_argument` (line 21810)
   - `test_acp_availability_no_kiro_process_unlocks_in_progress` (line 21835)
   - `test_acp_availability_kiro_running_falls_through_to_lock_holder` (line 21856)

   **Update** these tests (remove presence mock — no longer needed):
   - `test_an_idle_v3_session_is_reported_available` (line 21751): remove the
     `monkeypatch.setattr(presence_mod, "get_snapshot", ...)` call. The test now simply
     calls `_acp_availability([sid], held=set())` and asserts `result[sid] == "available"`.
   - `test_a_held_v3_session_already_in_the_held_set_is_held_not_locked` (line 21764):
     same — remove presence mock. Core assertion (held = "held") unchanged.
   - `test_acp_availability_kiro_running_acp_none_still_available` (line 21873): remove
     presence mock and `acp=None` setup — non-held sessions are always "available" now
     regardless of acp state. Simplify to: `result = _acp_availability([sid], frozenset());
     assert result[sid] == "available"`.

   **Add** one new test to replace the deleted coverage:
   - `test_acp_availability_always_available_for_non_held_v3_sessions`: calls
     `_acp_availability(["sess_1", "sess_2", "non-sess-3"], held={"sess_2"})` and asserts
     `result == {"sess_1": "available", "sess_2": "held", "non-sess-3": "available"}`.
     No mocks needed. Tests the simplified behavior end-to-end.

4. **`tests/test_web.py` — update `TestSessionAvailabilityAPI`**

   Find `test_locked_session_reports_locked_with_no_status` (~line 1265). Change
   the assertion from `body["availability"] == "locked"` to `body["availability"] == "available"`.
   Update the test name to `test_locked_session_now_reports_available` (reflects new behavior).
   Also update `body["status"] == ""` → still correct (non-held sessions have no status).

5. **`tests/test_web.py` — update `TestAcpListingEndpoint`**

   Find the three affected tests:
   - `test_the_supervisor_snapshot_happens_on_the_loop` (~line 21325): change
     `rows["sess_locked-1"]["availability"]` assertion from `"locked"` to `"available"`.
   - `test_availability_is_computed_only_for_returned_rows` (~line 21292): this test
     uses a spy on `_lock_holder_v3` calls. After simplification, `_lock_holder_v3` is
     never called from `_acp_availability`, so the spy assertion will fail. Rewrite the test
     to assert that `_acp_availability` is still called and returns the right values (all
     non-held sessions are "available"); remove the `_lock_holder_v3` spy entirely.
   - `test_the_lock_checks_run_off_the_event_loop` (~line 21354): if this test asserts
     `_lock_holder_v3` is called off-loop, rewrite to assert `_acp_availability` runs in a
     thread (the original intent) without the lock check detail.

6. **`tests/test_web.py` — split `TestCodexLiveDotAndResumeLock` (line 43904)**

   This class has **16 tests**, not 6. Tests 1-9 and test 16 cover Codex live-dot detection
   (`test_an_old_mtime_with_a_recent_last_record_is_live`, etc.) — **these test SC-5-protected
   behavior (liveness dot unchanged) and must be preserved.** Tests 10-15 cover `resume_locked`
   and must be deleted.

   **Rename the class** from `TestCodexLiveDotAndResumeLock` to `TestCodexLiveDot`.

   **Delete these 6 tests** (the resume_locked tests, identified by their names):
   - `test_a_codex_row_carries_resume_locked_only_when_its_lock_is_held`
   - `test_the_lock_never_touches_availability_or_status_or_the_other_fields`
   - `test_a_row_of_another_provider_never_carries_it_and_is_never_probed`
   - `test_the_flat_listing_marks_codex_rows_the_same_way`
   - `test_the_acp_route_never_carries_the_field`
   - `test_the_row_builders_run_off_the_event_loop`

   **Keep the remaining 10 tests** — do NOT delete the class or any other tests in it.

7. **`tests/test_web.py` — delete one test in `TestCodexAdapterThroughTheDashboardListing`**

   `TestCodexAdapterThroughTheDashboardListing.test_resume_locked_rides_a_held_codex_row_only_and_only_codex_is_probed`
   (line 44206): this test asserts `rows[live]["resume_locked"] is True`. After removing
   `_mark_resume_locked`, the field is never emitted, so this assertion fails. **Delete this
   one test.** The class exists at line 44136; do not touch other tests in it.

7. **`tests/test_web.py` — remaining references to `resume_locked` or `"locked"` availability**

   Search `tests/test_web.py` for any remaining references:
   - `test_workflow_state_session_availability_does_not_overlay_a_session_it_does_not_hold`
     (~line 44645): update `body["availability"] == "locked"` to `== "available"`.
   - `test_locked_session_reports_locked_with_no_status` (after rename, ~line 1265): also
     **remove the `presence.get_snapshot` and `_lock_holder_v3` monkeypatches** — neither
     function is called after the simplification; keeping dead mocks misleads readers.
   - `test_availability_is_computed_only_for_returned_rows` (~line 21292): after removing
     the `_lock_holder_v3` spy, **add a spy on `_acp_availability` itself** (via `monkeypatch`)
     to verify it is called with `session_ids` equal to the ids of the returned page rows
     only (not all 240 sessions). This preserves the test's original invariant.
   - `test_it_fails_open_to_available_when_the_lock_check_raises` (~line 21369, in
     `TestAcpListingEndpoint`): patches `_lock_holder_v3` to raise `OSError`. After the
     change, `_lock_holder_v3` is never called from `_acp_availability`; this test passes
     vacuously and covers dead behavior. **Delete it.**
   - The all-providers field set test (~line 43112): remove `"resume_locked"` from the
     allowed field set in the assertion.
   - Run `grep -n 'resume_locked\|"locked"' tests/test_web.py` before committing to catch
     any remaining references (excludes `_acp_delete_many`-context lines).

**Exit criteria**:
- [ ] `_acp_availability` returns only `"held"` or `"available"`; never `"locked"`.
  `workspace_hashes` parameter retained for API compatibility but unused.
- [ ] `_mark_resume_locked` function absent from `web.py`; no call sites remain.
- [ ] `presence.get_snapshot()` no longer called inside `_acp_availability`.
- [ ] `TestAcpAvailabilityV3`: 5 tests deleted, 3 updated, 1 new test added (4 total).
- [ ] `TestCodexLiveDotAndResumeLock` class absent from `tests/test_web.py`.
- [ ] No remaining `"locked"` availability assertions or `resume_locked` field references in
  `tests/test_web.py` (verified by grep).
- [ ] `pytest tests/test_web.py` passes (full suite).
- [ ] `memory/MEMORY.md` "v3 session liveness" decision entry updated per Documentation Updates table.

---

### Phase 2: Remove locked state from frontend [QA] [P:1]

**Goal**: Remove all `availability === 'locked'` and `resume_locked` handling from
`index.html`, `acp.html`, and `style.css`. Remove the "Locked elsewhere" / "Locked" buckets.

**Covers**: SC-1, SC-4, SC-5 (frontend side), SC-6 (frontend — no new tests needed as
no acp_page.test.mjs tests currently cover locked-state behavior)

**File scope**: `src/power_atlas/templates/index.html`, `src/power_atlas/templates/acp.html`,
`src/power_atlas/static/style.css`, `README.md`

**Details:**

**`index.html` — remove locked-state code at these locations:**

1. **Line 6954** — delete: `DASH_RAIL_AVAILABILITY.locked = 'locked';`

2. **Line 6968** — remove `'locked'` from `DASH_STATUS_BUCKET_ORDER` array. Result:
   `['working', 'waiting', 'errored', 'available']`

3. **Line 6974** — delete: `DASH_STATUS_BUCKET_LABEL.locked = 'Locked elsewhere';`

4. **`dashStatusBucketKey` (~line 6975)** — remove the locked branch:
   ```js
   // Delete this line:
   if (avail === 'locked') return 'locked';
   ```

5. **`dashOvOpenTile` (~line 2090)** — remove the locked guard. Change:
   ```js
   // Before: if (!tile || !tile.id || tile.availability === 'locked') return;
   // After:
   if (!tile || !tile.id) return;
   ```

6. **`dashOvTileNode` (~line 2106)** — remove the locked variable and all its uses:
   - Delete: `var locked = tile.availability === 'locked';`
   - In `el.className`: remove `+ (locked ? ' is-locked' : '')` — keep `idle` part
   - Delete: `el.disabled = locked;`
   - Delete: any `aria-disabled` set to `locked`, any `el.title` set for locked reason

7. **`dashRailRowNode` (~line 7596)** — remove the locked block. **This is an `if/else` not
   a standalone `if`.** Correctly remove the `if (state === 'locked') { ... } else {` head
   AND the corresponding closing `}` of the else, keeping only the body lines:
   ```js
   // Before (schematic):
   if (state === 'locked') {
     row.disabled = true;
     row.title = '...';
   } else {
     <body lines>
   }
   // After: only <body lines> remain (unwrap the else body)
   ```

8. **Row click handler (~line 7655)** — remove:
   ```js
   // Delete: if (state === 'locked') return;
   ```

9. **Resume button guard (~line 7665)** — remove `!session.resume_locked &&`:
   ```js
   // Before: if (state !== 'locked' && !session.resume_locked && (session.provider === ...))
   // After:  if (session.provider === 'kiro-cli' || session.provider === 'kiro-cli-v3' || ...)
   ```
   (Also remove the `state !== 'locked'` check — rows are never locked now.)

10. **`dashMaybeAttach` locked state (~lines 3910-3917)** — remove the
    `if (state === 'locked') { ... }` block that shows the "open elsewhere" banner and
    disables the composer. After removal, locked sessions no longer show this state.

11. **`dashRailRefresh` diff check (~lines 8946-8950)** — remove the **entire 4-line block**
    (condition + body + braces), not just the condition line:
    ```js
    // Delete this entire block (4 lines):
    if (!!s.resume_locked !== !!fresh.resume_locked) {
      s.resume_locked = fresh.resume_locked;
      changed = true;
    }
    ```

**`acp.html` — remove locked-state code at these locations:**

1. **Line 943** — delete: `RAIL_AVAILABILITY.locked = 'locked';`
2. **Line 997** — remove `'locked'` from `STATUS_BUCKET_ORDER`
3. **Line 1003** — delete: `STATUS_BUCKET_LABEL.locked = 'Locked';`
4. **Line 1007** — delete: `var RAIL_LOCKED_REASON = '...';`
5. **Comment at line 1004-1006** referencing locked row reason — delete
6. **`railBucketKey` (line 3058)** — remove: `if (avail === 'locked') return 'locked';`
7. **`railRowNode` (lines 3296-3409)** — remove all locked-dependent code:
   - Delete: `var locked = state === 'locked';`
   - In `row.className` or `el.className`: remove locked-class additions
   - Delete: `row.disabled = locked;`
   - Delete: the `if (locked) { row.title = RAIL_LOCKED_REASON; ... }` block (~lines 3355-3365)
   - Delete: the `if (!locked) row.title = note;` guard (restore to `row.title = note;`)
   - Delete: `if (locked) return;` click guard (~line 3409)
8. **Delete button disabled-when-locked (~lines 3543-3545)** — remove:
   ```js
   // Delete:
   } else if (state === 'locked') {
     del.disabled = true;
     del.title = '...';
   }
   ```
8. **`acp.html` and `index.html` — update batch-delete error messages**

   Both templates contain the string "could not be deleted (held or locked)."
   - `acp.html:~3986`: change to "could not be deleted (held by another process)."
   - `index.html:~8447`: change the identical string to "could not be deleted (held by another process)."

9. **Comment at line 919** referencing `locked` → may be updated or left as-is
   (update only if it actively misleads; do not remove factual design comments)

**`style.css` — remove locked-specific rules:**

1. **Lines 1435-1436** — delete both rules:
   ```css
   .dash-ov-tile.is-locked { cursor: default; opacity: 0.55; }
   .dash-ov-tile.is-locked:hover { background: var(--surface); }
   ```

**Exit criteria**:
- [ ] `DASH_RAIL_AVAILABILITY.locked` absent from `index.html`.
- [ ] "Locked elsewhere" absent from `index.html`; "Locked" absent from `acp.html`.
- [ ] `row.disabled = true` no longer triggered by `state === 'locked'` in either template.
- [ ] Click guards on `state === 'locked'` absent from both templates.
- [ ] `resume_locked` references absent from `index.html` (`!session.resume_locked` removed
  from Resume button guard; diff check removed).
- [ ] `.dash-ov-tile.is-locked` absent from `style.css`.
- [ ] `RAIL_LOCKED_REASON` absent from `acp.html`.
- [ ] README.md locked-state descriptions updated per Documentation Updates table.
- [ ] `acp.html` and `index.html` batch-delete error strings updated to "held by another process".
- [ ] `node tests/acp_page.test.mjs` passes (no regressions — hard reload, not restart).
- [ ] `pytest tests/test_web.py` passes (no regressions from Phase 2 changes).

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `acp.html:3986` references "held or locked" in a user-facing error message for batch delete | Low — message says "could not be deleted (held or locked)"; after change only "held" is the real reason | Update the string to "could not be deleted (held by another process)" in Phase 2 |
| `_acp_availability`'s `workspace_hashes` parameter becomes unused | Low — API signature kept for compatibility; no callers removed | Keep parameter with a note in the docstring that it is unused; callers pass it harmlessly |
| Tests in `TestAcpListingEndpoint` that spy on `_lock_holder_v3` calls | Medium — spy assertions will fail if they check call count | Phase 1 instructions explicitly rewrite those tests |
| `acp.html` comment at line 919 refers to `locked → ...` | Low | Reviewed in Phase 2; update or leave depending on context |

---

## 7) Verification

**Phase 1 (automated):**
```powershell
# Grep to confirm no "locked" availability or resume_locked in tests
Select-String -Path tests/test_web.py -Pattern '"locked"' | Where-Object { $_.Line -notmatch "#|delete_many|_acp_delete" }
Select-String -Path tests/test_web.py -Pattern "resume_locked"
# Run full suite
.venv-PowerAtlas\Scripts\python -m pytest tests/test_web.py --timeout=300 -q
```

**Phase 2 (automated):**
```powershell
node tests/acp_page.test.mjs
# Grep to confirm no locked UI remnants
Select-String -Path src/power_atlas/templates/index.html -Pattern "\.locked|resume_locked|Locked elsewhere"
Select-String -Path src/power_atlas/templates/acp.html -Pattern "RAIL_LOCKED_REASON|LOCKED_REASON|\.locked"
Select-String -Path src/power_atlas/static/style.css -Pattern "is-locked"
```

**Manual browser (SC-1)**:
1. Start PA; sign in via tray. Hard reload after Phase 2 changes.
2. Find a kiro-cli terminal session that was previously greyed out (or manually set one's
   `session.json` `"status"` to `"in_progress"`). Confirm the row is now clickable.
3. In `/acp`, switch to Status grouping. Confirm "Locked elsewhere" / "Locked" bucket absent.
4. For a Codex thread with a running terminal: confirm Resume button is visible (not hidden);
   clicking it may return an error from the backend — that is correct behavior.

*(PA restart required for Python changes. Hard reload for JS/template changes only.)*

---

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `docs/KNOWLEDGE.md` | No update needed — search found no relevant hits for removed identifiers. | — |
| `README.md` line 239 | Remove "greys out sessions another process currently holds" from the feature description. | 2 |
| `README.md` line 411 | Remove "or *locked* by another process" from rail row state description. | 2 |
| `README.md` line 419 | Remove "Locked" from the status-group bucket list; list becomes "Working / Waiting / Errored / Available". | 2 |
| `memory/MEMORY.md` | Append Update annotation to the "v3 session liveness" decision entry noting `_lock_holder_v3` and `any_live_process` removed from `_acp_availability`; function is now a trivial `{sid: "held" if sid in held else "available" for sid in session_ids}` comprehension; delete gate unchanged. | 1 (doc-table-only) |
| `docs/KNOWLEDGE.md` | Line ~253: paragraph stating PA hides Resume button based on the Codex writer lock is stale — the lock still gates the backend `resume.py:247` but PA no longer hides the button. Append an Update annotation to that paragraph. | 1 (doc-table-only) |

---

## Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Remove locked state — Python backend | Not started | |
| 2 | Remove locked state — Frontend | Not started | |

---

## 9) Implementation Divergences from Plan
<Reserved — filled during implementation>

## Follow-up Work (Deferred)
<Reserved — appended during /qplan, /qdev, /qreview>

## Review Log

### 2026-10-14 — Plan Creation (via /qplan)

Full-effort review (4 personas: Architect, Senior engineer, Domain expert JS, Reliability engineer). 1 cycle. 12 auto-resolved findings.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `TestCodexLiveDotAndResumeLock` has 16 tests, not 6; tests 1-9 and 16 cover live-dot detection (SC-5 protected) and must be kept. | Fixed — plan now says split: rename class to `TestCodexLiveDot`, delete only the 6 resume_locked tests by name, keep 10. |
| 2 | High | `TestCodexAdapterThroughTheDashboardListing.test_resume_locked_rides_a_held_codex_row_only_and_only_codex_is_probed` (line 44206) asserts `resume_locked is True` — breaks after `_mark_resume_locked` removed. | Fixed — added as explicit delete target in Phase 1 Step 7. |
| 3 | High | `index.html:~8447` has the same "held or locked" batch-delete message as `acp.html:3986`; neither was wired into Phase 2 steps with exit criteria. | Fixed — Phase 2 step 8 now covers both templates; exit criterion added. |
| 4 | Medium | `dashRailRefresh` diff-check block is 4 lines (condition + assignment body); plan said "remove the condition" which could leave the body running unconditionally. | Fixed — Phase 2 step 11 now says "remove the entire 4-line block." |
| 5 | Medium | `test_availability_is_computed_only_for_returned_rows` rewrite lacked a replacement assertion for the original invariant (availability computed only for returned rows). | Fixed — Phase 1 Step 7 now specifies: spy on `_acp_availability` and assert `session_ids` equals returned-row ids. |
| 6 | Medium | `test_it_fails_open_to_available_when_the_lock_check_raises` not in plan's test list; patches `_lock_holder_v3` to raise — passes vacuously after change. | Fixed — added to Phase 1 Step 7 deletion list. |
| 7 | Medium | `test_locked_session_reports_locked_with_no_status` plan says to flip assertion but didn't say to remove the dead `presence.get_snapshot` and `_lock_holder_v3` mocks. | Fixed — Phase 1 Step 7 now explicitly removes both dead mocks. |
| 8 | Low | Phase 2 step 7 (`dashRailRowNode` locked block) wording ambiguous — could be read as "delete only the if branch," leaving a syntax error. | Fixed — step 7 now says explicitly: remove the full `if (...) { ... } else {` head and closing `}`, keep body lines. |
| 9 | Low | `test_the_lock_checks_run_off_the_event_loop` plan description said "rewrite if it asserts `_lock_holder_v3` off-loop" — test actually spies on `_acp_availability` directly, needs no rewrite. | Fixed — removed the conditional "if" clause; test works as-is. |
| 10 | Low | `docs/KNOWLEDGE.md:~253` references PA hiding the Resume button based on Codex writer lock — stale post-plan. Not in Documentation Updates. | Fixed — added `docs/KNOWLEDGE.md` row to Documentation Updates table. |
| 11 | Low | `_check_test_names.py` pre-commit hook: class rename `TestCodexLiveDotAndResumeLock` → `TestCodexLiveDot` must not create a duplicate name. | Accepted — rename is a rename, not a new class alongside; no collision. |
| 12 | Low | `workspace_hashes` parameter becomes unused; no test verifies callers can pass it harmlessly. | Accepted — no test needed; all callers pass it harmlessly (parameter accepted but ignored by the comprehension). |

## Harness Improvement Opportunities
<Reserved — appended during /qexplore, /qplan and /qdev when harness friction is felt.
Format: - <observation> — cost: <what the friction actually cost> — suggested change: <one-line>
Leave the heading even if empty: /qclose Pass 2 and /qdream GAT-PLANS both read it, and an
absent heading is indistinguishable from a frictionless run.>

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
