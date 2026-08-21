# Icon Extraction Boot-Time Crash Fix

> **Date**: 2026-08-21
> **Status**: In Progress
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Fix native C-extension import race in `icons.py` that crashes PowerAtlas on boot
> **Estimated effort**: 1–2 hours

---

## Intent

### Problem statement & desired outcomes

PowerAtlas crashes at every login-startup with a `Windows fatal exception: access violation`. The
crash is captured in `crash.log` (pid 4048, 2026-08-21 09:09:39). The process dies before the
tray icon appears, so the app is silently absent until the user manually relaunches it.

Root cause: `_extract_windows_icon` in `src/power_atlas/icons.py` lazily imports three native
C-extension modules (`win32gui`, `win32ui`, and `PIL.Image`) inside the function body. When the
browser opens the dashboard after startup, it fires 3-4 simultaneous `GET /api/launcher-icon/provider--X`
requests. Each request is dispatched via `asyncio.to_thread(icons.extract_icon, ...)` into a
worker thread (`web.py:5156`). Multiple threads race to perform the first-ever C-extension load
of `win32gui` and `win32ui` concurrently, which triggers a native access violation inside the
C-level `PyInit` functions — crashing the entire process before Python's `except Exception`
handler can fire.

Secondary issue: `web.py:5110` and `web.py:5124` call `icons.extract_icon(...)` directly from
`async def` route handlers (on the event loop), making them blocking calls that stall the event
loop during PE binary reads and icon extraction.

Desired outcome: PowerAtlas starts reliably at every login. Icon extraction is thread-safe. The
two blocking event-loop call sites are made non-blocking.

### Success criteria

1. `crash.log` accumulates no new entries on repeated cold-boot startups (manual verification).
2. Provider icons load correctly in the dashboard after restart (no visual regression).
3. Custom launcher icons continue to be extracted and cached correctly on create/edit.
4. The module-level win32 import follows the `try/except` with `None` sentinel pattern established
   in `acp.py:101–105`.
5. The two synchronous `extract_icon` calls in `web.py` (lines 5110, 5124) are wrapped in
   `await asyncio.to_thread(...)`.
6. `test_launcher_create` asserts the icon extraction mock was called with the correct arguments. `test_launcher_update` exists and covers the `to_thread` call.

### Scope boundaries & non-goals

**In scope:**
- `src/power_atlas/icons.py`: move `win32gui`, `win32ui`, `PIL.Image` imports to module level
  with `try/except` / `None` sentinel guard pattern.
- `src/power_atlas/web.py`: wrap the two synchronous `extract_icon` calls (lines 5110, 5124)
  in `await asyncio.to_thread(...)`.
- Update and add tests in `tests/test_web.py`: add mock assertion to `test_launcher_create`, add `test_launcher_update`.

**Not in scope:**
- Adding a test file for parallel icon extraction (no new test files unless the user requests one).
- Changing the icon caching mechanism or storage location.
- Fixing any other blocking calls in route handlers.
- Linux/macOS icon extraction (no such code path exists).

### Invariants

- `extract_icon` must continue to return `bool` and silently return `False` on any failure
  (existing `except Exception: return False` contract — callers discard the return value but
  the contract must hold).
- If `win32gui` or `win32ui` are unavailable at import time (broken pywin32 install), icon
  extraction must degrade silently to no-icon rather than crashing the process.
- The `/api/launcher-icon/{id}` route must continue to serve PNG when extraction succeeds and
  fall back to SVG when it does not — behavior unchanged.
- No change to the public interface of any function in `icons.py`.

---

## Context

PowerAtlas crashes silently at every login-startup. The browser fires 3-4 simultaneous
`GET /api/launcher-icon/provider--X` requests on dashboard open; each dispatches via
`asyncio.to_thread(icons.extract_icon, ...)`. Multiple worker threads race to first-load
`win32gui`, `win32ui`, and `PIL.Image` — native C-extension modules imported lazily inside
`_extract_windows_icon` (`icons.py`, inside function body). CPython's C-extension loader
access-violates mid-`PyInit`, killing the process before any `except` can fire
(`crash.log` 2026-08-21 09:09:39, pid 4048). The established fix pattern — `try: import ...; except Exception: sentinel = None` at module level — already exists in `acp.py:101–105`
for the analogous `win32api`/`win32con`/`win32job` case.

Secondary: `web.py:5110` and `web.py:5124` call `extract_icon` directly on the asyncio event
loop (blocking IO on a launcher-create/update request). These are not crash sites but are
incorrect for an async handler.

## Files to modify

| File | Change |
|---|---|
| `src/power_atlas/icons.py` | Move `win32gui`, `win32ui`, `PIL.Image` imports to module level with `try/except`/`None` sentinel; guard `_extract_windows_icon` entry; remove lazy import lines inside function |
| `src/power_atlas/web.py` | Wrap `extract_icon` calls at lines 5110 and 5124 with `await asyncio.to_thread(...)` |

## External Dependencies

None. Code-only change. No infra, no migrations, no third-party service changes.

## Rollout / Migration / Cleanup

None. The fix takes effect on next PowerAtlas restart. No data migration, no config changes.

## Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Import-safety mechanism | Module-level `try/except` with `None` sentinel | `threading.Lock` + lazy import; no change | Matches `acp.py:101–105` precedent; simpler; eliminates race completely; handles broken pywin32 installs gracefully |
| `PIL.Image` scope | Module-level (no platform guard) | Windows-only via `sys.platform` block | PIL/Pillow is unconditional `pyproject.toml` dep — available on all platforms; no guard needed |
| Event-loop call sites | Wrap in `await asyncio.to_thread` | Leave as blocking calls | Already wrong; fix while in the area; routes are `async def` so `await` is valid |
| Log warning on import failure | Not added | Add `log.warning(...)` matching `acp.py:106` | `icons.py` has no module logger; adding one is a scope expansion. Deferred to follow-up. |
| Write-write race for same launcher_id | Acknowledged, not fixed | Add per-id lock | Two concurrent creates/edits for the same launcher produce last-writer-wins PNG overwrite — benign (identical source data). Not a crash risk. Fixing requires a per-id lock, disproportionate to the risk. |

## Step-by-step

### Phase 1: Move imports to module level in `icons.py` [QA]

**File scope**: `src/power_atlas/icons.py`

After the existing stdlib imports (after `from .config import CONFIG_DIR`, `icons.py:13`), add:

```python
# Module-level import of win32/PIL so concurrent threads cannot race the
# first-ever C-extension load (the crash mechanism: multiple asyncio.to_thread
# calls hitting _extract_windows_icon simultaneously at cold boot).
# Pattern matches acp.py:101-105. Pre-assign before try: so a partial-load
# (win32gui succeeds, win32ui raises) still leaves all sentinels None.
_win32gui = _win32ui = _PilImage = None
if sys.platform == "win32":
    try:
        import win32gui as _win32gui
        import win32ui as _win32ui
        from PIL import Image as _PilImage
        from ctypes import wintypes as _wintypes
    except Exception:  # pragma: no cover - broken pywin32 install
        _win32gui = _win32ui = _PilImage = _wintypes = None
```

> **Rejected**: placing the `try:` block inside an `if sys.platform == "win32":` with a bare
> `else: _win32gui = ... = None` AFTER — this form fails if `win32gui` loads but `win32ui`
> raises: `_win32gui` is non-None, `_win32ui` is unbound. **Use instead**: pre-assign all
> sentinels to `None` on one line before the `try:` block (shown above).

Also move `from ctypes import wintypes` into the module-level `try:` block above. `wintypes`
is a submodule of `ctypes`, not pulled in by the plain `import ctypes` at `__main__.py:4`.
Moving it avoids a redundant per-call import inside the function.

Inside `_extract_windows_icon`, immediately at the top of the `try:` block, **replace** all
five lazy import lines **and** the wintypes usage:

```python
# REMOVE these five lines entirely:
import ctypes
from ctypes import wintypes
import win32gui
import win32ui
from PIL import Image
```

Add a guard at the top of the `try:` block:

```python
# ADD: sentinel guard — use module-level names directly
if _win32gui is None:
    return False
```

Then replace every use of `win32gui`, `win32ui`, `Image`, and `wintypes` in the function body
with `_win32gui`, `_win32ui`, `_PilImage`, and `_wintypes` respectively. No local aliases needed
— use the module-level names directly.

> **Rejected**: local alias lines `win32gui = _win32gui; win32ui = _win32ui; Image = _PilImage`
> inside the function. **Use instead**: reference `_win32gui`, `_win32ui`, `_PilImage`,
> `_wintypes` directly throughout the function body (removes one layer of indirection and keeps
> the module-level sentinel names visible to readers). The `ctypes.WinDLL`, `wintypes.HANDLE`,
> `wintypes.c_uint` references become `ctypes.WinDLL`, `_wintypes.HANDLE`, `_wintypes.c_uint`.

The existing `except Exception: return False` at the function level remains as a safety net for
runtime GDI/PIL errors.

**Exit criteria**:
- [x] `src/power_atlas/icons.py` has no `import win32gui`, `import win32ui`, `from PIL import Image`, or `from ctypes import wintypes` inside any function body
- [x] Module-level sentinel block (`_win32gui = _win32ui = _PilImage = _wintypes = None` + `try:` block) present after `from .config import CONFIG_DIR`
- [x] `_extract_windows_icon` opens its `try:` block with the `if _win32gui is None: return False` guard
- [x] All `win32gui.`, `win32ui.`, `Image.`, `wintypes.` references in `_extract_windows_icon` updated to `_win32gui.`, `_win32ui.`, `_PilImage.`, `_wintypes.`
- [x] `.venv-PowerAtlas\Scripts\python -m pytest tests/test_launcher.py tests/test_web.py` passes with no new failures
- [ ] [manual] PowerAtlas restarts cleanly (tray → Restart), browser opens dashboard, `crash.log` has no new entry — requires a real restart, cannot be automated

**Implementation (2026-08-21, code: 5310bd9 + fixes afa5af7 + 484cebb)**
Module-level sentinel block added. `import ctypes` moved inside `if sys.platform == "win32": try:` alongside win32 imports; PIL imported unconditionally in its own `try/except` block before the platform guard (matching plan design decision). Five lazy imports removed from `_extract_windows_icon`; `if _win32gui is None: return False` guard added at entry. All `win32gui.`/`win32ui.`/`Image.`/`wintypes.` references replaced with `_win32gui.`/`_win32ui.`/`_PilImage.`/`_wintypes.`. Lazy `import re as _re` removed from `_resolve_cmd_to_exe`. GDI handle cleanup split into nested `try/finally` to ensure both `DeleteObject` calls run. Two sentinel tests added with real `.exe` files to reach the guard. `plans/tests/260701_POWERATLAS.md` §4.2 updated: H9 resolved, sentinel probe added. 1553 tests pass.

### Phase 2: Make event-loop call sites non-blocking in `web.py` [QA]

**File scope**: `src/power_atlas/web.py`

Locate the two synchronous calls (search by the surrounding context, not by line number — lines
drift):

**Call site 1** — inside `launcher_create`, after `save_config(config)`:
```python
# BEFORE (blocking on event loop):
icons.extract_icon(entry["id"], entry["command"], entry["terminal"])
```
```python
# AFTER:
await asyncio.to_thread(icons.extract_icon, entry["id"], entry["command"], entry["terminal"])
```

**Call site 2** — inside `launcher_update`, after the `for` loop updates the entry and before
`save_config`:
```python
# BEFORE (blocking on event loop):
icons.extract_icon(lid, entry.get("command", ""), entry.get("terminal", True))
```
```python
# AFTER:
await asyncio.to_thread(icons.extract_icon, lid, entry.get("command", ""), entry.get("terminal", True))
```

Both routes are `async def` so `await` is syntactically valid. The `asyncio` module is already
imported at `web.py`'s module level — no new import needed.

**Test updates (same file `tests/test_web.py`)**:

1. In `test_launcher_create` — add a mock-call assertion after the route call:
   ```python
   mock_extract.assert_called_once_with(<expected_id>, <expected_command>, <expected_terminal>)
   ```
   Check existing test for the exact entry dict values to use.

2. Add `test_launcher_update` parallel to `test_launcher_create`:
   - Patch `power_atlas.web.icons.extract_icon` and `power_atlas.web.save_config`
   - POST to `/api/launcher/update` with a valid `id` matching a config entry and updated fields
   - Assert response 200 and `mock_extract.assert_called_once_with(lid, updated_command, updated_terminal)`

   > `launcher_update` currently has zero test coverage — this plan touches it, so the test
   > must be added here. No new test file needed; add to `test_web.py`.

**Exit criteria**:
- [ ] Neither `icons.extract_icon(...)` call in `web.py` is bare (both are `await asyncio.to_thread(...)`)
- [ ] `test_launcher_create` asserts `mock_extract.assert_called_once_with(...)` with correct args
- [ ] `test_launcher_update` added to `tests/test_web.py`, covers the `to_thread` call with mock assertion
- [ ] `.venv-PowerAtlas\Scripts\python -m pytest tests/test_web.py` passes with no new failures
- [ ] `node tests/acp_page.test.mjs` passes (template JS unaffected — confirm no regression)

## Verification

```
# Full test suite
.venv-PowerAtlas\Scripts\python -m pytest

# Template test
node tests/acp_page.test.mjs

# Manual: PowerAtlas restart + dashboard open
# Tray icon → Restart (or kill + relaunch), open browser, check crash.log for new entry
# Verify provider icons render in dashboard
# Verify creating/editing a custom launcher still extracts + caches the icon
```

## Documentation updates

| Document | Update needed | Phase |
|---|---|---|
| `plans/tests/260701_POWERATLAS.md` § 4.2 | Note `_extract_windows_icon` now has an explicit `_win32gui is None` guard at entry; import errors now surface via sentinel, not the broad `except`. Separately verify whether the existing `finally` blocks (`DeleteObject`/`DestroyIcon`) already close H9 (GDI handle leak) — if so, update that note to "resolved"; if not, leave H9 open and note it is out of scope for this fix. | 1 |

## Follow-up Work (Deferred)

1. **Add `log.warning` on pywin32 import failure in `icons.py`.** `acp.py:106` logs `log.warning("pywin32 unavailable - ...")` when its sentinel is set. `icons.py` has no module logger and adding one is out of scope for this fix. Source: review finding F-I.
2. **Add `log.warning` or observability for other blocking calls in `web.py` routes.** `save_config` and other disk IO remain on the event loop in `launcher_create`/`launcher_update`. Pre-existing; out of scope. Source: Architect review finding #7.

## Review Log

### 2026-08-21 — Implementation Review (after Phase 1, persona: Security auditor, Reliability engineer, Maintainability reviewer, Senior engineer — high effort)

Implementation health: Green.
8 findings (0 High, 2 Medium, 6 Low). All fixed across 2 auto-fix cycles.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| R1 | Medium | `_PilImage` inside `if sys.platform == "win32":` block contradicts plan decision (no platform guard needed for PIL). | Fixed — moved to its own `try/except` block before the platform guard. |
| R2 | Medium | No test exercises the `_win32gui is None` guard; crash fix has zero automated coverage. | Fixed — `TestExtractIconSentinelGuard` added with real `.exe` file to reach the guard. |
| R3 | Low | `_resolve_cmd_to_exe` still has lazy `import re as _re` shadowing module-level `re`. | Fixed — removed lazy import; uses module-level `re` directly. |
| R4 | Low | `import ctypes` unconditional at module level; could be moved inside platform block. | Fixed — moved inside `if sys.platform == "win32": try:`. |
| R5 | Low | Comment cited `acp.py:94-107` without noting structural difference (acp.py has no platform guard). | Fixed — comment updated to note the structural difference. |
| R6 | Low | `plans/tests/260701_POWERATLAS.md` §4.2 describes H9 as open GDI leak; `finally` blocks close it. | Fixed — §4.2 updated to mark H9 resolved; sentinel probe added. |
| R7 | Low | `hbm_color` handle leaks if `DeleteObject(hbm_mask)` raises in the `finally` block (pre-existing). | Fixed — split into nested `try/finally` ensuring both deletes run. |
| R8 | Low | No `log.warning` on pywin32 import failure, unlike `acp.py` pattern. | User: accepted — deferred to Follow-up Work #1 (requires module logger). |

Cycle-2 finding (vacuous sentinel tests): Fixed — tests now create a real `.exe` file so `_resolve_binary` returns it and `_extract_windows_icon` is actually reached.

### 2026-08-21 — Plan Creation (via /qplan)

10 findings (0 High, 4 Medium, 6 Low). 8 auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| F-A | Medium | `launcher_update` route has zero test coverage; SC-3 (edit path) vacuously satisfied | Fixed — `test_launcher_update` added to Phase 2 exit criteria |
| F-B | Medium | `launcher_update` calls `extract_icon` before `save_config`; after `to_thread`, propagated exception skips config save | Fixed — `extract_icon` catches all exceptions internally (`except Exception: return False`); documented as design decision + deferred follow-up; pattern is safe though fragile |
| F-C | Medium | `test_launcher_create` has no mock-call assertion; wrong-args or dropped call undetected | Fixed — mock assertion added to Phase 2 exit criteria |
| F-D | Medium | `from ctypes import wintypes` stays inside function body; `wintypes` is a submodule not pulled in by `import ctypes` | Fixed — `from ctypes import wintypes` moved into module-level `try:` block as `_wintypes` |
| F-E | Medium | Partial-load sentinel gap: if `win32gui` loads but `win32ui` raises, `_win32gui is None` guard misses it | Fixed — pre-assignment `_win32gui = _win32ui = _PilImage = _wintypes = None` before `try:` block |
| F-F | Low | Local alias lines inside function are unnecessary indirection | Fixed — plan updated to use `_win32gui` etc. directly, with Rejected entry |
| F-G | Low | Phase 1 crash.log exit criterion cannot be automated; implies it can be ticked without restart | Fixed — marked `[manual]` in Phase 1 exit criteria |
| F-H | Low | H9 doc update conflated sentinel guard with GDI handle leak | Fixed — doc update row clarified to separately assess H9 via `finally` blocks |
| F-I | Low | No `log.warning` on pywin32 import failure, unlike `acp.py` pattern | Escalated — deferred to Follow-up Work #1; requires adding a module logger |
| F-J | Low | Write-write race for same launcher_id survives (last-writer-wins, identical bytes) | Escalated — acknowledged in Design Decisions; not fixed (benign, disproportionate to fix) |

## Harness Improvement Opportunities

<Reserved>
