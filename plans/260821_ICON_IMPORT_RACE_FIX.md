# Icon Extraction Boot-Time Crash Fix

> **Date**: 2026-08-21
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Fix native C-extension import race in `icons.py` that crashes PowerAtlas on boot

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
6. Existing tests pass without modification.

### Scope boundaries & non-goals

**In scope:**
- `src/power_atlas/icons.py`: move `win32gui`, `win32ui`, `PIL.Image` imports to module level
  with `try/except` / `None` sentinel guard pattern.
- `src/power_atlas/web.py`: wrap the two synchronous `extract_icon` calls (lines 5110, 5124)
  in `await asyncio.to_thread(...)`.
- Update existing tests in `tests/test_web.py` / `tests/test_launcher.py` if signatures or
  behavior change.

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

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

- **`try/except` module-level win32 import pattern** (`acp.py:101–105`): the established precedent
  for conditional pywin32 imports. `import win32api; import win32con; import win32job` wrapped in
  `try: ... except Exception as _e: win32api = win32con = win32job = None`. Callers guard with
  `if win32job is None: ...`.
- **`icons.py` module-level imports** (`icons.py:8–13`): only stdlib (`re`, `shutil`, `sys`,
  `pathlib.Path`) and one local import (`config.CONFIG_DIR`). No win32 or PIL imports.
- **Platform guard is separate** (`icons.py:55–57`): `sys.platform == "win32"` is checked in
  `extract_icon` before calling `_extract_windows_icon`. The lazy imports serve no platform-guard
  purpose — safe to move up.
- **`ctypes` already loaded** (`__main__.py:4`): `import ctypes` at the entry-point module level.
  The `from ctypes import wintypes` inside `_extract_windows_icon` is always a cache hit; it is
  not part of the race and can stay in place (or be moved — no impact).
- **`asyncio.to_thread` executor** (`web.py:5156`): uses Python's default loop executor
  (no `set_default_executor` anywhere in the codebase). Default `ThreadPoolExecutor` creates up
  to `min(32, cpu_count+4)` threads [unverified] — sufficient to run all provider icon extractions
  simultaneously, which is what triggers the race.
- **`icons` imported at `web.py:41`** (module level): `from . import autostart, data, icons, ...`.
  This is executed when `__main__.py` does `from .web import ..., app` — before `server_thread.start()`.
  Module-level win32 imports in `icons.py` will always complete before the server accepts its first
  request.
- **`tray.py` PIL import** (`tray.py:12`): `from PIL import Image, ImageDraw` at module level,
  but `tray.py` is imported inside `_run_foreground` after `ready_event.wait()` — PIL is cold at
  first browser request. PIL must be included in the module-level fix in `icons.py`.
- **Pillow is an unconditional dependency** (`pyproject.toml:14`): no platform guard needed for
  `PIL.Image`.
- **pywin32 is a Windows-conditional dependency** (`pyproject.toml:15`,
  `"pywin32>=306; sys_platform == 'win32'"`): will always be installed on Windows, but the
  `try/except` guard is still correct — it protects against broken installs (pywin32 not registered
  after install) and matches the codebase's defensive pattern.
- **`extract_icon` call sites** (all in `web.py`):
  - `web.py:5110` — `launcher_create` route, blocking call on event loop (no `await to_thread`)
  - `web.py:5124` — `launcher_update` route, blocking call on event loop (no `await to_thread`)
  - `web.py:5156` — `launcher_icon` GET route, already uses `await asyncio.to_thread` (crash site)
- **Return values discarded at all three call sites** — no caller inspects the bool return.
- **Test isolation**: `@patch("power_atlas.web.icons.extract_icon")` is the existing patch target
  (`test_web.py:810`). Wrapping the two sync calls in `to_thread` changes them to coroutines —
  existing tests mock the function entirely so they are unaffected, but any test asserting
  synchronous call behavior would need updating. No such test exists.
- **`acp_page.test.mjs`**: covers inline template JS only — no Python route behavior. Unaffected.

### 5. Risks & mitigations

- **R1 (Low): `win32gui`/`win32ui` import failure at module level crashes `icons.py` import**
  Mitigation: use `try/except Exception` with `None` sentinels (matching `acp.py` pattern).
  Guard `_extract_windows_icon` with `if win32gui is None: return False` at the top.
- **R2 (Low): `PIL.Image` moved to module level causes Linux import failure**
  Non-issue: PIL/Pillow is an unconditional `pyproject.toml` dependency — available on all
  platforms. The `from PIL import Image` is already safe at module level.
- **R3 (Low): wrapping sync calls in `to_thread` breaks existing tests**
  Sub-agents confirmed: all three `extract_icon` tests mock the function entirely via
  `@patch("power_atlas.web.icons.extract_icon")`. The mock works regardless of whether the
  call is sync or async. No test exercises the actual function.
- **R4 (Medium): `await asyncio.to_thread` in `launcher_create`/`launcher_update` changes
  response timing — any test asserting synchronous completion order**
  No such test found. The routes are already `async def` — `await` is syntactically valid.
- **R5 (Resolved): lazy imports served as platform guard**
  Confirmed not the case — `sys.platform == "win32"` check is separate and prior to the
  `_extract_windows_icon` call.

### 6. Resolved decisions

- Q1: Fix approach — module-level imports with `try/except`/`None` sentinels vs threading.Lock
  — A: Option A (module-level with try/except) — Decision: move `win32gui`, `win32ui`,
  `PIL.Image` imports to module level in `icons.py` using the `try/except` / `None` sentinel
  pattern matching `acp.py:101–105`; guard `_extract_windows_icon` entry with
  `if win32gui is None: return False`.
- Q2: Fix the two synchronous blocking `extract_icon` calls in event-loop route handlers
  — A: yes — Decision: wrap `web.py:5110` and `web.py:5124` with `await asyncio.to_thread(...)`.

### 7. Open items

None. All decisions resolved.

### 8. Recommended approach

**Phase 1 — `icons.py` import fix (crash fix, primary)**

At the top of `icons.py`, after the stdlib imports, add:

```python
if sys.platform == "win32":
    try:
        import win32gui as _win32gui
        import win32ui as _win32ui
        from PIL import Image as _PilImage
    except Exception:
        _win32gui = _win32ui = _PilImage = None
else:
    _win32gui = _win32ui = _PilImage = None
```

In `_extract_windows_icon`, replace the lazy import block with a guard:

```python
if _win32gui is None:
    return False
# use _win32gui, _win32ui, _PilImage directly (remove the import lines)
```

The existing `except Exception: return False` at the function level remains as a safety net for
runtime errors (GDI failures, bad PE headers, etc.).

**Phase 2 — `web.py` non-blocking call sites**

Change `web.py:5110` from:
```python
icons.extract_icon(entry["id"], entry["command"], entry["terminal"])
```
to:
```python
await asyncio.to_thread(icons.extract_icon, entry["id"], entry["command"], entry["terminal"])
```

Same change at `web.py:5124`.

**Test updates**: run `pytest` after each phase. No new test files. If any existing test
unexpectedly fails due to the `to_thread` wrapping, update the test's async mock setup.

### 9. QA environment

- **Start PowerAtlas**: `.venv-PowerAtlas\Scripts\power-atlas` from the repo root, or restart
  via the tray icon.
- **Boot-crash verification**: restart PowerAtlas cleanly (tray → Restart), open the dashboard
  in a browser, check `crash.log` for a new entry.
- **Visual regression**: provider icons (kiro-cli, claude-code, kiro-ide) and custom launcher
  icons should render in the dashboard after restart.
- **Test suite**: `.venv-PowerAtlas\Scripts\python -m pytest` — should pass without new failures.
- **Template test**: `node tests/acp_page.test.mjs` — unaffected (no template changes), but run
  to confirm.
- **Cannot reproduce the exact cold-boot race in a test** — the crash requires a real cold-boot
  with the browser opening the dashboard simultaneously. Manual restart + browser open is the
  verification path for the crash fix itself.
