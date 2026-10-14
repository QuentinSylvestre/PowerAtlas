# Edit Scheduled Resume

> **Date**: 2026-10-14
> **Status**: Complete  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Allow the user to edit the prompt and/or fire time of an already-scheduled resume from the Overview's "Stopped by a quota limit" section. Frontend-only change to `index.html`.
> **Memory relied on**: none
> **Estimated effort**: half a day

---

## Intent

### Problem statement & desired outcomes

Once a session has a scheduled resume, the prompt and fire time are frozen. There is no way to change them without cancelling the schedule and re-creating it. The edit capability is blocked solely by a UI guard at `index.html:1642` (`if (state !== 'scheduled')`) that hides the prompt + datetime inputs when a schedule already exists. The backend already handles edits: `POST /api/quota/schedule` upserts — calling it again with the same `session_id` replaces the existing schedule — and `decorate()` already includes `prompt` in `row.resume.prompt`.

### Success criteria

- SC-1: When a session has a scheduled resume, the prompt text input and datetime-local input are visible and editable in the Overview row (not hidden).
- SC-2: Both inputs are pre-filled with the existing scheduled values (`resume.prompt` and `resume.fire_at` converted to local datetime).
- SC-3: The button reads **"Update schedule"** (not "Schedule") when editing an existing schedule.
- SC-4: Clicking "Update schedule" re-posts to `POST /api/quota/schedule` with the new prompt + new fire_at, replacing the existing schedule.
- SC-5: If the user doesn't change the time, the existing `fire_at` is sent back unchanged (no reset to default).
- SC-6: "Cancel schedule", "Resume now", and "Dismiss" remain unchanged.
- SC-7: `node tests/acp_page.test.mjs` passes after the change (regression guard).

### Scope boundaries & non-goals

**In scope**: `src/power_atlas/templates/index.html` only (the `dashOvIntActions()` function).

**Not in scope**: `/acp` page changes (quota section is Overview-only). Backend/API changes. New endpoints. Auto-schedule behavior.

---

## Exploration Discovery

1. **Files identified**: `src/power_atlas/templates/index.html` (sole write target); `src/power_atlas/resume.py` (reference — no changes); `src/power_atlas/web.py` (reference — no changes); `tests/test_web.py` (no changes needed); `tests/acp_page.test.mjs` (may need update for new UI state).

2. **Existing patterns & constraints**: `index.html` is a Jinja2 template with inline JS. `acp.html` opens with `{% extends "base.html" %}`; `index.html` is similar. CSS lives in `style.css` served with a version stamp. After template edits, a **hard reload** (`Ctrl+Shift+R`) picks up changes — no PA restart needed. The `tests/acp_page.test.mjs` harness prohibits `innerHTML` and runs against `acp.html`, but AGENTS.md requires it as a regression guard on any inline script change.

3. **Prior art**: `plans/done/261013-2321_AUTO_SCHEDULE_RESUME_MODE.md` — the ROADMAP entry "Edit scheduled message" was already called out as a follow-up from this plan ("cancel shipped, edit open").

4. **Step 1.5**: code-tracing trio dispatched. Key finding: the feature is **frontend-only**. `POST /api/quota/schedule` (`web.py:4618`) upserts without guarding for an existing schedule. `resume.schedule()` (`resume.py:350`) always replaces. `decorate()` (`resume.py:455–457`) already emits `prompt` on the scheduled row. The only two blocking guards in `index.html` are the `if (state !== 'scheduled')` check at line 1642 (hides the prompt+time `<details>` block) and the draft initialization at lines 1598–1600 (sets `prompt: ''` instead of reading `resume.prompt`).

5. **Risks & mitigations**: The draft cache (`_dashOvIntDraft`) is keyed by `row.id`. If a session is already in the draft (user opened Schedule, then a schedule fired and the state changed to 'scheduled'), the draft may hold stale values. Mitigation: when `state === 'scheduled'`, **always** overwrite the draft with the live `resume.prompt` and `resume.fire_at` on each render call, rather than lazily initializing. This ensures the pre-fill always reflects the current scheduled values even if the draft was previously touched.

6. **Open items**: None.

7. **Assumptions confirmed by user**:
   - Both prompt and fire time are editable (not prompt-only).
   - Button label changes to "Update schedule" in scheduled state.
   - No new backend endpoint — re-post to existing `/api/quota/schedule`.
   - Dashboard only, no `/acp` changes.

8. **`dashOvLocalInput` / `dashOvLocalOutput`**: The existing datetime-local `<input>` uses `dashOvLocalInput(fire_at)` to convert an epoch-seconds float to a `datetime-local` string value, and `dashOvLocalOutput(when)` to convert back before posting. These utilities are already defined and can be reused for pre-filling.


---

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Draft pre-fill on every render vs. lazy-init | Always overwrite draft when `state === 'scheduled'` | If draft was previously initialized empty (user opened Schedule before schedule armed), stale `prompt: ''` would show blank instead of the actual scheduled prompt. Overwriting every render call ensures the input always reflects the live `resume.prompt` / `resume.fire_at`. |
| Button label | "Update schedule" | Distinguishes edit-of-existing from create-of-new in the same action area |
| Show/hide time input | Show both prompt and time inputs in scheduled state (same `<details>` block) | User confirmed "might as well" — editing the time is natural alongside the prompt. Reuses existing datetime-local input and `dashOvLocalInput` / `dashOvLocalOutput` utilities. |
| API | Re-post to existing `POST /api/quota/schedule` | The route already upserts; no new endpoint needed |
| Scope | `index.html` only | `decorate()` already returns `prompt`, web route already upserts, `resume.schedule()` already replaces — zero backend work |

## 5) Implementation Phases

### Phase 1: Edit-scheduled UI [QA]

**Goal**: In `dashOvIntActions()`, show the prompt + time `<details>` block when `state === 'scheduled'`, pre-filled from `resume.prompt` / `resume.fire_at`, with button label "Update schedule".

**Covers**: SC-1, SC-2, SC-3, SC-4, SC-5, SC-6, SC-7

**File scope**: `src/power_atlas/templates/index.html`

**Changes** (all within `dashOvIntActions()` — read the function first to verify exact line numbers against what sub-agents found at index.html:1590–1692):

1. **Draft pre-fill** (currently at lines 1597–1600): When `state === 'scheduled'`, always overwrite the draft's `prompt` and `when` from the live scheduled values:

   ```javascript
   if (!draft || state === 'scheduled') {
       draft = _dashOvIntDraft[row.id] = {
           prompt: (state === 'scheduled' && resume && resume.prompt) ? resume.prompt : '',
           when: (state === 'scheduled' && resume && resume.fire_at)
               ? dashOvLocalInput(resume.fire_at)
               : dashOvLocalInput(Number(row.resets_at) + 60)
       };
   }
   ```

2. **Guard change** (currently at line 1642): Change `if (state !== 'scheduled')` to **no guard** (or `if (true)`) so the `<details>` block renders in all non-watching states. The block should now show for both unscheduled and scheduled rows.

3. **Button label** (inside the `<details>` block, the "Schedule" button): Conditionally change to "Update schedule" when `state === 'scheduled'`:

   ```javascript
   // Button text:
   state === 'scheduled' ? 'Update schedule' : 'Schedule'
   ```

   Specifically, locate the `<button>` element that triggers the schedule POST and change its label. Use `createElement + textContent` per AGENTS.md (never `innerHTML`).

**Exit criteria**:
- [x] When a session has a scheduled resume, the `<details>` section with prompt and datetime inputs is visible.
- [x] The prompt input is pre-filled with the existing `resume.prompt`.
- [x] The datetime-local input is pre-filled with the existing `resume.fire_at` value (converted via `dashOvLocalInput()`).
- [x] The button reads "Update schedule" (not "Schedule") when editing an existing schedule.
- [x] Clicking "Update schedule" posts `{session_id, prompt, fire_at}` to `/api/quota/schedule` and the row updates to reflect the new values after the Overview re-polls.
- [x] If `fire_at` is unchanged, the same time is sent back to the server.
- [x] "Cancel schedule", "Resume now", "Dismiss" still work.
- [x] `node tests/acp_page.test.mjs` passes.

Implementation (2026-10-14, code: 54f2c97, fix: 534a1a8)
`index.html` — `dashOvIntActions()` changes: (1) Draft pre-fill uses `_from` tracker to avoid overwriting in-progress edits on every 2s re-render — only re-seeds when server's `resume.prompt`/`resume.fire_at` changes; (2) `if (state !== 'scheduled')` guard on prompt+time `<details>` block removed so inputs show for scheduled rows; (3) Button label is "Update schedule" when `state === 'scheduled'`; (4) `promptNow()` unified to always use `draft.prompt` (pre-filled from `resume.prompt`, so behavior is unchanged until user edits the field); (5) duplicate `var state` removed. `tests/acp_page.test.mjs` updated: scheduled-row test now asserts inputs visible, prompt pre-filled, "Update schedule" button present, POST body correct on click, fire_at value asserted.

Review (2026-10-14, full effort, Senior engineer): 5 findings (0 High, 2 Medium, 3 Low). 5 auto-fixed (534a1a8) per 1-cycle cap.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | Draft re-seeded on every render for scheduled rows — in-progress edits discarded | Fixed — `_from` tracker added (534a1a8) |
| 2 | Medium | SC-4/SC-5 not tested | Fixed — added "Update schedule" click test (534a1a8) |
| 3 | Low | Duplicate `var state` declaration | Fixed — removed (534a1a8) |
| 4 | Low | `schedWhen.value` not asserted in test | Fixed — added value assertion (534a1a8) |
| 5 | Low | `promptNow()` used `resume.prompt` instead of `draft.prompt` — discarded visible edits on Resume now | Fixed — unified to `draft.prompt` (534a1a8) |



| Risk | Impact | Mitigation |
|---|---|---|
| Draft overwrite clobbers user's in-progress edit | Medium | Only overwrite when `state === 'scheduled'`; once the user starts typing in the inputs, the draft will reflect their edits. Re-render on poll updates the scheduled values but the JS template re-runs `dashOvIntActions()` — check whether draft is re-initialized on each render. If it is, the user's in-progress edits may be lost on a 2 s poll. **Resolution at implementation**: if `dashOvIntActions()` is called on every poll render, only pre-fill when there is no existing draft (standard lazy-init) rather than on every call; if called once per row, always pre-fill is safe. |
| `<details>` open/closed state reset on re-render | Low | Each re-render of the row recreates the DOM node — the `<details>` `open` attribute is reset. Acceptable; the user must manually re-expand. |
| `acp_page.test.mjs` asserts on pre-existing prompt visibility | Low | The test covers `acp.html`, not `index.html`. If any test checks that the prompt block is hidden in the scheduled state, it would fail. Inspect test for existing scheduled-state assertions and update as needed. |

## 7) Verification

```powershell
cd c:\Users\QSylvestre.POLESTAR\Documents\Perso\PowerAtlas
node tests/acp_page.test.mjs 2>&1 | Select-Object -Last 10
.venv-PowerAtlas\Scripts\python -m pytest tests/test_web.py --timeout=300 -q 2>&1 | Select-Object -Last 5
```

Browser QA (requires running PowerAtlas + pa_local cookie):
1. Open Overview — find a session with a scheduled resume ("Resumes in X min")
2. Verify prompt input and datetime input are visible and pre-filled
3. Edit the prompt → click "Update schedule" → row updates
4. Edit the time → click "Update schedule" → row updates with new time
5. "Cancel schedule" still works

## 8) Documentation Updates

| Document | Update needed |
|---|---|
| `README.md` | Update "Stopped by a quota limit" section to mention prompt/time editing |
| `AGENTS.md` | Update "Edit scheduled message" Terminology entry with Settled ref |
| `plans/ROADMAP.md` | Mark "Edit scheduled message" as shipped |

## Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Edit-scheduled UI | Complete | code: 54f2c97, fix: 534a1a8 |

## 9) Implementation Divergences from Plan

`tests/acp_page.test.mjs` was committed alongside `index.html` in Phase 1 (per AGENTS.md "update existing tests when implementation changes").

## Review Log

### 2026-10-14 — /qdev Phase 1 (full effort, Senior engineer)

5 findings (0 High, 2 Medium, 3 Low). 5 auto-fixed (534a1a8), 1-cycle cap.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | Draft re-seeded on every render for scheduled rows | Fixed — `_from` tracker (534a1a8) |
| 2 | Medium | SC-4/SC-5 POST not tested | Fixed — added click+POST test (534a1a8) |
| 3 | Low | Duplicate `var state` | Fixed (534a1a8) |
| 4 | Low | `schedWhen.value` not asserted | Fixed (534a1a8) |
| 5 | Low | `promptNow()` ignored draft edits | Fixed — unified to `draft.prompt` (534a1a8) |

### 2026-10-14 — /qdev Step 9 final holistic (full effort, Senior engineer)

5 findings (0 High, 2 Medium, 3 Low). 5 auto-fixed (5bcaaa1).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | ROADMAP.md not updated | Fixed — both locations (5bcaaa1) |
| 2 | Medium | README.md not updated | Fixed — Update schedule sentence (5bcaaa1) |
| 3 | Low | AGENTS.md controls list | Fixed (5bcaaa1) |
| 4 | Low | Optional chaining style | Fixed — explicit null guard (5bcaaa1) |
| 5 | Low | SC-5 fire_at value not asserted | Fixed — round-trip assertion (5bcaaa1) |

Step 9b QA (2026-10-14): PASS via JS tests (1112 pass, Update-schedule click/POST/fire_at all covered); BLOCKED for live scheduled-row (requires real quota stop).


## Harness Improvement Opportunities

*None recorded.*
