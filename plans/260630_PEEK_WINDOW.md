# Peek Window — Hotkey-held Native HUD Overlay

> **Date**: 2026-06-30
> **Status**: In Progress  <!-- Status lifecycle: Exploring → Draft → In Progress → Complete -->
> **Scope**: Global hotkey-held native window showing the PowerAtlas dashboard as a full-screen overlay
> **Estimated effort**: 2-3 days

---

## Intent

### Problem statement & desired outcomes

PowerAtlas currently requires opening a browser tab to view the dashboard. Users want an instant HUD overlay that appears on a global hotkey hold for quick status checks and session launches without context-switching to a browser.

### Success criteria

1. Pressing `Ctrl+Shift+Z` (configurable) shows a full-screen, frameless, always-on-top native window rendering the PowerAtlas dashboard in <1s
2. Releasing either Ctrl or Shift hides the window instantly
3. User can interact with the dashboard (launch sessions, pin, etc.) while the window is visible
4. Dashboard data refreshes on every show
5. Hotkey is configurable from `config.toml` and the web UI settings page
6. Works on Windows and Linux (X11)
7. Gracefully degrades (feature disabled with warning) if pywebview is unavailable

### Scope boundaries & non-goals

**In scope**: Global hotkey detection, native peek window (pywebview), show/hide lifecycle, config integration, graceful degradation.

**Non-goals**: Wayland support (matches existing X11 constraint), cross-window sync between peek and browser (independent refresh), transparency/opacity effects, window size/position configurability (always full-screen).

## 1) Current State

**Architecture**: Python desktop app with FastAPI/uvicorn HTTP server + pystray system tray icon. UI is browser-based HTML/htmx served on a dynamic port. Single-instance via named mutex (Windows) or file lock (Linux).

**Threading model** (`__main__.py:150-175`):
- Main thread: pystray `icon.run()` blocks (required on Windows)
- Daemon thread 1: uvicorn `server.run()` (asyncio event loop)
- Daemon thread 2: `warmup_pinned()` (short-lived, preloads cache)
- Async task: `_background_refresh()` every 30s

**Server URL lifecycle** (`__main__.py:167-173`): Port extracted after ready event, passed to `run_tray(server_url, config)` as a string. Not stored globally — lives only as a local variable and a closure capture in `tray.py`.

**Relevant dead config** (`config.py:25`): `use_pywebview: bool = True` — field exists, persisted/validated in settings, but never consumed for windowing decisions. pywebview was previously removed (commit `0c1221a`).

**Session data warmth** (`data.py:50-86`): `SessionCache` is thread-safe. Pinned folder sessions are always warm (loaded at startup). Workspace discovery list expires after 30s TTL in `_cache` dict (NOT thread-safe).

## 2) Goal

Add a global-hotkey-triggered native overlay window (peek) that shows the PowerAtlas dashboard full-screen, appears in <1s, and hides on modifier key release. The window is pre-created at startup and uses pywebview to render the existing FastAPI HTML.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Window technology | pywebview (frameless, on_top) | tkinter+embedded browser, native Win32/GTK window | Renders existing HTML without a new UI layer; frameless+on_top gives overlay UX. Prior rejection was for main UI, not overlay. |
| Hotkey library | pynput>=1.7 | keyboard (needs root on Linux), system_hotkey (no release detection), native RegisterHotKey/XGrabKey | Only option with press+release detection, cross-platform, no root, same author as pystray |
| Hotkey default | Ctrl+Shift+Z | Caps Lock, Ctrl+Space | User-chosen. Three-key combo avoids flash on other Ctrl+Shift+* shortcuts. |
| Window size | Full-screen (primary monitor) | Centered fixed-size, cursor-anchored | User-chosen. |
| Window lifecycle | Pre-created hidden at startup | Create on demand | <1s latency requires pre-created window. ~30-50MB RAM tradeoff acceptable. |
| Thread ownership | Platform-split: Win=pywebview on bg thread, Linux=pywebview on main thread | Single-thread model | EdgeChromium (Win) works on non-main thread; GTK (Linux) requires main thread. |
| Data refresh | Refresh on every show | Keep stale, background refresh | User-chosen. Each show triggers htmx reload. |
| Degradation | Graceful — disable peek if pywebview unavailable | Hard dependency | Keeps app functional for users who only want browser UI |
| `use_pywebview` field | Remove and replace with `peek_hotkey` | Keep both | Dead config causes confusion; clean replacement |

## 4) External Dependencies & Costs

### Required external changes

| Category | Change needed | Owner | Status |
|---|---|---|---|
| Third-party services | None | — | N/A |

### Cost impact

None — no cloud/infra changes. Two new PyPI dependencies (`pywebview>=5.0`, `pynput>=1.7`) with no licensing concerns (BSD/LGPL).

Linux users need `gir1.2-webkit2-4.1` system package for pywebview (documented in README).

## 5) Implementation Phases

### Phase 1: Config and dependency changes [QA] [P:2]

**Goal**: Add `peek_hotkey` config field, remove dead `use_pywebview` field, add new dependencies.

**File scope**: `src/power_atlas/config.py`, `pyproject.toml`, `tests/test_config.py`, `src/power_atlas/web.py` (settings form references)

**Changes**:

`config.py` — replace `use_pywebview` with `peek_hotkey`:
```python
@dataclass
class Config:
    trust_all_tools: bool = False
    peek_hotkey: str = "ctrl+shift+z"  # replaces use_pywebview
    terminal_command: str = ""
    pinned_folders: list[str] = field(default_factory=list)
    pinned_sessions: list[str] = field(default_factory=list)
    workspace_icons: dict[str, str] = field(default_factory=dict)
    custom_launchers: list[dict] = field(default_factory=list)
```

`pyproject.toml` — add dependencies:
```toml
dependencies = [
    # ... existing ...
    "pywebview>=5.0",
    "pynput>=1.7",
]
```

`web.py` — remove `config.use_pywebview = "use_pywebview" in form` from `save_settings` (line 78), remove `"use_pywebview": bool` from `_SETTING_TYPES` (line 328), add `"peek_hotkey": str` to `_SETTING_TYPES`.

Note: existing config.toml files with `use_pywebview` will have the field silently ignored on load (by design — `load_config` skips unknown keys) and dropped on next save. No migration needed since the field was dead (never consumed for behavior).

`tests/test_config.py` — update tests: remove `use_pywebview` references, add `peek_hotkey` field tests (default value, round-trip persistence).

**Exit criteria**:
- [x] `Config` dataclass has `peek_hotkey: str = "ctrl+shift+z"`, no `use_pywebview`
- [x] `pyproject.toml` lists `pywebview>=5.0` and `pynput>=1.7`
- [x] `config.use_pywebview` line removed from `save_settings` handler
- [x] `use_pywebview` removed from `_SETTING_TYPES`; `peek_hotkey: str` added
- [x] `test_config.py` passes with updated fields
- [x] README.md config example replaces `use_pywebview` line with `peek_hotkey`
- [x] README.md documents Linux system package requirement (`gir1.2-webkit2-4.1`)

#### Implementation (2026-06-30, code: 537973c, fix: d5aa5f3)

Replaced the dead `use_pywebview: bool = True` config field with `peek_hotkey: str = "ctrl+shift+z"` across the Config dataclass, web.py's save_settings handler and `_SETTING_TYPES` dict, and tests. Added `pywebview>=5.0` and `pynput>=1.7` to pyproject.toml dependencies. Updated README.md to show the new `peek_hotkey` config line and document the `gir1.2-webkit2-4.1` Linux system package requirement. All 10 config tests pass. Review fixes: corrected README comment from "empty = disabled" to accurate format documentation, added int-vs-bool type coverage and peek_hotkey wrong-type test.

### Phase 2: Peek module — hotkey listener + pywebview window [QA] [P:1]

**Goal**: New `peek.py` module encapsulating the pynput hotkey listener and pywebview window lifecycle.

**File scope**: `src/power_atlas/peek.py` (new), `tests/test_peek.py` (new)

**Changes**:

Create `src/power_atlas/peek.py`:
```python
"""Peek window: hotkey-held native overlay showing the dashboard."""

import logging
import sys
import threading
from typing import Callable

log = logging.getLogger("power_atlas.peek")

_AVAILABLE = True
try:
    import webview
    from pynput import keyboard
except ImportError as e:
    _AVAILABLE = False
    _IMPORT_ERROR = str(e)


def is_available() -> bool:
    """Return True if peek dependencies are importable."""
    return _AVAILABLE


class PeekWindow:
    """Manages the pywebview overlay window and pynput hotkey listener."""

    def __init__(self, server_url: str, hotkey: str = "ctrl+shift+z"):
        if not _AVAILABLE:
            raise RuntimeError(f"Peek unavailable: {_IMPORT_ERROR}")
        self._server_url = server_url
        self._hotkey = hotkey
        self._window: webview.Window | None = None
        self._visible = False
        self._listener: keyboard.Listener | None = None
        self._trigger_keys = self._parse_hotkey(hotkey)
        self._pressed_keys: set = set()
        self._triggered = False  # True after full combo pressed
        self._webview_ready = threading.Event()

    def start(self, on_main_thread: bool = False) -> None:
        """Start the peek window and hotkey listener.
        
        Args:
            on_main_thread: If True, webview.start() is called on the
                current thread (blocks). If False, starts on a new thread.
        """
        if on_main_thread:
            self._start_listener()
            self._run_webview()  # blocks
        else:
            t = threading.Thread(target=self._run_webview, daemon=True)
            t.start()
            self._webview_ready.wait(timeout=10)
            if not self._webview_ready.is_set():
                log.warning("Peek webview did not become ready within 10s — peek may be non-functional")
            self._start_listener()

    def stop(self) -> None:
        """Stop the hotkey listener and destroy the window. Final — call only at process exit."""
        if self._listener:
            self._listener.stop()
            self._listener = None
        if self._window:
            self._window.destroy()
            self._window = None

    def _run_webview(self) -> None:
        """Create and run the pywebview window."""
        self._window = webview.create_window(
            "PowerAtlas",
            self._server_url,
            frameless=True,
            on_top=True,
            hidden=True,
        )
        webview.start(func=self._on_webview_ready, debug=False)

    def _on_webview_ready(self) -> None:
        """Called when webview is ready."""
        log.info("Peek webview ready")
        self._webview_ready.set()

    def _start_listener(self) -> None:
        """Start the pynput keyboard listener."""
        try:
            self._listener = keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
            )
            self._listener.daemon = True
            self._listener.start()
            log.info("Peek hotkey listener started (hotkey: %s)", self._hotkey)
        except Exception as e:
            log.warning("Failed to start hotkey listener: %s", e)
            self._listener = None

    def _show(self) -> None:
        if self._window and not self._visible:
            self._visible = True
            log.debug("Peek show")
            self._window.show()
            # Refresh data via JS (avoids full page reload, preserves DOM/scroll)
            self._window.evaluate_js("if(typeof doRefresh==='function') doRefresh()")

    def _hide(self) -> None:
        if self._window and self._visible:
            self._visible = False
            log.debug("Peek hide")
            self._window.hide()

    def _on_press(self, key) -> None:
        """Track pressed keys, show on full combo. Escape is a fallback dismiss."""
        normalized = self._normalize_key(key)
        if normalized:
            self._pressed_keys.add(normalized)
        # Escape key fallback: always dismiss if visible
        if self._triggered and normalized == "esc":
            self._triggered = False
            self._pressed_keys.clear()
            self._hide()
            return
        if not self._triggered and self._trigger_keys.issubset(self._pressed_keys):
            self._triggered = True
            self._show()

    def _on_release(self, key) -> None:
        """Hide on modifier release after trigger."""
        normalized = self._normalize_key(key)
        if normalized:
            self._pressed_keys.discard(normalized)
        if self._triggered:
            # Hide when either modifier is released
            modifiers = {k for k in self._trigger_keys if k in ("ctrl", "shift", "alt")}
            if not modifiers.issubset(self._pressed_keys):
                self._triggered = False
                self._hide()

    @staticmethod
    def _parse_hotkey(hotkey: str) -> set[str]:
        """Parse 'ctrl+shift+z' into {'ctrl', 'shift', 'z'}."""
        return {part.strip().lower() for part in hotkey.split("+")}

    @staticmethod
    def _normalize_key(key) -> str | None:
        """Normalize a pynput key to a string."""
        if hasattr(key, "char") and key.char:
            return key.char.lower()
        if hasattr(key, "name"):
            name = key.name.lower()
            # Normalize left/right/bare modifiers
            if name in ("ctrl_l", "ctrl_r", "ctrl"):
                return "ctrl"
            if name in ("shift_l", "shift_r", "shift"):
                return "shift"
            if name in ("alt_l", "alt_r", "alt_gr", "alt"):
                return "alt"
            return name
        return None


def create_peek(server_url: str, hotkey: str = "ctrl+shift+z") -> PeekWindow | None:
    """Factory: create PeekWindow if available, else log warning and return None."""
    if not is_available():
        log.warning("Peek window disabled: %s", _IMPORT_ERROR)
        return None
    # Validate hotkey format: must have at least one modifier and one non-modifier
    _KNOWN_MODIFIERS = {"ctrl", "shift", "alt"}
    parts = {p.strip().lower() for p in hotkey.split("+") if p.strip()}
    modifiers = parts & _KNOWN_MODIFIERS
    non_modifiers = parts - _KNOWN_MODIFIERS
    if not modifiers or not non_modifiers:
        log.warning("Invalid peek_hotkey '%s' (need modifier+key). Falling back to ctrl+shift+z", hotkey)
        hotkey = "ctrl+shift+z"
    try:
        return PeekWindow(server_url, hotkey)
    except Exception as e:
        log.warning("Peek window disabled: %s", e)
        return None
```

`tests/test_peek.py` — unit tests:
- `test_parse_hotkey`: verifies `ctrl+shift+z` → `{"ctrl", "shift", "z"}`
- `test_normalize_key`: verifies left/right modifier normalization
- `test_is_available`: verifies graceful handling when imports fail (mock)
- `test_create_peek_unavailable`: verifies `create_peek()` returns None when deps missing

**Exit criteria**:
- [x] `peek.py` module created with `PeekWindow` class
- [x] Hotkey parsing handles `ctrl+shift+z` format correctly
- [x] Key normalization handles left/right/bare modifier variants
- [x] Escape key dismisses the peek window as a fallback
- [x] `create_peek()` validates hotkey format (falls back to default on invalid)
- [x] `create_peek()` returns None gracefully when pywebview unavailable
- [x] Lifecycle methods (`_show`, `_hide`, `_start_listener`) log at appropriate levels
- [x] `_start_listener` catches X11/display errors gracefully on Linux
- [x] `_show()` uses `evaluate_js("doRefresh()")` not `load_url()` for data refresh
- [x] `test_peek.py` passes (new test file justified: tests a new module)

#### Implementation (2026-06-30, code: 8003973, fix: 7b66bc8)

Created `src/power_atlas/peek.py` with the `PeekWindow` class encapsulating the pynput hotkey listener and pywebview overlay lifecycle — hotkey parsing normalizes `ctrl+shift+z` format, key normalization handles left/right/bare modifier variants, escape dismisses the window as a fallback, and `create_peek()` validates the hotkey (falling back to default on invalid) and returns None gracefully when dependencies are unavailable. Created `tests/test_peek.py` with 25 tests covering parse_hotkey, normalize_key, is_available, create_peek, and hotkey state-machine behavior. Review fixes: added local-capture pattern for thread safety in `_show`/`_hide`, `_webview_ok` flag to gate show on webview readiness, empty-string filter in `_parse_hotkey`, removed unused imports and no-op test fixture, added 6 state-machine tests.

### Phase 3: Integration into app startup [QA]

**Goal**: Wire the peek module into `_run_foreground()` with platform-specific thread dispatch and correct shutdown/restart flow on both platforms.

**File scope**: `src/power_atlas/__main__.py`, `src/power_atlas/tray.py`

**Changes**:

`tray.py` — modify `on_quit` and `on_restart` to also signal peek shutdown. Add a module-level `_peek_stop_callback`:
```python
_peek_stop_callback: Callable | None = None

def set_peek_stop_callback(cb: Callable) -> None:
    """Register a callback to stop the peek window on tray quit/restart."""
    global _peek_stop_callback
    _peek_stop_callback = cb
```

In `on_quit` and `on_restart`, call the peek stop callback before `icon.stop()`:
```python
def on_quit(icon, item):
    if _peek_stop_callback:
        _peek_stop_callback()  # unblocks webview.start() on Linux
    _shutdown_event.set()
    icon.stop()

def on_restart(icon, item):
    global _restart_requested
    _restart_requested = True
    if _peek_stop_callback:
        _peek_stop_callback()  # unblocks webview.start() on Linux
    _shutdown_event.set()
    icon.stop()
```

Also update the `/api/restart` handler in `web.py` to call the callback:
```python
@app.post("/api/restart")
async def api_restart():
    import power_atlas.tray as _tray
    _tray._restart_requested = True
    if _tray._peek_stop_callback:
        _tray._peek_stop_callback()
    _tray._shutdown_event.set()
    if _tray._icon_instance:
        _tray._icon_instance.stop()
    return {"ok": True}
```

`__main__.py` — restructured `_run_foreground()`:
```python
def _run_foreground() -> None:
    # ... existing setup through server_url ...
    
    from .peek import create_peek
    from .tray import run_tray, restart_requested, set_peek_stop_callback
    
    peek = create_peek(server_url, config.peek_hotkey)
    
    if peek:
        # Register peek.stop() so tray quit/restart can unblock webview
        set_peek_stop_callback(peek.stop)
        
        if sys.platform != "win32":
            # Linux: pywebview needs main thread (GTK).
            # Pystray on background daemon thread.
            tray_thread = threading.Thread(target=run_tray, args=(server_url, config), daemon=True)
            tray_thread.start()
            _threading.Thread(target=warmup_pinned, args=(config.pinned_folders,), daemon=True).start()
            peek.start(on_main_thread=True)  # blocks until peek.stop() is called
        else:
            # Windows: pywebview on background thread, pystray on main.
            peek.start(on_main_thread=False)
            _threading.Thread(target=warmup_pinned, args=(config.pinned_folders,), daemon=True).start()
            run_tray(server_url, config)  # blocks until tray quit
    else:
        # No peek available — original path
        _threading.Thread(target=warmup_pinned, args=(config.pinned_folders,), daemon=True).start()
        run_tray(server_url, config)

    # Shutdown sequence (reached after blocking call returns on either platform)
    if peek:
        peek.stop()  # no-op if already stopped by tray callback
    
    server.should_exit = True
    server_thread.join(timeout=5)
    should_restart = restart_requested()
    _remove_pid()
    _release_mutex()
    logging.shutdown()
    
    if should_restart:
        _relaunch_detached()
    os._exit(0)
```

**Shutdown flow (Linux path)**:
1. User clicks "Quit" or "Restart" in tray menu
2. `on_quit`/`on_restart` calls `_peek_stop_callback()` → `peek.stop()` → `window.destroy()`
3. `webview.start()` returns (unblocks main thread)
4. Main thread continues to shutdown sequence
5. `peek.stop()` called again (no-op — window already None)
6. Server stopped, PID/mutex cleaned, exit (or restart)

**Shutdown flow (Windows path)**:
1. User clicks "Quit" or "Restart" in tray → `icon.stop()` returns
2. `run_tray()` unblocks → main thread continues
3. `peek.stop()` destroys the background webview thread
4. Normal shutdown

**Exit criteria**:
- [ ] Peek window starts on Windows (pywebview on background thread, pystray on main)
- [ ] Peek window starts on Linux (pywebview on main thread, pystray on background thread)
- [ ] Tray quit on Linux unblocks main thread via `peek.stop()` callback
- [ ] Tray restart on Linux triggers full restart cycle (no hang)
- [ ] `/api/restart` endpoint works with peek active on both platforms
- [ ] Original startup path preserved when peek is unavailable
- [ ] Shutdown cleans up peek (idempotent `peek.stop()`)
- [ ] Server URL is accessible to the peek module

### Phase 4: Settings UI for peek hotkey [QA]

**Goal**: Add peek hotkey configuration to the web settings page.

**File scope**: `src/power_atlas/templates/settings.html`, `src/power_atlas/web.py`

**Changes**:

`settings.html` — add a text input for `peek_hotkey`:
```html
<div class="setting-row">
  <label for="peekHotkey">Peek hotkey</label>
  <input type="text" id="peekHotkey" name="peek_hotkey" 
         value="{{ config.peek_hotkey }}" placeholder="ctrl+shift+z">
  <small>Format: modifier+modifier+key (e.g., ctrl+shift+z). Requires restart.</small>
</div>
```

`web.py` `save_settings` — add `peek_hotkey` to the form handling with basic validation:
```python
raw_hotkey = form.get("peek_hotkey", "ctrl+shift+z").strip().lower()
# Basic validation: must contain at least one modifier and one non-modifier
parts = {p for p in raw_hotkey.split("+") if p}
if parts & {"ctrl", "shift", "alt"} and parts - {"ctrl", "shift", "alt"}:
    config.peek_hotkey = raw_hotkey
# else: keep existing value (invalid input silently rejected)
```

**Exit criteria**:
- [ ] Settings page shows peek hotkey field with current value
- [ ] Saving settings validates hotkey format (rejects invalid values)
- [ ] Saving settings persists valid `peek_hotkey` to config.toml
- [ ] Settings page displays "Requires restart" note inline
- [ ] Update README.md with peek hotkey configuration documentation

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| pywebview main-thread on Linux conflicts with pystray GTK | App crash or freeze | Platform-split threading (Phase 3); empirical testing needed. Fallback: if webview on background thread works on Linux too, simplify to uniform model. |
| pynput doesn't detect key release on some keyboard layouts | Peek window stuck visible | Escape key fallback implemented in Phase 2 `_on_press` handler |
| pywebview `start()` called once limitation | Can't restart peek without full app restart | Design as show/hide lifecycle; `stop()` only at process exit; documented in code |
| pywebview EdgeChromium on non-main thread (Windows) | Webview crash or no render | Phase 3 exit criterion requires empirical verification; if fails, swap thread model (pywebview on main, pystray on bg — same as Linux path) |
| `_cache` dict in data.py not thread-safe | Torn read on concurrent access | CPython GIL makes dict assignment atomic for simple types; acceptable for desktop app. Worst case: stale or partial workspace list for one peek show cycle. |
| Wayland-only Linux systems | Hotkey doesn't work | Document X11 requirement (matches existing `_ensure_display()` constraint); `_start_listener` catches X11 errors gracefully |
| RAM cost of hidden webview (~30-50MB) | Higher baseline memory | Acceptable for always-on desktop app |
| Invalid hotkey config | Feature silently broken | `create_peek()` validates and falls back to default; `save_settings` rejects invalid input |
| Linux shutdown/restart deadlock | App hangs on quit | Tray quit/restart calls `peek.stop()` via callback, unblocking `webview.start()` on main thread |

## 7) Verification

- `pytest` — all existing + new tests pass
- Manual: start app, press Ctrl+Shift+Z → full-screen overlay appears
- Manual: release Ctrl or Shift → overlay disappears in <100ms
- Manual: click "Launch" button while peek visible → session launches
- Manual: change `peek_hotkey` in config.toml, restart, verify new hotkey works
- Manual: uninstall pywebview, start app → warning logged, peek disabled, tray+browser still work
- Manual: test on Linux (X11) — same behavior as Windows

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Add peek window feature description, Linux system package requirement, hotkey config | 1, 4 |

## 9) Implementation Divergences from Plan
<Reserved — filled during implementation>

## Review Log

### 2026-06-30 — Plan Review (high-effort, 4 personas: Architect, Senior engineer, Reliability engineer, End-user advocate)

13 unique findings (4 High, 7 Medium, 4 Low merged from ~50 raw findings across 4 personas). 11 auto-resolved.

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | High | Linux shutdown/restart deadlocks — no mechanism to unblock webview.start() | Resolved — added peek_stop_callback in tray.py; explicit shutdown flow documented |
| 2 | High | Escape key fallback not implemented despite being in Risk Assessment | Resolved — added Escape handling in _on_press |
| 3 | High | _SETTING_TYPES cleanup ambiguous (use_pywebview removal not explicit) | Resolved — Phase 1 exit criteria now explicitly list both remove and add |
| 4 | High | No hotkey format validation — invalid config silently breaks feature | Resolved — validation in create_peek() and save_settings |
| 5 | Medium | load_url() on every show defeats <1s latency goal | Resolved — changed to evaluate_js("doRefresh()") |
| 6 | Medium | pywebview ready-event timeout silently ignored | Resolved — added warning log and graceful handling |
| 7 | Medium | Config migration for use_pywebview users | Noted — field was dead (never consumed); silent drop is by design; documented in plan |
| 8 | Medium | Interaction while holding modifiers has UX tension | Noted — inherent to hold-to-peek UX; Escape fallback mitigates stuck state |
| 9 | Medium | No close hint for accidental activation | Noted — deferred to UX polish; Escape key is the safety valve |
| 10 | Medium | _normalize_key missed bare "ctrl"/"alt" key names | Resolved — added to normalization logic |
| 11 | Medium | Phase 3 import pattern fragile (conditional imports) | Resolved — moved to single import before platform branch |
| 12 | Low | _pressed_keys has no timeout/cleanup | Noted — CPython GIL + single listener thread makes this safe; cleared on hide |
| 13 | Low | No accessibility annotations for peek overlay | Noted — left for user review (low-priority polish) |


### 2026-06-30 -- Implementation Review (after Phase 1, persona: Senior engineer, Maintainability reviewer, Reliability engineer, End-user advocate)

Implementation health: Yellow (all auto-fixed).
4 findings (0 High, 2 Medium, 2 Low).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | README claims "empty = disabled" but code falls back to default hotkey | Fixed — changed comment to "global overlay hotkey (modifier+key format)" |
| 2 | Medium | pywebview/pynput unconditional deps may break install on headless systems | Accepted — plan design decision; graceful degradation covers runtime import failure |
| 3 | Low | test_wrong_type_bool lost int-vs-bool coverage after removing use_pywebview | Fixed — restored int-for-bool test case |
| 4 | Low | No test for peek_hotkey with wrong type (integer value) | Fixed — added test_wrong_type_str_gets_default |

### 2026-06-30 -- Implementation Review (after Phase 2, persona: Reliability engineer, Senior engineer, Maintainability reviewer, Security auditor)

Implementation health: Yellow (all auto-fixed).
6 findings (0 High, 2 Medium, 4 Low).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | stop() races with listener callbacks — _window destroyed while _show mid-execution | Fixed — added local-capture pattern in _show/_hide |
| 2 | Medium | No tests for _on_press/_on_release hotkey state machine logic | Fixed — added 5 state-machine tests plus trailing-plus edge case |
| 3 | Low | _parse_hotkey doesn't filter empty strings from trailing "+" | Fixed — added `if part.strip()` filter |
| 4 | Low | Unused imports: sys and Callable never referenced | Fixed — removed both |
| 5 | Low | _mock_optional_deps autouse fixture is a no-op | Fixed — removed |
| 6 | Low | After webview timeout, listener still starts but show/hide may crash | Fixed — added _webview_ok flag gating _show |
