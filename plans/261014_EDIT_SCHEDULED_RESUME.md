# Edit Scheduled Resume

> **Date**: 2026-10-14
> **Status**: Draft  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
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

