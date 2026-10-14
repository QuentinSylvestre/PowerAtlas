# Auto-Schedule Resume Mode

> **Date**: 2026-10-13
> **Status**: Complete  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: 2026-10-14T03:45
> **Scope**: Single PA-wide boolean setting that automatically schedules a resume for every session stopped by a quota limit, without requiring the user to click "Schedule" manually. Gear-menu toggle. Integrates with existing "Stopped by a quota limit" section.
> **Memory relied on**: none
> **Estimated effort**: 1-2 days

---

## Completion Summary

All 9 success criteria implemented, tested (3428 tests), and verified end-to-end (Step 9b browser QA 10/10 PASS).

**What shipped**: `auto_schedule: bool = False` in `Config`; `_state["skipped"]` + `cancel_with_skip()` + `add_skip()` in `resume.py`; `_auto_schedule_new()` called from `tick()`; `GET/POST /api/quota/auto-schedule` routes in `web.py`; `cancel` route replaced with atomic `cancel_with_skip()`; "Auto-schedule resumes" toggle in gear menu (`index.html`) with `role=switch`/`aria-pressed`; 22 tests in `TestResume`; section 2.31 in `plans/tests/260701_POWERATLAS.md`; README, ROADMAP, AGENTS.md docs updated.

### Acknowledged at archival

- **Skipped (harness opportunity)**: `/qexplore` Step 3 filename note in SKILL.md could specify `Get-Date -Format "yyMMdd"` to prevent time-suffix qvalidate failures on Windows — low-signal enough to skip promotion.
- **Implementation Divergences section left as "Reserved"** — all divergences were addressed in per-phase implementation notes rather than the formal section (accept; same informational content, different location).
- **Follow-up Work (deferred)**: Dismiss-after-snapshot phantom schedule race — a `dismiss()` call between `_auto_schedule_new()`'s snapshot and its `schedule()` call can create a phantom schedule for a dismissed session; `tick()`'s own dismissed-check drops it silently before any resume fires. ≤5 s window, no user impact. See Phase 2 code comment and Design Decisions (Race #3 row).

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

**Goal**: Add `auto_schedule: bool = False` to Config; add `skipped` state to `resume.py` with `_load()`/`_save()`/`reset()` support; add `cancel_with_skip()` (atomic cancel+skip, used by cancel route) and `add_skip()` (test-level use); add `_if_unscheduled` guard to `schedule()` with skip-clear on manual invocation.

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

*4a. In `restore()` — add prune for expired skip entries (called at startup):*

Inside `restore()` after `_load()` is called, add:

```python
        cutoff = now - DISMISS_KEEP_SECONDS
        stale_skips = [s for s in _state.get("skipped", []) if s.get("at", 0) <= cutoff]
        if stale_skips:
            _state["skipped"] = [s for s in _state["skipped"] if s.get("at", 0) > cutoff]
            _save()
```

*4b. In `reset()` — add `skipped=[]`:*

```python
def reset() -> None:
    with _lock:
        _state.update(loaded=False, schedules=[], dismissed=[], skipped=[])
        _runs.clear()
```

*5. Add `cancel_with_skip()` — the atomic cancel+skip used by the cancel route:*

```python
def add_skip(session_id: str, now: float | None = None) -> None:
    """Cancel a scheduled resume and atomically record a per-stop skip.
    The earlier two-step cancel()+add_skip() left a race window where the scheduler
    could re-arm the session between calls. This function holds _lock throughout.
    """
    now = time.time() if now is None else now
    row = quota.find_hit(session_id, now)   # outside lock
    key_at = _key_at(row) if row is not None else None
    with _lock:
        _load()
        before = len(_state["schedules"])
        _state["schedules"] = [s for s in _state["schedules"] if s["session_id"] != session_id]
        if len(_state["schedules"]) == before:
            return {"ok": False, "error": "Nothing was scheduled for that session"}
        if key_at is not None:
            cutoff = now - DISMISS_KEEP_SECONDS
            _state["skipped"] = [
                s for s in _state["skipped"]
                if s.get("at", 0) > cutoff
                and not (s["session_id"] == session_id and abs(s.get("key_at", 0) - key_at) < 1.0)
            ] + [{"session_id": session_id, "key_at": key_at, "at": now}]
        _save()
    return {"ok": True}
```

*6. Keep `add_skip()` for test-level use (place after `cancel_with_skip()`):*

```python
def add_skip(session_id: str, now: float | None = None) -> None:
    """Record a per-stop skip outside a cancel. Kept for test-level use only.
    Production cancel calls cancel_with_skip() to close the race window.
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
            return {"ok": True, "fire_at": None, "persisted": False, "open_terminal": False}
        if not _if_unscheduled:
            # Manual schedule: clear any skip record so auto-schedule can re-arm on the next stop.
            _state["skipped"] = [s for s in _state["skipped"] if s["session_id"] != session_id]
```

Note: the skip-clear only removes entries by `session_id` (not by `key_at`), so all skips for this session are cleared on a manual schedule — the user explicitly chose to schedule, so auto-schedule should resume full management.

**Exit criteria**:
- [x] `Config.auto_schedule` field exists with default `False`; `load_config()` returns it as a Python `bool`.
- [x] `config.toml` round-trip: setting `auto_schedule = true` in the file loads as `True`, setting to `false` loads as `False`.
- [x] `quota-state.json` includes `"skipped": [...]` on next save; existing files without `"skipped"` load cleanly (empty list).
- [x] `add_skip(session_id)` for a valid stopped session creates a `skipped` entry in `_state["skipped"]` and persists it to `quota-state.json`.
- [x] `add_skip(session_id)` for a session not in `quota.find_hit()` is a no-op (no write, no error).
- [x] `schedule(session_id, _if_unscheduled=True)` when that session already has a schedule: returns `{"ok": True, "fire_at": None, ...}` without modifying `_state["schedules"]`.
- [x] `schedule(session_id, _if_unscheduled=True)` when that session is in `_runs`: returns `{"ok": True, "fire_at": None, ...}` without clearing `_runs`.
- [x] `schedule(session_id)` (manual, no `_if_unscheduled`) when that session has a skip record: clears the skip record AND creates the new schedule atomically.
- [x] `reset()` clears `_state["skipped"]`.
- [x] `cancel_with_skip(session_id)` when session has a schedule: removes schedule, records skip, saves atomically — single `_lock` acquisition.
- [x] `cancel_with_skip(session_id)` when session has NO schedule: returns `{"ok": False, ...}` without touching `_state["skipped"]`.

Implementation (2026-10-13, code: 23b5d56, fix: 6755534)
`config.py`: Added `auto_schedule: bool = False` field to the `Config` dataclass after `acp_permission_base_agent`, with a comment referencing the plan slug. `resume.py`: Extended `_state` with `skipped: []`; updated `_load()` to parse skipped entries (with field validation matching the dismissed pattern); updated `_save()` to serialize skipped; added prune pass in `restore()` for stale skip records; updated `reset()` to clear skipped; added `cancel_with_skip()` — atomic cancel + skip under a single `_lock` acquisition, with `_valid_session_id()` upfront guard; added `add_skip()` test helper; extended `schedule()` signature with `_if_unscheduled: bool = False` — early return when session is already scheduled or in `_runs`, skip-clear on manual invocation. Review fixes (6755534): moved skipped parsing inside `isinstance(raw, dict)` guard; fixed PEP 8 spacing; rephrased `cancel_with_skip()` docstring; merged double-save in `restore()` into single call; changed `_state.get("skipped", [])` to direct key access.

QA (2026-10-13): PASS — library exports only; no HTTP/UI surface in Phase 1. Verified via full pytest (3397 passed, 1 skipped).

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
    rows = quota.interrupted(now)       # file-stat-memoized; re-reads only changed files; O(N stat calls) not O(1)
    with _lock:
        _load()
        already_scheduled = {s["session_id"] for s in _state["schedules"]}
        dismissed_keys = {(d["session_id"], d["key_at"]) for d in _state["dismissed"]}
        in_runs = {r["session_id"] for r in _runs}
        # Age-filter skip entries; comparison uses tolerance (1 s) to match add_skip()
        _cutoff_s = now - DISMISS_KEEP_SECONDS
        _skipped_live = [s for s in _state["skipped"] if s.get("at", 0) > _cutoff_s]
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
        if any(s["session_id"] == sid and abs(s["key_at"] - key_at) < 1.0 for s in _skipped_live):
            continue
        try:
            _result = schedule(sid, now=now, _if_unscheduled=True)
            if not _result.get("ok"):
                log.debug("auto-schedule skipped %s: %s", sid, _result.get("error"))
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
- [x] `tick()` called with `auto_schedule=True` and one unattended stopped session: session appears in `_state["schedules"]` within one tick.
- [x] `tick()` called with `auto_schedule=True` and a session with `resets_at=None` (kiro-cli): session NOT in `_state["schedules"]`.
- [x] `tick()` called with `auto_schedule=True` and a session with `state="ready"`: session NOT in `_state["schedules"]`.
- [x] `tick()` called with `auto_schedule=True` and a session already in `_state["schedules"]`: existing schedule NOT replaced (fire_at unchanged).
- [x] `tick()` called with `auto_schedule=True` and a session in `_runs` (any state including `"gave_up"`): session NOT rescheduled.
- [x] `tick()` called with `auto_schedule=True` and a dismissed session: NOT rescheduled.
- [x] `tick()` called with `auto_schedule=True` and a skipped session: NOT rescheduled.
- [x] `tick()` called with `auto_schedule=False`: no sessions scheduled by `_auto_schedule_new`.

Implementation (2026-10-13, code: a543925, fix: 44da1ea, 6e96684)
`resume.py`: Added `_auto_schedule_new(now)` (50 lines) immediately before `tick()`. Reads `auto_schedule` config; calls `quota.interrupted(now)` (file-stat-memoized); snapshots `already_scheduled`, `dismissed_keys`, `in_runs`, `_skipped_live` under `_lock`; iterates rows filtering by `resets_at is None` (kiro-cli), `state == "ready"`, already-scheduled, in-runs (all states), dismissed (exact float), skipped (1 s tolerance); calls `schedule(sid, _if_unscheduled=True)` per candidate; logs debug on non-ok. Added `_auto_schedule_new(now)` call at top of `tick()` before the `while True:` loop. Review fixes (44da1ea): corrected `_if_unscheduled` guard comment to list all `_runs` states (watching, gave_up, error, no_activity); added explanation comment for `dismissed_keys` exact float equality. Lock fix (6e96684): `cancel_with_skip()` now reads `load_config().auto_schedule` before acquiring `_lock`, matching the pattern in `_auto_schedule_new()`.

Divergence: sub-agent returned `exit_criteria_ticked: 8` and claimed "9 new tests" — those tests are Phase 5 scope; Phase 2 behavioral exit criteria are verified by code inspection + full suite pass (3406); formal test verification deferred to Phase 5.

QA (2026-10-13): PASS — library-only surface; full pytest 3406 passed.

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
    log.info("auto_schedule set to %s", enabled)
    return {"enabled": enabled}
```

These routes are **loopback-only** (not added to `_REMOTE_ALLOWED_PATHS`), consistent with all other `/api/quota/*` routes.

*2. Modify `api_quota_cancel` (currently at `web.py:4628`):*

```python
@app.post("/api/quota/cancel")
async def api_quota_cancel(request: Request):
    """Cancel a session's scheduled resume and (atomically) record a skip: `{"session_id"}`.
    Uses cancel_with_skip() to close the race window that existed when cancel() and add_skip()
    were separate to_thread calls. Note: add_skip() implicit UUID validation via ok=True gate.
    """
    body = await _quota_body(request)
    if body is None:
        return JSONResponse({"ok": False, "error": "bad request"}, 400)
    session_id = body.get("session_id")
    try:
        result = await asyncio.to_thread(resume.cancel_with_skip, session_id)
    except Exception:
        log.exception("cancel_with_skip failed for %s", session_id)
        result = {"ok": False, "error": "Internal error"}
    return result
```

*3. Add `auto_schedule` to `index()` template context (after `"notifications_enabled"` line, ~`web.py:2989`):*

```python
"auto_schedule": bool(config.auto_schedule),
```

**Exit criteria**:
- [x] `GET /api/quota/auto-schedule` returns `{"enabled": false}` on a fresh config.
- [x] `POST /api/quota/auto-schedule` toggles and persists: after one POST, `GET` returns `{"enabled": true}`; after a second POST, returns `{"enabled": false}`.
- [x] Both routes require `pa_local` cookie + same-origin header (403 otherwise).
- [x] Neither route appears in `_REMOTE_ALLOWED_PATHS`.
- [x] `POST /api/quota/cancel` with auto-schedule on or off: calls `resume.cancel_with_skip(session_id)` atomically (skip record in `_state["skipped"]` only when auto_schedule=True and session found).
- [x] `POST /api/quota/cancel` with auto-schedule off: `cancel_with_skip()` still runs (single function always), but skip record is only added when `auto_schedule=True`.
- [x] `index()` template context contains `"auto_schedule": bool`.

Implementation (2026-10-13, code: 655382d, fix: 44da1ea, 6e96684)
`web.py`: Added `GET /api/quota/auto-schedule` → `{"enabled": bool(load_config().auto_schedule)}`; added `POST /api/quota/auto-schedule` → toggle, save, `log.info(...)`, return `{"enabled": new}`; both loopback-only (not in `_REMOTE_ALLOWED_PATHS`). Modified `api_quota_cancel` to call `asyncio.to_thread(resume.cancel_with_skip, session_id)` wrapped in try/except; bad-body returns `JSONResponse({"ok": False, "error": "bad request"}, status_code=400)`. Added `"auto_schedule": bool(config.auto_schedule)` to `index()` template context. Review fixes (44da1ea): docstring corrected to name `cancel_with_skip()` and its upfront `_valid_session_id()` guard; `cancel_with_skip()` now gates skip-record on `load_config().auto_schedule` (H1 fix); `load_config()` moved outside `_lock` (6e96684); `test_a_body_that_is_not_a_json_object_is_refused_without_calling_anything` updated for 400 status.

QA (2026-10-13): PASS — HTTP endpoints verified via TestClient (127 targeted tests pass); Phase 5 adds formal behavioral coverage.

---

### Phase 4: Gear-menu toggle [QA]

**Goal**: Add "Auto-schedule resumes" toggle row to the gear menu in `index.html`, following the "Notify me" row pattern.

**Covers**: SC-1

**File scope**: `src/power_atlas/templates/index.html`, `README.md`, `plans/ROADMAP.md`, `AGENTS.md`

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
- [x] "Auto-schedule resumes" toggle appears in the gear quick-menu, after "Notify me".
- [x] Toggle reflects the server state on page load (pressed when `auto_schedule=True`, unpressed when `False`).
- [x] Clicking the toggle calls `POST /api/quota/auto-schedule` and updates `aria-pressed` on the button.
- [x] Hard reload (`Ctrl+Shift+R`) after toggling retains the new state.
- [x] No new CSS rules inline in `acp.html` (this toggle is dashboard-only, not on `/acp`).
- [x] `node tests/acp_page.test.mjs` passes (AGENTS.md requires running it when changing a template inline script; run it as regression guard even though it covers `acp.html` not `index.html`).
- [x] `README.md` updated: auto-schedule description in quota-stop section; gear-menu list; new bullet near "Notify me".
- [x] `plans/ROADMAP.md` updated: all four locations (Timed-prompts summary, Edit-scheduled-message summary, quota-limit auto-schedule detail, Edit-scheduled-message sub-bullets).
- [x] `AGENTS.md` "Stopped by a quota limit" Terminology entry updated: Cancel schedule added to controls, gear-menu toggle noted, Settled annotation updated.

Implementation (2026-10-13, code: f45e117, fix: 5c349bc)
`index.html`: Added "Auto-schedule resumes" toggle row to the gear quick-menu immediately after "Notify me", mirroring its exact HTML structure and JS init pattern. The toggle element has `role="switch" aria-pressed="..."` (5c349bc) set from the Jinja2 template context; a click handler POSTs to `/api/quota/auto-schedule` and updates both the CSS active class and `aria-pressed`. `README.md`: Added auto-schedule description in the "Stopped by a quota limit" block, gear-menu list, and Notify-me-adjacent bullet. `plans/ROADMAP.md`: Updated four locations (timed-prompts summary, edit-scheduled-message summary, auto-schedule detail marked shipped, cancel sub-bullet). Note: commit f45e117 also picked up a pre-existing uncommitted "Supervisor / crash restart" ROADMAP item from another session — incidental addition, legitimate roadmap content. `AGENTS.md`: "Stopped by a quota limit" Terminology entry updated with Cancel schedule control and auto-schedule toggle reference.

QA (2026-10-13): BLOCKED — UI toggle requires running PowerAtlas instance and browser verification. Deferred to Step 9b full QA sweep. Template change only (no Python changes), `auto_reload=True` means hard reload suffices when verified.

### 2026-10-13 — /qdev Phase 4 review (full effort, 1 persona: Senior)

2 findings (0 High, 1 Medium, 1 Low). 1 fixed, 1 accepted.

| # | Severity | Finding | Persona | Resolution |
|---|---|---|---|---|
| 1 | Medium | EC-3 not satisfied — `aria-pressed` absent from toggle element; implementation used `classList.toggle('active')` matching existing Notify Me codebase convention but criterion explicitly requires `aria-pressed` | Senior | Fixed — added `role="switch" aria-pressed` to element in template and `setAttribute` in JS handler (5c349bc) |
| 2 | Low | Out-of-scope "Supervisor / crash restart" ROADMAP item committed — pre-existing uncommitted change from another session picked up by Phase 4's `ROADMAP.md` staging | Senior | Accepted — legitimate content; noted as incidental addition in implementation notes |

---

### Phase 5: Tests [QA]

**Goal**: Add `TestResume` tests covering auto-schedule behavior (skip state, `_if_unscheduled` guard, `add_skip`, `_auto_schedule_new` via `tick()`) and route-level tests for `GET/POST /api/quota/auto-schedule` and the modified cancel route.

**Covers**: SC-1 through SC-9 (via test assertions)

**File scope**: `tests/test_web.py`, `plans/tests/260701_POWERATLAS.md`

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
| `test_auto_schedule_does_not_re_arm_error_run` | session in `_runs["error"]`, tick with `auto_schedule=True`; assert not rescheduled | SC-3: error state excluded |
| `test_auto_schedule_does_not_re_arm_no_activity_run` | session in `_runs["no_activity"]`, tick; assert not rescheduled | SC-3: no_activity state excluded |
| `test_add_skip_for_expired_session_is_noop` | `add_skip()` when `quota.find_hit()` returns None; assert `_state["skipped"]` stays empty | Phase 1 exit criterion 5 |
| `test_resume_now_when_auto_schedule_off_unchanged` | `auto_schedule=False`, call `resume_now()`; assert normal return, no skip created | SC-9: resume_now path unaffected |

**Exit criteria**:
- [x] All 22 new tests pass.
- [x] `_check_test_names.py` reports no duplicate test names.
- [x] Existing `TestResume` tests pass without modification.
- [x] `TestQuotaRoutes` passes without modification.
- [x] `plans/tests/260701_POWERATLAS.md` section `2.31 Auto-schedule resume mode` added with route descriptions and TestResume probe list; scope comment widened to include `2.31`.
- [x] (deferred from Phase 3) `/api/quota/auto-schedule` added to `TestQuotaRoutes._PATHS` or an explicit `assert "/api/quota/auto-schedule" not in web_mod._REMOTE_ALLOWED_PATHS` assertion added — ensures `_REMOTE_ALLOWED_PATHS` exclusion is covered by automated test.

Implementation (2026-10-13, code: aba43b7, fix: 1ad61b1)
Added 19 new test methods to `TestResume` (3 from Phase 2 already existed; 22 total plan-specified tests all pass). Tests follow the `isolated_config` autouse pattern; tick tests use the real `quota.interrupted` against the test fixture filesystem (stronger than mocking). `TestQuotaRoutes._PATHS` updated with `/api/quota/auto-schedule` (1ad61b1) providing same-origin enforcement coverage. Section 2.31 added to `plans/tests/260701_POWERATLAS.md`. Review fixes (1ad61b1): added schedule-removal assertions to `test_cancel_when_auto_schedule_on_adds_skip` and `test_cancel_route_adds_skip_when_auto_schedule_on`; added route to `_PATHS`.

QA (2026-10-13): PASS — test suite is the surface; 3425 passed, 1 skipped.

### 2026-10-13 — /qdev Phase 5 review (full effort, 1 persona: Senior)

3 findings (0 High, 2 Medium, 1 Low). All resolved.

| # | Severity | Finding | Persona | Resolution |
|---|---|---|---|---|
| 1 | Medium | `test_cancel_when_auto_schedule_on_adds_skip` only asserted skip present, not that schedule was removed — broken cancel could pass | Senior | Fixed — added schedule-removal assertion (1ad61b1) |
| 2 | Medium | `test_cancel_route_adds_skip_when_auto_schedule_on` same gap at HTTP level | Senior | Fixed — added schedule-removal assertion (1ad61b1) |
| 3 | Low | Same-origin enforcement for POST `/api/quota/auto-schedule` untested — route omitted from `_PATHS` | Senior | Fixed — added to `TestQuotaRoutes._PATHS` (1ad61b1) |

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Auto-schedule clobbers manual schedule | High | `_if_unscheduled=True` guard in `schedule()` checks `_state["schedules"]` + `_runs` under `_lock` before writing (Phase 1) |
| Auto-schedule clears a watching run | High | Same `_if_unscheduled=True` guard checks `_runs` (any state) (Phase 1) |
| Cancel-then-re-arm loop | High | Atomic `cancel_with_skip()` holds `_lock` through both cancel and skip-record steps; no race window (Phases 1 + 3) |
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
| `README.md` | (a) Add auto-schedule description to "Stopped by a quota limit" block (after the Schedule sentence; also mention Cancel schedule keeping the row). (b) Add "**Auto-schedule resumes**" to the gear-menu items list (~line 259). (c) Add "**Auto-schedule resumes** (gear icon, off by default)..." bullet near the "Notify me" bullet (~line 271). | 4 |
| `plans/ROADMAP.md` | (a) Update "Timed prompts" summary (~line 33) to note auto-schedule mode shipped. (b) Update "Edit scheduled message" summary (~line 36): cancel ships, edit remains open. (c) Update "Quota-limit auto-schedule" detail (~line 185): mark shipped, revise "Still open". (d) Update "Edit scheduled message" detail sub-bullets (~lines 187–190): mark cancel shipped, narrow remaining to edit-only. | 4 |
| `AGENTS.md` | Update "Stopped by a quota limit" Terminology entry (~line 172): add "Cancel schedule" to controls list; add gear-menu auto-schedule toggle note; update "Settled" annotation to also reference `261013_AUTO_SCHEDULE_RESUME_MODE`. | 4 |
| `plans/tests/260701_POWERATLAS.md` | Add section `2.31 Auto-schedule resume mode` (route descriptions, TestResume probe list). Widen scope comment (~line 603) to include `2.31`. | 5 |

## Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Config field + resume.py foundation | Complete | code: 23b5d56, fix: 6755534 |
| 2 | `_auto_schedule_new()` + tick() integration | Complete | code: a543925, fix: 44da1ea, 6e96684 |
| 3 | Web routes + cancel modification + template context | Complete | code: 655382d, fix: 44da1ea, 6e96684 |
| 4 | Gear-menu toggle | Complete | code: f45e117, fix: 5c349bc |
| 5 | Tests | Complete | code: aba43b7, fix: 1ad61b1 |

## 9) Implementation Divergences from Plan

*Reserved — filled during implementation.*

## Follow-up Work (Deferred)

1. **Dismiss-after-snapshot phantom schedule.** A dismiss() call between `_auto_schedule_new()`'s snapshot and its `schedule()` call can create a schedule for a dismissed session, which `tick()`'s own dismissed check drops silently. The race window is ≤5 s, no resume fires. A full fix would check dismissed inside `schedule()`'s lock using pre-fetched `key_at` from `quota.find_hit()` — viable but adds complexity for an edge case with no user impact. Source: Design Decisions (Race #3 row), Phase 2 code comment.

## Review Log

### 2026-10-13 — /qdev Phase 2 review (full effort, 1 persona: Reliability)

3 findings (1 High, 1 Medium, 1 Low). All resolved.

| # | Severity | Finding | Persona | Resolution |
|---|---|---|---|---|
| 1 | High | No automated tests in Phase 2 commit — sub-agent's "9 new tests" claim was incorrect; behavioral exit criteria ticked without test verification | Reliability | Noted as divergence; Phase 5 provides formal behavioral test coverage for all Phase 2 exit criteria |
| 2 | Medium | Misleading `_if_unscheduled` guard comment: says only "watching" but blocks all `_runs` states | Reliability | Fixed — comment updated to list all states: watching, gave_up, error, no_activity (44da1ea) |
| 3 | Low | `dismissed_keys` uses exact float equality; undocumented why it's safe vs `tick()`'s tolerance | Reliability | Fixed — added inline comment explaining safety (44da1ea) |

### 2026-10-13 — /qdev Phase 3 review (full effort, 2 personas: Senior + Security)

5 findings (1 High, 2 Medium, 2 Low). 4 fixed, 1 deferred to Phase 5.

| # | Severity | Finding | Persona | Resolution |
|---|---|---|---|---|
| 1 | High | `cancel_with_skip()` wrote skip record regardless of `auto_schedule` setting — violated SC-9 and exit criteria 5/6 | Senior (H), Security (L) | Fixed — added `and load_config().auto_schedule` gate, then moved config read outside `_lock` (44da1ea, 6e96684) |
| 2 | Medium | Bad-body cancel route returned 200/"Invalid JSON body" instead of plan-specified 400/"bad request"; test needed update | Senior | Fixed — implemented 400 response; test updated (44da1ea) |
| 3 | Medium | `_PATHS` missing `/api/quota/auto-schedule` — `_REMOTE_ALLOWED_PATHS` exclusion untested | Senior | Deferred to Phase 5 (added to Phase 5 exit criteria) |
| 4 | Low | Cancel route docstring named wrong function and wrong validation style | Security | Fixed — docstring updated (44da1ea) |
| 5 | Low | Phase 3 exit criteria 3+4 ticked without automated test | Senior | Noted; Phase 5 adds explicit remote-path assertion |

Diff-only re-review of Phase 3 H1 fix (commit 44da1ea): 1 Low (load_config inside lock) → fixed by 6e96684. No regressions.

### 2026-10-13 — /qdev Phase 1 review (full effort, 2 personas: Reliability + Senior)

6 findings (0 High, 1 Medium, 5 Low). 6 auto-fixed (1-cycle cap per AGENTS.md; all fixes mechanical). Cycle 2 skipped per user 1-cycle cap; Medium was a clear guard-placement fix with no design choice.

| # | Severity | Finding | Persona | Resolution |
|---|---|---|---|---|
| 1 | Medium | `skipped` parsing outside `if isinstance(raw, dict)` guard in `_load()` — `raw.get()` throws `AttributeError` on non-dict JSON root; existing handler doesn't catch it | Senior | Fixed — moved inside guard; initialized `skipped = []` before guard so name always defined (6755534) |
| 2 | Low | Missing blank line between `_load()` and `_save()` (PEP 8) | Senior | Fixed — added blank line (6755534) |
| 3 | Low | Misleading `cancel_with_skip()` docstring referenced a "two-step" design that was never implemented | Senior | Fixed — rephrased to describe lock semantics (6755534) |
| 4 | Low | `_state.get("skipped", [])` in `restore()` redundant after `_load()` guarantees the key | Senior, Reliability | Fixed — changed to `_state["skipped"]` (6755534) |
| 5 | Low | Double-save in `restore()` — first `_save()` written before skip prune, leaving intermediate inconsistency | Reliability | Fixed — merged into single `_save()` at end of prune block (6755534) |
| 6 | Low | `cancel_with_skip()` missing `_valid_session_id()` upfront check | Reliability | Fixed — added early validation mirroring `schedule()` (6755534) |

### 2026-10-13 — /qplan Step 4 (full effort, 4 personas)

13 findings (1 High, 3 Medium, 9 Low). 13 auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | Cancel+skip non-atomic: scheduler can re-arm between `cancel()` and `add_skip()` to_thread calls, violating SC-4 | Fixed — replaced two-step with atomic `cancel_with_skip()` (Phases 1 + 3) |
| 2 | Medium | Phase 4 exit criteria omit `node tests/acp_page.test.mjs` required by AGENTS.md for template inline script changes | Fixed — added criterion to Phase 4 |
| 3 | Medium | `quota.interrupted()` comment claims "O(1)" — actual cost is O(N stat calls) | Fixed — comment corrected to "file-stat-memoized" in Phase 2 |
| 4 | Medium | `_runs` test coverage only exercises `"gave_up"`; `"error"` and `"no_activity"` states not tested | Fixed — added 2 parametrized tests to Phase 5 |
| 5 | Low | Exception from `find_hit()` inside `cancel_with_skip` can turn a successful cancel into 500 | Fixed — try/except wraps `asyncio.to_thread(resume.cancel_with_skip, ...)` in Phase 3 |
| 6 | Low | `_auto_schedule_new()` skip check used exact float; `add_skip()` uses 1-second tolerance | Fixed — skip check now uses `abs() < 1.0` tolerance in Phase 2 |
| 7 | Low | Stale skip entries not age-filtered in `_auto_schedule_new()` snapshot | Fixed — age-filter applied when building `_skipped_live` in Phase 2 |
| 8 | Low | `restore()` does not prune expired skip entries on restart | Fixed — added prune pass inside `restore()` in Phase 1 |
| 9 | Low | No INFO log on `auto_schedule` toggle | Fixed — `log.info("auto_schedule set to %s", enabled)` added in Phase 3 |
| 10 | Low | SC-9 test gap: no test for `resume_now()` with `auto_schedule=off` | Fixed — `test_resume_now_when_auto_schedule_off_unchanged` added to Phase 5 |
| 11 | Low | `add_skip()` no-op path (find_hit=None) not tested | Fixed — `test_add_skip_for_expired_session_is_noop` added to Phase 5 |
| 12 | Low | `"persisted": True` in `_if_unscheduled` early return is inaccurate (nothing written) | Fixed — changed to `"persisted": False` in Phase 1 |
| 13 | Low | Implicit UUID validation gate via `cancel()` ok=True not documented | Fixed — docstring note added to `api_quota_cancel` in Phase 3 |

Personas: Architect, Senior engineer, Reliability engineer (escalated H1 from Medium), Security auditor. Deduplication: H1 corroborated by all three code reviewers (kept High per cross-persona severity rule); findings 6 and 3 each corroborated by two personas.

### 2026-10-13 — /qdev Step 9 final holistic review (full effort, 2 personas: Senior + Reliability)

9 findings (0 High, 3 Medium, 6 Low). 8 auto-fixed (6572f77); 1 accepted (R-L2, design parity with dismissed).

| # | Severity | Finding | Persona | Resolution |
|---|---|---|---|---|
| M1 | Medium | `schedule(_if_unscheduled=True)` missing skip-list check — race between snapshot and schedule() call could create phantom schedule that tick() fires | Reliability | Fixed — added skip-list check inside guard (6572f77) |
| M2 | Medium | No test for `schedule(_if_unscheduled=True)` respecting skip record | Reliability | Fixed — added `test_schedule_if_unscheduled_respects_skip_record` (6572f77) |
| M3 | Medium | `restore()` skip-prune path zero test coverage | Senior | Fixed — added `test_restore_prunes_stale_skip_records` (6572f77) |
| L1 | Low | `auto_schedule_on` pre-lock comment didn't note both toggle-direction failure cases | Reliability | Fixed — comment updated (6572f77) |
| L2 | Low | Stale skips not pruned in `_auto_schedule_new()` — bounded by TTL, consistent with dismissed pattern | Reliability | Accepted — design parity; no code change |
| L3 | Low | Skip round-trip (disk→reload→tick) not end-to-end tested | Senior | Fixed — added `test_skip_round_trips_through_disk_and_blocks_auto_schedule` (6572f77) |
| L4 | Low | `cancel()` missing docstring note to prefer `cancel_with_skip()` | Senior | Fixed — docstring added (6572f77) |
| L5 | Low | Duplicate Phase 2/Phase 5 tests, no comment distinguishing them | Senior | Fixed — comments added to Phase 2 variants (6572f77) |
| L6 | Low | `aria-pressed` not set optimistically in toggle JS — transient screen-reader gap | Senior | Fixed — optimistic set + rollback added (6572f77) |

Diff-only cycle-2 re-review of M1 fix: clean — no new issues. Callers unaffected, `_key_at_val` match correct, guard scope correct.

Step 9b QA (2026-10-13): **PASS** — 10/10 browser checks via standalone Playwright: dashboard signs in, toggle appears in gear menu after "Notify me" with correct label and role=switch, aria-pressed reflects server state on load and updates on click, API stays in sync.

## Harness Improvement Opportunities

- `/qexplore` Step 3 filename creation used `Get-Date -Format "yyMMdd-HHmm"` producing date-time format `261013-1914_` which fails qvalidate's `filename-grammar` check (active plans need `YYMMDD_`, no time suffix) — cost: one rename commit and a qvalidate failure cycle — suggested change: use `Get-Date -Format "yyMMdd"` in the `/qexplore` Step 3 filename note.
