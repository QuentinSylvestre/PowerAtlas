# Auto-Schedule Resume Mode

> **Date**: 2026-10-13
> **Status**: Draft  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Single PA-wide boolean setting that automatically schedules a resume for every session stopped by a quota limit, without requiring the user to click "Schedule" manually. Gear-menu toggle. Integrates with existing "Stopped by a quota limit" section.
> **Memory relied on**: none
> **Estimated effort**: 1-2 days

---

## Intent

### Problem statement & desired outcomes

The "Stopped by a quota limit" section in the Overview's "Needs attention" panel lists sessions whose quota limit stopped them, with a "Schedule" button per row. When several sessions are stopped simultaneously — or the user leaves PA running overnight — every one of them requires a manual schedule click. Auto-schedule mode removes that friction: once enabled, PA schedules every resumable stopped session automatically at `resets_at + 60 s` (the existing default delay), so the user wakes up to resumed sessions without any extra interaction.

The setting is global (applies to all providers, all workspaces) and toggled from the gear menu alongside "Notify me" and "Autostart on login."

### Success criteria

- SC-1: A toggle "Auto-schedule resumes" appears in the gear menu (same surface as "Notify me").
- SC-2: When on: within one scheduler tick (~5 s), every session currently in "Stopped by a quota limit" that has a known `resets_at`, is not already scheduled/watching, is not dismissed, and is not skipped, is automatically scheduled at `resets_at + FIRE_DELAY_SECONDS`.
- SC-3: When on: a manually-set schedule (custom time or prompt) is never overwritten by auto-schedule.
- SC-4: When on: clicking "Cancel schedule" on an auto-scheduled row removes the schedule AND records a per-stop skip entry, so the session stays in the list but is not re-armed by auto-schedule for that stop.
- SC-5: When the user manually clicks "Schedule" on a skipped session, the skip record is cleared and a new schedule is created; future auto-schedule ticks treat the session normally.
- SC-6: kiro-cli-v3 sessions (no `resets_at`) and "ready" sessions (quota already reset) are never auto-scheduled.
- SC-7: Toggling auto-schedule off leaves all existing schedules in place; they fire as scheduled.
- SC-8: The setting persists across PA restarts.
- SC-9: All existing resume paths (manual Resume now, Schedule, Cancel, Dismiss) continue to work identically when auto-schedule is off.

### Scope boundaries & non-goals

**In scope:**
- `auto_schedule: bool = False` top-level `Config` field + `GET/POST /api/quota/auto-schedule` route pair
- `_auto_schedule_new(now)` helper called from `resume.tick()`
- `skipped` list added to `quota-state.json` (keyed by `session_id + key_at`, same shape as `dismissed`)
- `_if_unscheduled=True` guard parameter on `schedule()` (prevents clobbering manual schedules and watching runs)
- Modified `cancel` endpoint: calls `resume.add_skip()` when auto-schedule is on
- Gear-menu toggle in `index.html` (existing quick-menu section, same row pattern as "Notify me")
- `skipped` records pruned after `DISMISS_KEEP_SECONDS` (14 days)

**Not in scope:**
- Per-session auto-schedule override preferences
- Configurable prompt for auto-scheduled resumes (always uses the default "resume")
- Configurable delay (always uses `FIRE_DELAY_SECONDS = 60 s`)
- `/acp` page changes (quota section is Overview-only)
- kiro-cli-v3 sessions (excluded by existing `resets_at is None` guard in `schedule()`)

---

## 1) Current State

**`resume.py` scheduler** (`resume.py:295–332`): `tick(now)` is called every `TICK_SECONDS = 5` s on a daemon thread. It fires due schedules (one at a time, taken off the list before firing) then watches running resumes. The `_state` dict (loaded lazily from `quota-state.json`) currently holds `schedules` and `dismissed` lists. `decorate()` (`resume.py:232`) reads both to produce per-row `resume` and `can_resume` fields consumed by the UI.

**`schedule(session_id, ...)` function** (`resume.py:165`): validates session, builds a record, acquires `_lock`, replaces any existing schedule for the same session, clears `_runs` for that session, saves. All callers (web routes + `tick()` firing) go through this function and serialize on `_lock`.

**`cancel(session_id)` function** (`resume.py:236`): acquires `_lock`, removes schedule if present, saves. Called by `POST /api/quota/cancel` (`web.py:4628`).

**Config pattern** (`config.py:99`): `Config` is a `@dataclass`. `notifications: dict = {"enabled": False}` is the precedent for a behavioural on/off setting, but has its own route pair (`web.py:5634`) rather than going through `_SETTING_TYPES` (`web.py:6429`), which deliberately excludes booleans. `tomli_w` serializes Python `bool` as TOML `true`/`false` (verified). `_load_config_once`'s type-check guard passes `bool` fields correctly.

**Gear menu toggle pattern** (`index.html`: "Notify me" row): GET `/api/notifications` on page load; POST `/api/notifications` on click; toggles `config.notifications["enabled"]` via `load_config()` + `save_config()`.

**`quota-state.json`** (`config.CONFIG_DIR / "quota-state.json"`): current format `{"version": 1, "schedules": [...], "dismissed": [...]}`. `_load()` (`resume.py:68`) reads with `raw.get("schedules")` / `raw.get("dismissed")` — unknown keys ignored.

## 2) Goal

Add a single `auto_schedule: bool = False` setting to `config.toml` and a `skipped` list to `quota-state.json`. When on, `resume.tick()` calls a new `_auto_schedule_new(now)` helper that schedules every unattended resumable stopped session; `cancel` adds to `skipped` to prevent re-arm; `schedule()` clears skip on manual invocation and has an `_if_unscheduled` guard to prevent auto-schedule from clobbering manual schedules or watching runs.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Config field type | Top-level `Config` field `auto_schedule: bool = False` | Nest under `notifications` dict | Notifications dict requires `.get()` guards everywhere; a top-level bool field has clean load/save semantics and mirrors the `acp_max_sessions` pattern |
| Toggle route | Own `GET/POST /api/quota/auto-schedule` pair | Add to `_SETTING_TYPES` | `_SETTING_TYPES` explicitly rejects booleans (`isinstance(True, int)` guard at `web.py:5614`); own route pair follows the `notifications` pattern |
| Integration point | `resume.tick()` via `_auto_schedule_new(now)` | `decorate()` (read-only, wrong owner); `_overview_summary()` (web thread, wrong context) | `tick()` is sequential, owns `_state`/`_runs`, calls `schedule()` — natural fit with no threading changes |
| Skip-on-cancel | `_state["skipped"]` in `quota-state.json`; `add_skip()` called by cancel route | Replace "Cancel" with "Dismiss"; add cooldown delay | User wants to cancel without losing row visibility or re-arming; per-stop keyed set (same shape as `dismissed`) is the minimal addition |
| `_if_unscheduled` guard | New optional param on `schedule()`; checks `_state["schedules"]` + `_runs` inside `_lock` | Pre-check outside lock | Lock-inside check prevents race between auto-schedule and concurrent manual schedule calls |
| Skip clear on manual schedule | Inside `schedule()` lock body when `_if_unscheduled=False` | Separate `clear_skip()` call in web route | Single atomic operation; web route has no knowledge of key_at; avoids a race between clear and new-schedule write |
| Race #3 (dismiss-after-snapshot) mitigation | Accept one phantom schedule per race (tick's existing dismissed check drops it silently; `_auto_schedule_new` snapshot sees dismissed on next call) | Check dismissed inside `schedule()` lock | `quota.find_hit()` is a file-read unsuitable inside `_lock`; the phantom resolves in ≤10 s and causes no user-visible effect (tick's dismissed guard at `resume.py:~306` already handles it) |

## 4) External Dependencies & Costs

### Required external changes

None. Code-only change. No infrastructure, CI/CD, IAM, migrations, or third-party services involved.

### Cost impact

None. One extra `load_config()` TOML parse per 5 s scheduler tick and one extra `quota.interrupted()` call (O(1) from memo cache). Negligible.

## 5) Implementation Phases

### Phase 1: Config field + `resume.py` foundation [QA]

**Goal**: Add `auto_schedule: bool = False` to Config; add `skipped` state to `resume.py` with `_load()`/`_save()`/`reset()` support; add `add_skip()` public function; add `_if_unscheduled` guard to `schedule()` with skip-clear on manual invocation.

**Covers**: SC-3, SC-4 (foundation), SC-5, SC-8, SC-9

**File scope**: `src/power_atlas/config.py`, `src/power_atlas/resume.py`

**`config.py` change** — add after `acp_permission_base_agent: str = "kiro_default"` line:

```python
# Automatically schedule a resume for every session stopped by a quota limit.
# Toggled via GET/POST /api/quota/auto-schedule. Takes effect at the next
# scheduler tick (~5 s). 261013-1914_AUTO_SCHEDULE_RESUME_MODE
auto_schedule: bool = False
```

**`resume.py` changes:**

*1. Extend `_state` dict to include `skipped`:*

```python
_state: dict = {"loaded": False, "schedules": [], "dismissed": [], "skipped": []}
```

*2. In `_load()` — add skipped parsing after the dismissed loop (caller holds `_lock`):*

```python
skipped = []
for s in raw.get("skipped") if isinstance(raw.get("skipped"), list) else []:
    if (isinstance(s, dict) and isinstance(s.get("session_id"), str)
            and _UUID_RE.fullmatch(s["session_id"])
            and _num(s.get("key_at")) is not None and _num(s.get("at")) is not None):
        skipped.append({"session_id": s["session_id"], "key_at": float(s["key_at"]),
                        "at": float(s["at"])})
_state["schedules"], _state["dismissed"], _state["skipped"] = schedules, dismissed, skipped
```

*3. In `_save()` — include `skipped` in the JSON body:*

```python
body = json.dumps({"version": 1, "schedules": _state["schedules"],
                   "dismissed": _state["dismissed"], "skipped": _state["skipped"]},
                  indent=1, allow_nan=False)
```

*4. In `reset()` — add `skipped=[]`:*

```python
def reset() -> None:
    with _lock:
        _state.update(loaded=False, schedules=[], dismissed=[], skipped=[])
        _runs.clear()
```

*5. Add `add_skip()` public function (place near `cancel()` / `dismiss()`):*

```python
def add_skip(session_id: str, now: float | None = None) -> None:
    """Record a per-stop skip: _auto_schedule_new() will not re-arm this session for its
    current stop. The skip persists until the session is stopped again with a different
    resets_at, or the record expires (DISMISS_KEEP_SECONDS).

    Called by api_quota_cancel when auto_schedule is on.
    """
    now = time.time() if now is None else now
    row = quota.find_hit(session_id, now)
    if row is None:
        return
    key_at = _key_at(row)
    with _lock:
        _load()
        cutoff = now - DISMISS_KEEP_SECONDS
        _state["skipped"] = [
            s for s in _state["skipped"]
            if s.get("at", 0) > cutoff
            and not (s["session_id"] == session_id and abs(s.get("key_at", 0) - key_at) < 1.0)
        ] + [{"session_id": session_id, "key_at": key_at, "at": now}]
        _save()
```

*6. Modify `schedule()` — add `_if_unscheduled` parameter and skip-clear:*

The current signature at `resume.py:165` is `def schedule(session_id, prompt=None, fire_at=None, now: float | None = None) -> dict`. Change to:

```python
def schedule(session_id, prompt=None, fire_at=None, now: float | None = None,
             _if_unscheduled: bool = False) -> dict:
```

Inside the `with _lock:` block (`resume.py:186`), before the existing `_state["schedules"] = ...` line, add:

```python
        if _if_unscheduled and (
            any(s["session_id"] == session_id for s in _state["schedules"])
            or any(r["session_id"] == session_id for r in _runs)
        ):
            # Session already scheduled or being watched — auto-schedule must not clobber it.
            # Return a non-error response so the caller does not log a spurious warning.
            return {"ok": True, "fire_at": None, "persisted": True, "open_terminal": False}
        if not _if_unscheduled:
            # Manual schedule: clear any skip record so auto-schedule can re-arm on the next stop.
            _state["skipped"] = [s for s in _state["skipped"] if s["session_id"] != session_id]
```

Note: the skip-clear only removes entries by `session_id` (not by `key_at`), so all skips for this session are cleared on a manual schedule — the user explicitly chose to schedule, so auto-schedule should resume full management.

**Exit criteria**:
- [ ] `Config.auto_schedule` field exists with default `False`; `load_config()` returns it as a Python `bool`.
- [ ] `config.toml` round-trip: setting `auto_schedule = true` in the file loads as `True`, setting to `false` loads as `False`.
- [ ] `quota-state.json` includes `"skipped": [...]` on next save; existing files without `"skipped"` load cleanly (empty list).
- [ ] `add_skip(session_id)` for a valid stopped session creates a `skipped` entry in `_state["skipped"]` and persists it to `quota-state.json`.
- [ ] `add_skip(session_id)` for a session not in `quota.find_hit()` is a no-op (no write, no error).
- [ ] `schedule(session_id, _if_unscheduled=True)` when that session already has a schedule: returns `{"ok": True, "fire_at": None, ...}` without modifying `_state["schedules"]`.
- [ ] `schedule(session_id, _if_unscheduled=True)` when that session is in `_runs`: returns `{"ok": True, "fire_at": None, ...}` without clearing `_runs`.
- [ ] `schedule(session_id)` (manual, no `_if_unscheduled`) when that session has a skip record: clears the skip record AND creates the new schedule atomically.
- [ ] `reset()` clears `_state["skipped"]`.

---

### Phase 2: `_auto_schedule_new()` + `tick()` integration [QA] [P:3]

**Goal**: Implement `_auto_schedule_new(now)` and call it from `tick()` before the watch loop.

**Covers**: SC-2, SC-6, SC-7, SC-9

**File scope**: `src/power_atlas/resume.py`

**`resume.py` changes:**

*1. Add `_auto_schedule_new(now)` (place before `tick()`):*

```python
def _auto_schedule_new(now: float) -> None:
    """Schedule resumes for unattended interrupted sessions when auto_schedule is on.

    Called from tick(). Uses _if_unscheduled=True so it never clobbers a manual
    schedule or clears a watching run. Snapshot is taken under _lock once; individual
    schedule() calls re-acquire _lock independently (serialized with manual calls).

    Race note: a dismiss() between the snapshot and a schedule() call can produce one
    phantom schedule that tick() discards (via its own dismissed check). The session
    then appears dismissed in the snapshot of the next tick and is not rescheduled.
    """
    try:
        from .config import load_config
        if not load_config().auto_schedule:
            return
    except Exception:
        return
    rows = quota.interrupted(now)       # O(1) from memo cache; no lock needed
    with _lock:
        _load()
        already_scheduled = {s["session_id"] for s in _state["schedules"]}
        dismissed_keys = {(d["session_id"], d["key_at"]) for d in _state["dismissed"]}
        in_runs = {r["session_id"] for r in _runs}
        skipped_keys = {(s["session_id"], s["key_at"]) for s in _state["skipped"]}
    for row in rows:
        if row.get("resets_at") is None:        # kiro-cli: never schedulable
            continue
        if row.get("state") == "ready":         # quota already reset; Resume now is the action
            continue
        sid = row["id"]
        key_at = _key_at(row)
        if sid in already_scheduled:
            continue
        if sid in in_runs:                      # any state: watching, error, gave_up, no_activity
            continue
        if (sid, key_at) in dismissed_keys:
            continue
        if (sid, key_at) in skipped_keys:
            continue
        try:
            schedule(sid, now=now, _if_unscheduled=True)
        except Exception:
            log.exception("auto-schedule failed for %s", sid)
```

*2. In `tick()` (`resume.py:295`) — add call at the top before the `while True` loop:*

```python
def tick(now: float | None = None) -> None:
    now = time.time() if now is None else now
    _auto_schedule_new(now)    # auto-schedule before firing due schedules
    while True:
        ...
```

**Exit criteria**:
- [ ] `tick()` called with `auto_schedule=True` and one unattended stopped session: session appears in `_state["schedules"]` within one tick.
- [ ] `tick()` called with `auto_schedule=True` and a session with `resets_at=None` (kiro-cli): session NOT in `_state["schedules"]`.
- [ ] `tick()` called with `auto_schedule=True` and a session with `state="ready"`: session NOT in `_state["schedules"]`.
- [ ] `tick()` called with `auto_schedule=True` and a session already in `_state["schedules"]`: existing schedule NOT replaced (fire_at unchanged).
- [ ] `tick()` called with `auto_schedule=True` and a session in `_runs` (any state including `"gave_up"`): session NOT rescheduled.
- [ ] `tick()` called with `auto_schedule=True` and a dismissed session: NOT rescheduled.
- [ ] `tick()` called with `auto_schedule=True` and a skipped session: NOT rescheduled.
- [ ] `tick()` called with `auto_schedule=False`: no sessions scheduled by `_auto_schedule_new`.

---

### Phase 3: Web routes + cancel modification + template context [QA] [P:2]

**Goal**: Add `GET/POST /api/quota/auto-schedule` routes; modify `api_quota_cancel` to call `resume.add_skip()` when auto-schedule is on; pass `auto_schedule` to the `index()` template context.

**Covers**: SC-1 (partial — template context), SC-4, SC-8 (route persists setting)

**File scope**: `src/power_atlas/web.py`

**`web.py` changes:**

*1. Add two routes near the other quota routes (`web.py:4598`):*

```python
@app.get("/api/quota/auto-schedule")
async def get_quota_auto_schedule():
    """Whether auto-schedule mode is on: `{"enabled": bool}`."""
    return {"enabled": bool(load_config().auto_schedule)}


@app.post("/api/quota/auto-schedule")
async def toggle_quota_auto_schedule():
    """Toggle auto-schedule mode. Returns `{"enabled": bool}` (new value)."""
    config = load_config()
    enabled = not bool(config.auto_schedule)
    config.auto_schedule = enabled
    save_config(config)
    return {"enabled": enabled}
```

These routes are **loopback-only** (not added to `_REMOTE_ALLOWED_PATHS`), consistent with all other `/api/quota/*` routes.

*2. Modify `api_quota_cancel` (currently at `web.py:4628`):*

```python
@app.post("/api/quota/cancel")
async def api_quota_cancel(request: Request):
    """Cancel a session's scheduled resume: `{"session_id"}`.
    When auto-schedule is on, also records a skip so the session is not re-armed.
    """
    body = await _quota_body(request)
    if body is None:
        return JSONResponse({"ok": False, "error": "bad request"}, 400)
    session_id = body.get("session_id")
    result = await asyncio.to_thread(resume.cancel, session_id)
    if result.get("ok") and load_config().auto_schedule:
        await asyncio.to_thread(resume.add_skip, session_id)
    return result
```

*3. Add `auto_schedule` to `index()` template context (after `"notifications_enabled"` line, ~`web.py:2989`):*

```python
"auto_schedule": bool(config.auto_schedule),
```

**Exit criteria**:
- [ ] `GET /api/quota/auto-schedule` returns `{"enabled": false}` on a fresh config.
- [ ] `POST /api/quota/auto-schedule` toggles and persists: after one POST, `GET` returns `{"enabled": true}`; after a second POST, returns `{"enabled": false}`.
- [ ] Both routes require `pa_local` cookie + same-origin header (403 otherwise).
- [ ] Neither route appears in `_REMOTE_ALLOWED_PATHS`.
- [ ] `POST /api/quota/cancel` with auto-schedule on: calls `resume.add_skip(session_id)` (skip record created in `_state["skipped"]`).
- [ ] `POST /api/quota/cancel` with auto-schedule off: does NOT call `resume.add_skip()`.
- [ ] `index()` template context contains `"auto_schedule": bool`.

---

### Phase 4: Gear-menu toggle [QA]

**Goal**: Add "Auto-schedule resumes" toggle row to the gear menu in `index.html`, following the "Notify me" row pattern.

**Covers**: SC-1

**File scope**: `src/power_atlas/templates/index.html`

**`index.html` change**: In the gear quick-menu, immediately after the "Notify me" toggle row, add:

```html
<div class="topbar-menu-row" id="autoScheduleRow">
  <span class="topbar-menu-label">Auto-schedule resumes</span>
  <button type="button" class="topbar-toggle-btn" id="autoScheduleToggle"
          aria-pressed="{{ 'true' if auto_schedule else 'false' }}"
          title="Automatically schedule a resume for every session stopped by a quota limit">
    <span class="topbar-toggle-track"></span>
  </button>
</div>
```

The JS init block (same section as "Notify me" init):

```javascript
// Auto-schedule toggle — 261013-1914_AUTO_SCHEDULE_RESUME_MODE
(function() {
  const btn = document.getElementById('autoScheduleToggle');
  if (!btn) return;
  function setAutoSchedule(enabled) {
    btn.setAttribute('aria-pressed', enabled ? 'true' : 'false');
  }
  // Initialize from server state (Jinja2 variable already sets aria-pressed on load)
  btn.addEventListener('click', function() {
    fetch('/api/quota/auto-schedule', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({})})
      .then(r => r.json())
      .then(d => { if (typeof d.enabled === 'boolean') setAutoSchedule(d.enabled); })
      .catch(() => {});
  });
})();
```

Note: the exact markup must match the existing "Notify me" row structure for consistent styling. Read the "Notify me" row in `index.html` before writing to confirm the class names and element structure.

**Exit criteria**:
- [ ] "Auto-schedule resumes" toggle appears in the gear quick-menu, after "Notify me".
- [ ] Toggle reflects the server state on page load (pressed when `auto_schedule=True`, unpressed when `False`).
- [ ] Clicking the toggle calls `POST /api/quota/auto-schedule` and updates `aria-pressed` on the button.
- [ ] Hard reload (`Ctrl+Shift+R`) after toggling retains the new state.
- [ ] No new CSS rules inline in `acp.html` (this toggle is dashboard-only, not on `/acp`).

---

### Phase 5: Tests [QA]

**Goal**: Add `TestResume` tests covering auto-schedule behavior (skip state, `_if_unscheduled` guard, `add_skip`, `_auto_schedule_new` via `tick()`) and route-level tests for `GET/POST /api/quota/auto-schedule` and the modified cancel route.

**Covers**: SC-1 through SC-9 (via test assertions)

**File scope**: `tests/test_web.py`

Tests follow the existing `TestResume` fixture pattern (`test_web.py:45823`): `isolated_config` autouse fixture + `_REAL_START_SCHEDULER` pattern for manual tick calls. **Do not use `git add .` — stage only `tests/test_web.py`.**

**Test list and invariants pinned:**

| Test name | Scenario | Invariant pinned |
|---|---|---|
| `test_auto_schedule_arms_unattended_rows_on_tick` | `auto_schedule=True`, two stopped sessions, call `tick()`; assert both scheduled at `resets_at + FIRE_DELAY_SECONDS` | SC-2: tick arms all unattended rows |
| `test_auto_schedule_does_not_overwrite_manual_schedule` | manual schedule at custom `fire_at`, then `auto_schedule=True`, tick; assert `fire_at` unchanged | SC-3: manual schedule never clobbered |
| `test_auto_schedule_does_not_re_arm_watching_run` | session in `_runs["watching"]`, then tick with `auto_schedule=True`; assert not in `_state["schedules"]` | SC-3/concurrency: `_runs` check in guard |
| `test_auto_schedule_does_not_re_arm_gave_up_run` | session in `_runs["gave_up"]`, then tick; assert not rescheduled | SC-3: `in_runs` covers ended states |
| `test_cancel_when_auto_schedule_on_adds_skip` | schedule session, `auto_schedule=True`, cancel; assert skip in `_state["skipped"]` | SC-4: cancel adds skip when on |
| `test_cancel_when_auto_schedule_off_does_not_add_skip` | schedule session, `auto_schedule=False`, cancel; assert `_state["skipped"]` empty | SC-9: off changes nothing |
| `test_auto_schedule_respects_skip_after_cancel` | cancel (skip added), tick with `auto_schedule=True`; assert session NOT rescheduled | SC-4: skip prevents re-arm |
| `test_manual_schedule_clears_skip` | cancel (skip added), then manual `schedule()`; assert skip removed AND schedule created | SC-5: manual schedule clears skip |
| `test_kiro_row_not_auto_scheduled` | kiro-cli row (`resets_at=None`), tick with `auto_schedule=True`; assert not scheduled | SC-6: kiro excluded |
| `test_ready_row_not_auto_scheduled` | session with `state="ready"`, tick; assert not scheduled | SC-6: ready excluded |
| `test_dismissed_row_not_auto_scheduled` | dismissed session, tick with `auto_schedule=True`; assert not scheduled | SC-2 boundary |
| `test_auto_schedule_off_leaves_existing_schedules` | sessions auto-scheduled, toggle off, verify existing schedules intact, tick fires them | SC-7: toggle off doesn't touch existing |
| `test_skip_persisted_to_quota_state_json` | `add_skip()` then reload from disk; assert skip record present with correct `key_at` | SC-4/SC-8: persistence |
| `test_skip_pruned_after_14_days` | skip record with `at = now - DISMISS_KEEP_SECONDS - 1`, call `add_skip()` for another session; assert old record pruned | Pruning parity with dismissed |
| `test_get_auto_schedule_route` | `GET /api/quota/auto-schedule` with cookie; assert `{"enabled": false}` default | SC-1/SC-8: route |
| `test_toggle_auto_schedule_route` | `POST` twice; assert enabled → disabled | SC-8: toggle persists |
| `test_auto_schedule_route_requires_auth` | no cookie → 403 | Route security |
| `test_cancel_route_adds_skip_when_auto_schedule_on` | HTTP-level: cancel with `auto_schedule=True`; assert skip in `_state["skipped"]` | SC-4: route-level |

**Exit criteria**:
- [ ] All 18 new tests pass.
- [ ] `_check_test_names.py` reports no duplicate test names.
- [ ] Existing `TestResume` tests pass without modification.
- [ ] `TestQuotaRoutes` passes without modification.

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Auto-schedule clobbers manual schedule | High | `_if_unscheduled=True` guard in `schedule()` checks `_state["schedules"]` + `_runs` under `_lock` before writing (Phase 1) |
| Auto-schedule clears a watching run | High | Same `_if_unscheduled=True` guard checks `_runs` (any state) (Phase 1) |
| Cancel-then-re-arm loop | High | Per-stop `skipped` set; `add_skip()` called by cancel route when `auto_schedule=True` (Phases 1 + 3) |
| Dismiss-after-snapshot race (phantom schedule) | Low | Bounded to ≤10 s; `tick()`'s own dismissed check at `resume.py:~306` drops the phantom silently before firing; next `_auto_schedule_new()` snapshot sees dismissed correctly — no resume attempt ever fires (Phase 2 note in comment) |
| `_SETTING_TYPES` boolean exclusion | Mitigated | Own route pair at `/api/quota/auto-schedule`, not `/api/save-setting` (Phase 3) |
| Remote access exposure | Mitigated | New routes NOT added to `_REMOTE_ALLOWED_PATHS`; both quota routes are loopback-only (Phase 3) |
| `quota-state.json` backward compat | None | `_load()` uses `raw.get("skipped", [])` pattern; existing files without `"skipped"` load empty list (Phase 1) |

## 7) Verification

**Automated (run before marking Done)**:
```
cd c:\Users\QSylvestre.POLESTAR\Documents\Perso\PowerAtlas
.venv-PowerAtlas\Scripts\python -m pytest tests/test_web.py -k "auto_schedule or auto_schedule" --timeout=300 -v
.venv-PowerAtlas\Scripts\python _check_test_names.py
```

**Browser QA (requires running PowerAtlas instance + pa_local cookie per AGENTS.md)**:
1. Start PowerAtlas: `.venv-PowerAtlas\Scripts\power-atlas`
2. Open dashboard via tray → hard reload (`Ctrl+Shift+R`)
3. Gear menu → confirm "Auto-schedule resumes" toggle appears after "Notify me"
4. Toggle on → toggle shows pressed state; no page error in console
5. Trigger a quota hit (use synthetic transcript fixture or wait for a real hit)
6. Within 5 s: the stopped session shows "Resumes in X min" without clicking Schedule
7. Click "Cancel schedule" → row stays in list, not rescheduled after 10 s
8. Click "Schedule" manually on the same row → "Resumes in X min" reappears
9. Dismiss the row → row disappears; toggling auto-schedule off/on does not bring it back for this stop
10. Toggle auto-schedule off → existing scheduled rows keep "Resumes in X min"

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Add "Auto-schedule resumes" to the Settings/Features list (gear menu settings section) | 4 |

*Pending doc-impact sub-agent findings — will be updated when that agent returns.*

## Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Config field + resume.py foundation | Not started | |
| 2 | `_auto_schedule_new()` + tick() integration | Not started | [P:3] |
| 3 | Web routes + cancel modification + template context | Not started | [P:2] |
| 4 | Gear-menu toggle | Not started | |
| 5 | Tests | Not started | |

## 9) Implementation Divergences from Plan

*Reserved — filled during implementation.*

## Follow-up Work (Deferred)

1. **Dismiss-after-snapshot phantom schedule.** A dismiss() call between `_auto_schedule_new()`'s snapshot and its `schedule()` call can create a schedule for a dismissed session, which `tick()`'s own dismissed check drops silently. The race window is ≤5 s, no resume fires. A full fix would check dismissed inside `schedule()`'s lock using pre-fetched `key_at` from `quota.find_hit()` — viable but adds complexity for an edge case with no user impact. Source: Design Decisions (Race #3 row), Phase 2 code comment.

## Review Log

*Reserved — filled by review cycles.*

## Harness Improvement Opportunities

*Reserved — filled during exploration and implementation.*
