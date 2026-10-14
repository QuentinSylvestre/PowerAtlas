# Auto-Schedule Resume Mode

> **Date**: 2026-10-13
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Single PA-wide boolean setting that automatically schedules a resume for every session stopped by a quota limit, without requiring the user to click "Schedule" manually. Gear-menu toggle. Integrates with existing "Stopped by a quota limit" section.

---

## Intent

### Problem statement & desired outcomes

The "Stopped by a quota limit" section in the Overview's "Needs attention" panel lists sessions whose quota limit stopped them, with a "Schedule" button per row. When several sessions are stopped simultaneously — or the user leaves PA running overnight — every one of them requires a manual schedule click. Auto-schedule mode removes that friction: once enabled, PA schedules every resumable stopped session automatically at `resets_at + 60 s` (the existing default delay), so the user wakes up to resumed sessions without any extra interaction.

The setting is global (applies to all providers, all workspaces) and toggled from the gear menu alongside "Notify me" and "Autostart on login."

### Success criteria

1. A toggle "Auto-schedule resumes" appears in the gear menu (same surface as "Notify me").
2. When on: within one scheduler tick (~5 s), every session currently in "Stopped by a quota limit" that has a known `resets_at`, is not already scheduled/watching, is not dismissed, and is not skipped, is automatically scheduled at `resets_at + FIRE_DELAY_SECONDS`.
3. When on: a manually-set schedule (custom time or prompt) is never overwritten by auto-schedule.
4. When on: clicking "Cancel schedule" on an auto-scheduled row removes the schedule AND records a per-stop skip entry, so the session stays in the list but is not re-armed by auto-schedule for that stop.
5. When the user manually clicks "Schedule" on a skipped session, the skip record is cleared and a new schedule is created; future auto-schedule ticks treat the session normally.
6. kiro-cli-v3 sessions (no `resets_at`) and "ready" sessions (quota already reset) are never auto-scheduled.
7. Toggling auto-schedule off leaves all existing schedules in place; they fire as scheduled.
8. The setting persists across PA restarts.
9. All existing resume paths (manual Resume now, Schedule, Cancel, Dismiss) continue to work identically when auto-schedule is off.

### Scope boundaries & non-goals

**In scope:**
- `auto_schedule: bool = False` top-level `Config` field + `GET/POST /api/quota/auto-schedule` route pair
- `_auto_schedule_new(now)` helper called from `resume.tick()`
- `skipped` list added to `quota-state.json` (keyed by `session_id + key_at`, same shape as `dismissed`)
- `_if_unscheduled=True` guard parameter on `schedule()` (prevents clobbering manual schedules and watching runs)
- Modified `cancel` endpoint: adds to `skipped` when auto-schedule is on
- Gear-menu toggle in `index.html` (existing quick-menu section, same row pattern as "Notify me")
- `skipped` records pruned after `DISMISS_KEEP_SECONDS` (14 days)

**Not in scope:**
- Per-session auto-schedule override preferences
- Configurable prompt for auto-scheduled resumes (always uses the default "resume")
- Configurable delay (always uses `FIRE_DELAY_SECONDS = 60 s`)
- `/acp` page changes (quota section is Overview-only)
- kiro-cli-v3 sessions (excluded by existing `resets_at is None` guard in `schedule()`)

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

- **`resume.tick()` integration point** (`resume.py:295`): called every `TICK_SECONDS = 5` s on the scheduler thread via `_loop`. After the schedule-firing `while True` loop and before the watch loop. Sequential, never concurrent with itself. Already calls `load_config()` in `_notify()` (same file) — one more TOML parse per 5 s tick is acceptable.
- **`quota.interrupted(now)` is O(1) from cache** (`quota.py:~261`): memoised on `(mtime_ns, size)` per file. Calling it from `tick()` adds no file I/O unless transcripts changed.
- **`schedule()` single-lock serialization** (`resume.py:186`): all callers (manual + auto) serialize on `_lock`. The `_if_unscheduled` guard check must happen inside that same lock acquisition to be race-safe.
- **`_SETTING_TYPES` deliberately excludes booleans** (`web.py:5614`): `isinstance(True, int)` is `True` in Python, so the save-setting route rejects every bool. `auto_schedule` needs its own route pair following the `notifications` pattern (`web.py:5634–5657`).
- **`Config` bool field round-trip verified**: `tomli_w` serializes `True`/`False` as TOML `true`/`false`; `_load_config_once`'s guard (`isinstance(v, bool) and expected is not bool` → False for a `bool` field) passes the value correctly.
- **`decorate()` reads `_state["schedules"]` and `_runs`** (`resume.py:232`): auto-scheduled sessions surface in the UI as `resume.state == "scheduled"` at the next poll (~2 s) with no additional UI code.
- **`dismiss()` clears `_runs` and `_state["schedules"]`** (`resume.py:221`): dismiss still works correctly when auto-schedule is on — the skip record is separate and not cleared by dismiss.
- **`AGENTS.md`**: no PA restart required for a new `Config` bool field (it is not in `_RESTART_TO_APPLY`). The setting takes effect at the next scheduler tick.

### 5. Risks & mitigations

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Auto-schedule overwrites a manual schedule | High | `_if_unscheduled=True` guard in `schedule()` — aborts if session already scheduled or in `_runs` (any state) |
| Auto-schedule clears a watching run | High | Same `_if_unscheduled=True` guard checks `_runs` |
| Cancel-then-re-arm loop | High | Per-stop `skipped` set in `quota-state.json`; cancel adds to it; auto-schedule skips entries in it |
| Dismiss race with auto-schedule (auto-schedules after dismiss check) | Low | `dismiss()` removes the schedule — net result is dismissed, no schedule (correct) |
| `_SETTING_TYPES` boolean exclusion | Mitigated | Own route pair, not `/api/save-setting` |
| `skipped` backward compatibility | None | `_load()` uses `raw.get("skipped", [])` — existing files load empty skipped list |

### 6. Resolved decisions

- Q1: Toggle location — A: Gear menu — Decision: `index.html` gear menu (same surface as "Notify me"), single toggle row.
- Q2: Cancel behavior when auto-schedule on — A: Option C (per-stop skip flag) — Decision: cancel endpoint adds `(session_id, key_at)` to `skipped` set when `auto_schedule` is on; auto-schedule skips this entry; manual Schedule clears it.
- Q3: Retroactive scheduling — A: Immediate — Decision: `_auto_schedule_new()` runs each tick unconditionally; sessions already in "Stopped by a quota limit" when the setting is turned on are scheduled on the next tick.

### 7. Open items

- None. All decisions are resolved.

**Assumptions (unconfirmed)** — resolved at minimal depth:
- Auto-scheduled prompt: default `"resume"` (not separately configurable).
- `skipped` records pruned after `DISMISS_KEEP_SECONDS = 14 * 86400`.
- Manually clicking "Schedule" on a skipped session clears the skip record (call `_clear_skip(session_id, key_at)` before or inside `schedule()`).
- Config field name: `auto_schedule`.
- Route: `GET/POST /api/quota/auto-schedule`.

### 8. Recommended approach

**Phase 1 — Backend (config, resume.py, web.py):**

1. `config.py`: Add `auto_schedule: bool = False` to the `Config` dataclass.
2. `resume.py`:
   - Add `skipped` list to `_state` dict, `_load()`, `_save()`, following the `dismissed` pattern.
   - Add `_key_at`-keyed helper `_is_skipped(session_id, key_at)` and `_add_skip(session_id, key_at)` / `_clear_skip(session_id, key_at)`.
   - Add `_if_unscheduled=False` parameter to `schedule()`: when `True`, check inside the lock whether the session is already scheduled or in `_runs`; if so, return `{"ok": True, ..., "_skipped": True}` without modifying state.
   - Add `_auto_schedule_new(now)`: reads `load_config().auto_schedule`, calls `quota.interrupted(now)`, skips kiro rows (`resets_at is None`), skips "ready" rows, skips already-scheduled/running/skipped/dismissed sessions, calls `schedule(sid, _if_unscheduled=True)` for each.
   - Call `_auto_schedule_new(now)` at the top of `tick()`.
3. `web.py`:
   - Add `GET /api/quota/auto-schedule` → `{"enabled": bool(load_config().auto_schedule)}`.
   - Add `POST /api/quota/auto-schedule` → toggle, save, return `{"enabled": new_value}`.
   - Modify `api_quota_cancel`: after calling `resume.cancel()`, if `auto_schedule` is on, call `resume.add_skip(session_id, key_at_from_row)`. Needs `quota.find_hit()` to get `key_at`. (Alternative: move skip logic into `cancel()` itself with an `auto_schedule` flag param — cleaner.)
   - Pass `"auto_schedule": config.auto_schedule` to `index()` template context.

**Phase 2 — UI (index.html):**

1. Gear menu: add `Auto-schedule resumes` toggle row (same markup as "Notify me" row; fetches `GET /api/quota/auto-schedule` on page load; posts to `POST /api/quota/auto-schedule` on click).
2. No changes needed to the "Stopped by a quota limit" section — auto-scheduled sessions surface as `resume.state == "scheduled"` via existing `decorate()` / existing UI rendering.

**Phase 3 — Tests (test_web.py):**

New `TestResume` tests:
- `test_auto_schedule_arms_unattended_rows_on_tick`: turn on, call `tick()`, assert both sessions scheduled.
- `test_auto_schedule_does_not_overwrite_manual_schedule`: manual schedule at custom time, turn on, tick, assert fire_at unchanged.
- `test_auto_schedule_does_not_re_arm_watching_run`: session in "watching" run, tick, assert no second schedule.
- `test_cancel_when_auto_schedule_on_adds_skip_and_prevents_re_arm`: cancel, tick, assert not rescheduled; then manual schedule, tick, assert rescheduled.
- `test_skip_clears_on_manual_schedule`: cancel (skip added), manual schedule, assert skip removed.
- `test_kiro_row_not_auto_scheduled`: kiro row, tick, assert no schedule.
- `test_dismissed_row_not_auto_scheduled`: dismissed row, tick, assert no schedule.
- `test_skip_record_pruned_with_dismissed_age`: skip older than 14 days pruned on next save.
- `test_auto_schedule_off_leaves_existing_schedules`: toggle off after auto-scheduling, schedules remain.

### 9. QA environment

- PowerAtlas running locally: `.venv-PowerAtlas\Scripts\power-atlas`
- Dashboard at `http://127.0.0.1:<port>/` (requires `pa_local` cookie via tray)
- To trigger a "stopped" session row: use a synthetic quota hit fixture or the existing `TestQuota` transcript fixtures
- Verify: toggle gear menu → "Auto-schedule resumes"; stop a session; within 5 s it shows "Resumes in X min" without clicking Schedule
- Verify cancel → row stays in list, "Resumes in X min" does not reappear; manual Schedule works
- Browser: Playwright MCP (start Chrome if needed per AGENTS.md)
