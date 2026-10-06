# Merged Peek and App Window with Configurable Shortcuts

> **Date**: 2026-10-06
> **Status**: In Progress  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Turn the peek overlay into one PowerAtlas window with a peek mode and an app mode, add configurable shortcuts, and offer app window and browser from the tray
> **Estimated effort**: 1-2 days

---

## Intent

### Problem statement & desired outcomes

PowerAtlas opens its UI in two ways today: the default browser (tray Open, peek double-tap) and the peek overlay, a frameless full-screen pywebview window shown while the hotkey is held. There is no desktop app window, and the only shortcut setting is `peek_hotkey`.

The user wants a desktop app window without adding Electron. The peek overlay already runs a WebView2 window through pywebview, so the outcome is one PowerAtlas window with two modes:

- **Peek mode**: frameless, always on top, full screen, off the taskbar. Shown by the peek shortcut.
- **App mode**: a normal framed window in the taskbar, focused, at its last size and position. Opened by a double-tap of the peek shortcut or by the tray.

The window keeps its page state across hides and mode switches, instead of resetting to the dashboard on every show. The browser stays available from the tray and from an optional shortcut.

### Success criteria

- SC-1: With the window hidden, holding the peek shortcut shows peek mode; releasing it hides the window. The page shown is the app's current page, not a reset to `/`.
- SC-2: A double-tap of the peek shortcut, from any state, switches the window to app mode, framed, focused, in the taskbar, at its last size, position and maximized state. If the window was in app mode and focused before the first tap, the double-tap hides it.
- SC-3: With the window open in app mode, holding the peek shortcut shows peek mode temporarily; releasing it returns to app mode with the same bounds, maximized state and focus state.
- SC-4: The app window's X button hides the window and keeps its state. It never quits PowerAtlas.
- SC-5: A **Peek mode** setting offers **Hold** (default, today's behaviour) and **Toggle**. In Toggle, a press shows peek mode and the next press ends it; a second press within the double-tap interval opens app mode instead.
- SC-6: An optional **Browser shortcut** setting, empty (off) by default, opens a new signed-in browser tab.
- SC-7: Saving a shortcut rejects an invalid format and rejects a shortcut that equals, or contains, the other shortcut.
- SC-8: The tray menu has **Open PowerAtlas** (default action, app mode) and **Open in browser**, followed by the existing Copy login link, Logs, Restart and Quit. When app mode is unavailable (pywebview or pynput missing or failing to load, or a non-Windows platform), Open PowerAtlas opens the browser and Open in browser is hidden.
- SC-9: After a local-secret rotation, the next show signs the window in again instead of leaving it on the gate page.
- SC-10: Every login URL is still minted in-process, used once and never logged.

### Scope boundaries & non-goals

In scope: Windows behaviour of the merged window, the new settings and their save-time validation, the tray menu, README and existing tests.

Out of scope:
- Electron, Tauri or any second UI runtime.
- Applying shortcut changes without a restart. The new keys are restart-to-apply, like `peek_hotkey`.
- A key-capture widget for shortcut fields.
- Choosing which monitor peek mode covers (it stays the primary screen).
- Saving the app window's size and position across restarts.
- App mode on Linux. Linux keeps today's peek and a double-tap keeps opening the browser.

---

## 1) Current State

**Thread layout** (`src/power_atlas/__main__.py`, `_run_foreground`, around lines 847-890). Uvicorn runs on a daemon thread. With peek available, the tray runs on a daemon thread and `peek.start(on_main_thread=True)` blocks the main thread inside `webview.start()`. Without peek, `run_tray` owns the main thread and no webview exists. The window is the only pywebview window and is created before `webview.start()` (`peek.py`, `_run_webview`, around line 108: `frameless=True, on_top=True, hidden=True, width=1, height=1`).

**Hotkey state machine** (`peek.py`, `_win32_event_filter` around 144-186, `_show` around 211-274, `_hide` 276-287, `_on_press`/`_on_release` 289-319). One chord, parsed into `self._trigger_keys`. Show on full combo; hide when any required modifier is released; Esc dismisses. The double-tap is detected inside `_show` with `_last_trigger_time` and `_DOUBLE_TAP_INTERVAL = 0.5`, and opens the browser with `webbrowser.open(_login_url(...))`. On Windows the filter suppresses the chord's non-modifier key and resets `_triggered` on its key-up, which is what lets a second tap register while modifiers stay held. Matching is `issubset`, so a superset chord also fires. `_show` reloads `_login_url` on every show (lands on `/`). Exceptions escaping a pynput callback stop the listener; `_show`'s `load_url` and the double-tap are guarded, but `win.show/resize/move`, `native.Invoke` and `_hide`'s `win.hide()` are not.

**Tray** (`tray.py`, `run_tray` around 167-222). Fixed menu: Open (default, starts a `warmup_pinned` thread then opens the browser with a fresh login URL), Copy login link, Logs, Restart, Quit. A single module slot `_peek_stop_callback` (`set_peek_stop_callback`, line 25) is called by Quit, Restart and `trigger_restart`. `peek.py` imports `_login_url` from `tray.py` (line 16), so the tray cannot import `peek` back.

**Login code** (`web.py` around 2015-2093 mint/consume; `login_url` around 2570-2587). Single use, 120 s TTL, cap 64, minted in-process only. `/local-auth` redirects to a literal `/` (around line 5007). Rotation (`POST` rotate route, around 6114-6135) calls `set_local_secret(secret)`, clears outstanding codes, re-issues the cookie for the requesting browser only, and closes loopback ACP sockets. The pywebview logger is clamped to INFO because DEBUG logs URLs (`peek.py`, `_clamp_pywebview_logger`).

**Settings** (`web.py`). `_SETTING_TYPES` (around 5812), `_SETTING_BOUNDS`, `_RESTART_TO_APPLY` (around 5847, includes `peek_hotkey`), `set_startup_config` snapshot, `/api/save-setting` (around 5905-6006): strings checked only for length and control characters; empty string accepted; an invalid hotkey is caught only at startup by `create_peek`, which falls back silently to `ctrl+shift+z` (`peek.py` around 347-363). Payloads: index page context (`"peek_hotkey": config.peek_hotkey` around 2692) and `/api/settings` (around 5521). `Config.peek_hotkey` at `config.py:94`; `load_config` keeps only values whose type matches the default. UI: `templates/partials/settings_modal.html`, General pane, row `.peek-hotkey-group` with input `#peekHotkey` (saves on `change`, then `loadRestartKeys`); `index.html` `refreshSettings` (around 5786), `_RESTART_KEY_LABELS` (around 9282) and `markRestartInputs` owner map (around 9418).

**pywebview 6.2.1, WinForms + EdgeChromium** (installed; verified 2026-10-06 by `import webview.platforms.winforms as w; w.is_chromium` → True):
- `start()` must run on the main thread; the loop ends only when the last window instance closes (`webview/platforms/winforms.py`, `on_close`, around 372-398).
- `closing` is cancellable: a handler returning `False` sets `args.Cancel` (`winforms.py`, `on_closing`, around 400-403).
- `window.native` is the WinForms form. `show` and `hide` marshal to the UI thread themselves, and `show` always calls `Activate()` (`winforms.py` around 494-501). `resize` and `move` call `SetWindowPos` from the caller's thread and multiply by `GetDpiForWindow/96` (`winforms.py` around 600-643); the process is DPI-aware, so `GetSystemMetrics` and `Bounds` are physical pixels.
- `evaluate_js` waits up to 20 s for the page bridge and then on a semaphore with no timeout; called on the UI thread it deadlocks (`edgechromium.py` around 149-160).
- pynput's `win32_event_filter` runs synchronously inside the WH_KEYBOARD_LL hook procedure (`pynput/_util/win32.py` around 298-311 and 378-393; `pynput/keyboard/_win32.py` around 319); `suppress_event()` raises `SuppressException`, an `Exception` subclass.
- pywebview's `events.closing` handlers get no arguments (no `CloseReason`), and `Event.set` logs and swallows handler exceptions (`webview/event.py` around 36-66).
- Uvicorn runs at `log_level="warning"` (`__main__.py` around 814), so there is no access log; a successful `/local-auth` exchange logs only the INFO line `loopback browser signed in with a login code` (`web.py`, `/local-auth` handler).
- `target=_blank` and `window.open` already open the default browser: `edgechromium.py`, `on_new_window_request`, honours `OPEN_EXTERNAL_LINKS_IN_BROWSER`, default `True` (`webview/__init__.py` around 125).

**Runtime facts established by a throwaway probe on 2026-10-06** (script in the exploration session's scratchpad, not in the repo; re-run before relying on them after a pywebview upgrade):
- A second window can be created after `start()` from a non-main thread.
- A `closing` handler returning `False` keeps the window alive after a simulated X (`native.Close()` via `Invoke`).
- One window switched at runtime from `FormBorderStyle.Sizable`/`TopMost=False` to `FormBorderStyle.None`/`TopMost=True` and back without error; `hide()`/`show()` worked afterwards.
- Two pywebview windows in one process do **not** share cookies.

**pynput 1.8.2**: `GlobalHotKeys` cannot parse the repo's `ctrl+shift+z` format (verified: `HotKey.parse('ctrl+shift+z')` raises) and does no suppression, so the repo's own filter stays.

**Tests touching this area**: `tests/test_peek.py` (`TestCreatePeek`, `TestHotkeyStateMachine` whose `_make_peek` replaces `__init__` with a fixed attribute list, `TestHideCallsResetOverlays`, `TestPywebviewLoggerClamp`), `tests/test_tray.py` (icon tests only), `tests/test_config.py` (`peek_hotkey` load, wrong-type fallback, round trip), `tests/test_web.py` (save-setting around 615; settings payload `expected_keys` around 1602; restart-to-apply around 13495-13509; a source pin on `create_peek(server_url` and `run_tray, args=(server_url, config)` around 13822-13833), `tests/acp_page.test.mjs` (restart-key fixtures around 5509-5592).

**Prior art**: `plans/done/260630-1607_PEEK_WINDOW.md` records pywebview being removed once and rejected "for main UI, not overlay". That rejection predates the WebView2 backend this repo now runs in peek; this plan reuses the proven peek window rather than adding a second embedding. `plans/done/260707-1259_CONFIG_HOT_RELOAD_AND_PEEK_RESET.md` introduced `resetOverlays` on peek hide.

## 2) Goal

Make the existing peek pywebview window a single PowerAtlas window with a peek mode and an app mode, driven by one state machine shared by the hotkey listener and the tray, and add `peek_mode` and `browser_hotkey` settings with save-time validation.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| D-1 Window runtime | Keep pywebview (WebView2); no Electron | Electron; Trame | Exploration discussion: Electron adds a Node runtime, packaging and a second process for the same engine; Trame is a UI framework rewrite, not a window host. |
| D-2 Close behaviour (Q1) | X hides and keeps state; showing again restores it | X destroys, next open recreates and lands on `/` | User answer to Q1 (Option A). |
| D-3 One window or two (Q2) | One window with peek mode and app mode, switched at runtime through `window.native` | Second app window beside the peek window | User approved the merge at Q2. Probe showed runtime chrome switching works; one window keeps one cookie jar and never creates a window after start. |
| D-4 Shortcut set (Q3) | Peek shortcut; Peek mode Hold/Toggle; double-tap always toggles app mode; optional Browser shortcut, empty by default | Double-tap configurable browser/app; dedicated app shortcut | User answer to Q3. |
| D-5 Tray (Q4) | Open PowerAtlas (default, app mode), Open in browser, then existing items; fallback to browser when app mode is unavailable | Keep Open = browser and add Open app window | User answer to Q4. |
| D-6 Restart-to-apply | `peek_mode` and `browser_hotkey` join `_RESTART_TO_APPLY` | Live re-registration of the listener | Assumption accepted by the user at the exploration checkpoint ("Ok for me"); matches `peek_hotkey` (`web.py`, `_RESTART_TO_APPLY`). |
| D-7 Save validation | One pure validator module checks format and conflicts on save and at startup | Validate only at startup (today) | Accepted at the checkpoint; closes the gap at `/api/save-setting`'s string branch. |
| D-8 Startup visibility | Window starts hidden, including on autostart | Show app mode at launch | Accepted at the checkpoint. |
| D-9 App placement | Remembered in memory for the run as a Win32 `WINDOWPLACEMENT` (`GetWindowPlacement`/`SetWindowPlacement`), saved on every exit from APP; first app show uses 1280x800 logical pixels centered on the primary work area, clamped to it | `Bounds`/`RestoreBounds` plus a maximized flag; persist in config.toml | Accepted at the checkpoint (in-memory). `WINDOWPLACEMENT` round-trips minimized and maximized windows, where `Bounds` of a minimized form is the off-screen parking rectangle. Changed after plan review (findings A1, A8, S8, R7). |
| D-10 Taskbar | App mode in taskbar; peek mode off it | Always in taskbar | Accepted at the checkpoint. |
| D-11 `resetOverlays` | Runs only on PEEK → HIDDEN, not on PEEK → APP | Run on every peek end | Accepted at the checkpoint; the app mode keeps its state. |
| D-12 Platform | App mode Windows only (`supports_app_mode = sys.platform == "win32"`); elsewhere double-tap and tray Open use the browser | GTK chrome switching | Accepted at the checkpoint; WinForms is the only backend probed. |
| D-13 Signed-out detection (SC-9) | `web.py` keeps a process-local `_local_secret_generation` counter bumped in `set_local_secret`; the window records the generation it last signed in under (read before minting the creation URL) and reloads `_login_url` on any event that ends in PEEK or APP, including tray Open while already in APP, when it differs | Read the HttpOnly cookie with `window.get_cookies()`; inspect the page with `evaluate_js` | Rotation is the only in-run event that invalidates the window's cookie (90-day max age outlives any run). No page round-trip; `evaluate_js` can hang 20 s on an unloaded page. A rotation done inside the window causes one unneeded reload to `/`, accepted. |
| D-14 Focus test | "Focused" means `user32.GetForegroundWindow() == hwnd`, where `hwnd` is read from `native.Handle` inside `Invoke` after each chrome switch and cached as an int | `native.ContainsFocus` under `Invoke`; reading `native.Handle` from another thread | `Control.Handle` read off the UI thread during a handle recreation could create the HWND on the wrong thread. |
| D-20 Peek activation | Peek is shown without activating it; Esc is suppressed while a peek shows; on peek end the pre-peek foreground window and z-order are restored | `win.show()` (always activates) | Satisfies SC-3's "same focus state"; the chord and Esc are handled by the global hook, so the peek needs no focus. Behaviour change: typing during a peek goes to the user's app unless they click into the peek; a maximized APP returning from a peek is briefly activated (no non-activating maximized show) and the previous foreground is restored. Added after plan review (cycle 1: A7, S4, R6; cycle 2: Esc suppression). |
| D-21 Threading | Hook callbacks only update key state, suppress and enqueue; one worker thread owns all window state and calls; other threads enqueue; `stop()` never waits on the worker | Transitions on the listener thread under a shared lock | The filter runs inside the low-level hook; blocking there stalls input and can get the hook removed. Added after plan review (A3, A4, S6, R1-R3). |
| D-22 Close interception | Direct `native.FormClosing` handler; cancel only `CloseReason.UserClosing`; fail closed (cancel) on an exception unless stopping | `window.events.closing` returning `False` | pywebview's event has no close reason (would block Windows shutdown) and fails open on exceptions. Added after plan review (A2, S5, R4, R5). |
| D-15 Tray → window coupling | Replace `set_peek_stop_callback` with `set_window_controller(ctrl)`; the controller exposes `show_app()`, `stop()` and `supports_app_mode` | Tray imports `peek` | `peek.py` imports `tray._login_url`, so the reverse import is circular. |
| D-16 Names | Keep `peek.py`, `PeekWindow`, `create_peek` | Rename to `window.py`/`AtlasWindow` | Renaming churns every test and source pin for no behaviour change. The docstrings say it is the PowerAtlas window. |
| D-17 External links | No change: pywebview already opens `target=_blank` and `window.open` in the default browser | JS shim + js_api | `edgechromium.py`, `on_new_window_request`, `OPEN_EXTERNAL_LINKS_IN_BROWSER=True`. Phase 1 QA confirms it live. A same-origin PowerAtlas link opened this way lands on the browser's gate page (no cookie there); accepted, see Follow-up. |
| D-18 Chord matching with two chords | On a key-down, among chords whose keys are a subset of the pressed keys, fire the one with the most keys; tie → peek | Exact-match only | Exact match would change today's behaviour for users who hold an extra modifier. Conflicting (equal or nested) chords are refused at save and disabled at startup, so ties only arise from presses with extra keys. |
| D-19 `_AVAILABLE` | Stays one flag for pywebview and pynput | Split flags | App mode without a shortcut listener is still reachable from the tray, but a split adds a code path no install has needed; the tray fallback (SC-8) covers both missing. |

## 4) External Dependencies & Costs

### Required external changes

None. No new packages, services, CI, IAM or data migration. Config gains two keys with defaults; an older config.toml loads unchanged.

### Cost impact

None.

## 5) Implementation Phases

No phase is `[P:N]`: every phase edits `peek.py` or `web.py`, and all share one running instance for QA.

**Review cadence for `/qdev` (user override, 2026-10-06):** one `/qreview` cycle per phase (fix its findings, no re-review); only the Step 9 holistic review may run more than one cycle. Default overridden: `/qdev`'s per-phase review-and-fix loop of up to several cycles.

### Threading model (normative for Phases 1-3)
<!-- resolves cycle-1 findings 1-3, 14, 20, 21 and cycle-2 findings 1, 4, 5, 6, 9, 12 -->

- **The hook decides, the worker acts.** `_win32_event_filter` and `_on_press`/`_on_release` only: update `_pressed_keys` and per-chord `_triggered`; decide whether to suppress; build a small event tuple; `put` it on `self._events` (`queue.SimpleQueue`). They never call pywebview, `native`, `Invoke`, `load_url`, `evaluate_js`, a lock, or the browser.
- **The filter never lets an exception escape except the suppression.** pynput stops the whole listener on any other exception raised in the filter (`pynput/_util/__init__.py`, `_emitter`). The filter computes its decision inside `try/except Exception` (log the type name; on error: no suppression, no event) and calls `suppress_event()` **after** that `try`, outside any `try`, when the decision says so (`suppress_event()` raises pynput's `SuppressException`, an `Exception` subclass).
- **One worker thread owns the window.** `PeekWindow` starts a daemon thread `_window_worker` that loops on `self._events` and performs every transition and every pywebview/native call. All state (`_state`, `_return_to`, `_app_placement`, `_tap_origin`, `_last_press`, `_signed_gen`, `_prev_foreground`, `_hwnd`) is read and written only on that thread, with one exception: the worker publishes `_peek_showing` (a plain bool it alone writes) for the hook to read. Each event is handled inside `try/except Exception` that logs `type(e).__name__`, plus `str(e)` for errors from Win32/.NET calls on paths that never handle a URL.
- **UI-thread calls are bounded.** The worker runs native code with `native.BeginInvoke(...)` and waits on a completion event for up to 2 s; past that it logs a WARNING naming the operation and moves on (a hung UI thread must be visible in the log, not a silent stall).
- **Every other caller enqueues.** Tray `show_app()`, the `FormClosing` handler and the browser chord post events; none waits for the result.
- **`stop()` is idempotent and bounded.** The first call sets `_stopping`, stops the listener, posts a `stop` sentinel, and calls `self._window.destroy()` from a daemon thread joined for at most 5 s; later calls return at once. Guarantee: *tray Quit and Restart end the process (and Restart relaunches) within 15 s even if the UI thread hangs.* On a destroy timeout, post `WinForms.Application.Exit()` via `BeginInvoke`; if `webview.start()` has still not returned 5 s later, a watchdog thread runs the same shutdown tail `__main__` runs after `peek.start()` returns. Refactor that tail (after `peek.start(on_main_thread=True)`: stop server, join, restart check, remove PID, release mutex, `logging.shutdown`, relaunch if requested, `_exit_immediately`) into a function the watchdog can call, guarded so it runs once.
- **Nothing waits on the page from the UI thread.** No pywebview call that waits (`evaluate_js`, `get_cookies`, anything decorated `_loaded_call`/`_pywebview_ready_call`) runs inside an `Invoke`/`BeginInvoke` callable. `resetOverlays` runs as `native.webview.CoreWebView2.ExecuteScriptAsync("if(typeof resetOverlays==='function') resetOverlays()")` inside `BeginInvoke`, not awaited, skipped when `CoreWebView2` is `None`, wrapped in `try` (`native.webview` is the WebView2 control: `webview/platforms/winforms.py` around 280; pywebview itself calls `ExecuteScriptAsync` in `edgechromium.py` around 154).
- **Readiness gate.** pywebview starts the `start(func=...)` thread *before* it builds the form (`webview/__init__.py` around 294-303), so `_on_webview_ready` alone proves nothing. Readiness is reached on the worker after: `self._window.events.shown.wait(30)` (set by `on_shown`, which fires for a `hidden=True` window too, because `create_window` does `Show()`/`Hide()`), then, inside `BeginInvoke`, `native is not None` and `native.IsHandleCreated`; then the worker subscribes `FormClosing`, caches `_hwnd`, sets `_ready`, and logs INFO `PowerAtlas window ready` (the smoke signal; `Peek webview ready` is logged before the form exists and proves nothing). On timeout it logs a WARNING and leaves `_ready` unset, so app mode stays unavailable and tray Open uses the browser. Window events that arrive before `_ready` are dropped with a DEBUG line. Remove the unconditional `_webview_ok = True` on the main-thread path.
- **`supports_app_mode`** = `sys.platform == "win32"` and `self._ready.is_set()` and EdgeChromium in use (`webview.platforms.winforms.is_chromium`, imported lazily). A WebView2 initialization failure that pywebview only logs is out of scope (Follow-up 8).
- **Double-tap timing** uses the hook's own timestamp on Windows (`data.time`, a 32-bit millisecond tick from `KBDLLHOOKSTRUCT`, copied out during the call) and `time.monotonic()` elsewhere, carried in the event. Intervals on Windows are `(t2 - t1) & 0xFFFFFFFF` milliseconds (wraps every 49.7 days). After a double-tap `_last_press` resets to `None`, so a third tap is not another double-tap.
- **A key-down with that chord's `_triggered` already set is never an event** (auto-repeat), in either peek mode. `_triggered` resets on the chord's non-modifier key-up, on every platform (today `_on_release` resets it only on modifier release; extend it to the non-modifier key-up so a modifiers-held double-tap works off Windows too).
- **A key-down matches a chord only when the key is that chord's non-modifier key** and the chord's modifiers are held.
- **Esc while a peek is showing is suppressed.** Peek no longer takes focus (D-20), so an unsuppressed Esc would reach the user's foreground app (for example interrupting an agent turn in a terminal). When `_peek_showing` is true the filter suppresses Esc key-down and key-up and enqueues `esc`; otherwise Esc passes through untouched and is not an event. Off Windows (no suppression) Esc stays an `_on_press` event as today.

> **Rejected:** running transitions on the pynput listener thread (today's `_show`/`_hide`), or under a lock shared by the hook and the tray — the filter runs inside the WH_KEYBOARD_LL callback, and blocking there on `Invoke`, a lock, `load_url` or `evaluate_js` stalls system-wide input and lets Windows drop the hook silently. **Use instead:** the event queue and single worker above.

### The window state machine (normative for Phases 1-3)

States: **HIDDEN**; **PEEK** (carries `return_to`: `None`, or `"app"` meaning return to APP); **APP**. The worker also keeps `_app_placement`: the last saved app placement (Win32 `WINDOWPLACEMENT` from `GetWindowPlacement`, which carries the normal rectangle, the show command and the restore-to-maximized flag, so minimized and maximized windows round-trip), and `_prev_foreground`: the foreground HWND when the current peek began.

**Save rule.** Every exit from APP (to PEEK, to HIDDEN by X, to HIDDEN by double-tap) first writes `_app_placement` and records `app_was_foreground = (GetForegroundWindow() == _hwnd)`.

**Window primitives (Windows, inside `BeginInvoke`, Win32 only; never `win.show()`, `win.hide()`, `win.resize()`, `win.move()` or `native.TopMost`):**
- *show peek*: apply peek chrome (border `None`, out of the taskbar); if the window is iconic or maximized, `ShowWindow(SW_SHOWNOACTIVATE)` first to leave that state; then `SetWindowPos(_hwnd, HWND_TOPMOST, screen rect, SWP_NOACTIVATE | SWP_SHOWWINDOW)`; set `_peek_showing = True`.
- *hide*: `ShowWindow(SW_HIDE)`; `_peek_showing = False`.
- *apply app placement, not activating* (end peek → APP): apply app chrome (border `Sizable`, in the taskbar); `SetWindowPos(_hwnd, HWND_NOTOPMOST, 0,0,0,0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)`; `SetWindowPlacement` with `showCmd` mapped `SW_SHOWNORMAL → SW_SHOWNOACTIVATE`, `SW_SHOWMINIMIZED → SW_SHOWMINNOACTIVE`, `SW_SHOWMAXIMIZED` kept (it has no non-activating form; the foreground restore below repairs it); `_peek_showing = False`.
- *apply app placement, focused* (→ APP, focused): app chrome; `HWND_NOTOPMOST`; `SetWindowPlacement` with `showCmd` forced to `SW_SHOWNORMAL`, or `SW_SHOWMAXIMIZED` when the placement's restore-to-maximized flag is set; first use: `SetWindowPos` to the D-9 default rect (screen coordinates), then capture the placement (never write the default into `rcNormalPosition`, which is in workspace coordinates); then focus (Phase 1 focus property); `_peek_showing = False`.
- *restore previous foreground*: capture `pa_fg = (GetForegroundWindow() == _hwnd)` **before** hiding or re-placing; afterwards, if `pa_fg` and `_prev_foreground` is a live window (`IsWindow`) other than `_hwnd`, `SetForegroundWindow(_prev_foreground)`; and on end peek → APP with `app_was_foreground` false, `SetWindowPos(_hwnd, _prev_foreground, 0,0,0,0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)` so the window goes back below the user's app.

**`tap_origin`.** The worker records `tap_origin = (state, app_was_foreground)` when it handles the first press of a press pair, from the state before that press. The double-tap decision reads `tap_origin`, never the current state.

| Event | HIDDEN | PEEK (`return_to=None`) | PEEK (`return_to="app"`) | APP |
|---|---|---|---|---|
| Peek press, not a double-tap (Hold) | → PEEK(None) | no-op | no-op | save, → PEEK("app") |
| Peek press, not a double-tap (Toggle) | → PEEK(None) | end peek → HIDDEN | end peek → APP | save, → PEEK("app") |
| Second press within 0.5 s (both modes) | double-tap rule | double-tap rule | double-tap rule | double-tap rule |
| Modifier release (Hold) | no-op | end peek → HIDDEN | end peek → APP | no-op |
| Modifier release (Toggle) | no-op | no-op | no-op | no-op |
| Esc (suppressed while a peek shows) | n/a (passes through, no event) | end peek → HIDDEN | end peek → APP | n/a (passes through, no event) |
| User close (`FormClosing`, `UserClosing`) | n/a | same as Esc (only reachable after the user clicked into the peek) | same as Esc | save, → HIDDEN |
| Tray Open PowerAtlas | → APP, focused | → APP, focused | → APP, focused | → APP, focused (restores a minimized window, brings to front) |
| Tray Open in browser; browser chord | browser | browser | browser | browser |

**Double-tap rule**: if `tap_origin` is APP and was foreground → save, → HIDDEN; otherwise → APP, focused. It applies in every state, including HIDDEN: in Hold, pressing the whole chord, releasing everything and pressing again within 0.5 s is a double-tap (today's behaviour); in Toggle, two quick presses from PEEK(None) end the peek on the first and open APP on the second (a brief flicker, accepted rather than delaying every press by 0.5 s).

Definitions:
- **end peek → HIDDEN**: fire `resetOverlays` (no wait), *restore previous foreground* around *hide*.
- **end peek → APP**: *apply app placement, not activating*, then *restore previous foreground*. No `resetOverlays`.
- **→ APP, focused**: *apply app placement, focused*.
- **→ HIDDEN** from APP or by double-tap: *hide*, no `resetOverlays`.
- **save**: the Save rule.
- Every event that ends in PEEK or APP runs the sign-in check (D-13) first, including tray Open PowerAtlas while already in APP.

On a non-Windows platform there is no APP: the double-tap and tray Open open the browser; peek uses `show()` + `toggle_fullscreen()` as today; the rest of the table applies with PEEK(None) only.

### Phase 1: App mode, hold peek and tray [QA]
**Goal**: The single window gains the event worker, app mode, the hold-mode rows of the state machine, X-to-hide, the sign-in generation check and the new tray menu.
**File scope**: `src/power_atlas/peek.py`, `src/power_atlas/tray.py`, `src/power_atlas/__main__.py`, `src/power_atlas/web.py` (generation counter only), `tests/test_peek.py`, `tests/test_tray.py`, `tests/test_web.py`, `README.md`, `plans/tests/260701_POWERATLAS.md`
**Covers**: SC-1, SC-2, SC-3, SC-4, SC-8, SC-9, SC-10

Contract for `peek.py` (the Threading model and state machine above are normative; this list adds what they do not say):
- Public, never-raising methods for other threads: `show_app()` (tray; non-blocking), `stop()` (idempotent, bounded per the Threading model), property `supports_app_mode`. The worker's internal handlers are private.
- **Geometry and chrome.** All geometry is in physical pixels through Win32 or `native` inside `BeginInvoke` (the process is DPI-aware; pywebview's `resize`/`move` scale by DPI and are not used). Peek bounds: `Screen.PrimaryScreen.Bounds`. D-9 default: 1280x800 logical pixels times `GetDpiForWindow(_hwnd)/96`, centered in `Screen.PrimaryScreen.WorkingArea` and clamped to it.
- **HWND.** Read `native.Handle` only inside `BeginInvoke`, at readiness and after each chrome switch, and cache it in `_hwnd` (D-14).
- **Taskbar presence.** Today's code sets `native.ShowInTaskbar = False` once; toggling it recreates the form handle, and WinForms implements `ShowInTaskbar = False` with a hidden owner window [unverified]. Property: *taskbar presence follows the mode, the page survives 20 consecutive mode switches with its state intact, and a window that was foreground before a switch is still foreground after it.* Candidates: (a) toggle `ShowInTaskbar`; (b) keep `ShowInTaskbar = True` and toggle `WS_EX_TOOLWINDOW` (peek) / `WS_EX_APPWINDOW` (0x40000, app) with `SetWindowLongW(GWL_EXSTYLE)`, doing hide → restyle → show when the window is visible, plus `SWP_FRAMECHANGED`. Decide with a throwaway probe in the scratchpad (not the repo): load a page, set `window.__probe = 1` and record `location.href`; run 20 switches; after each assert `__probe` and `href` unchanged, the WebView2 child HWND visible with a non-zero rect, foreground retained, and taskbar presence: in APP the window has `WS_EX_APPWINDOW` or no owner (`GetWindow(h, GW_OWNER=4) == 0`), in PEEK neither. Also minimize the window in APP after a switch and assert it is iconic (not a desktop stub). Prefer (b) if both pass, since it keeps the handle.
- **Focus.** Property: *after a double-tap or tray Open, the PowerAtlas window is the foreground window and receives typed keys.* First try `native.Activate()` inside `BeginInvoke`. Fallback, in the same callable: `AttachThreadInput` to the foreground window's thread, `SetForegroundWindow(_hwnd)`, detach in a `finally`. If it still fails, record it under Follow-up and continue.
- **Closing.** Do not use pywebview's `events.closing`: it carries no close reason, and an exception in a handler is swallowed and lets the close through. At readiness, subscribe to `native.FormClosing` with a .NET handler (it runs after pywebview's own `on_closing`; WinForms reads `args.Cancel` after all handlers, so order does not matter) that: does nothing when `_stopping` is set; cancels (`args.Cancel = True`) and posts a `user_close` event when `args.CloseReason == CloseReason.UserClosing`; lets any other reason (`WindowsShutDown`, `TaskManagerClosing`, `ApplicationExitCall`) through so PowerAtlas never blocks logoff. The body is wrapped so an exception results in `Cancel = True` unless `_stopping` is set. It never touches state or calls pywebview. (`stop()` → `destroy()` arrives as `UserClosing`, hence the `_stopping` check.)
  > **Rejected:** `window.events.closing += handler` returning `False` — the handler cannot see `CloseReason`, so it would cancel Windows shutdown, and a raised exception makes pywebview let the close through, quitting PowerAtlas on X. **Use instead:** the direct `FormClosing` subscription above.
- **Sign-in check (D-13).** `_run_webview` reads `web.local_secret_generation()` **before** minting the creation URL and stores it in `_signed_gen`. The worker's check: `from .web import local_secret_generation` (lazy); if it differs, `win.load_url(_login_url(self._server_url))` (marshals itself; never from inside `BeginInvoke`) and update `_signed_gen`. Remove the per-show `load_url`.
  > **Rejected:** keeping today's `load_url` on every show, including to make an old test pass — it resets the page to `/`, breaking SC-1 and Q1. **Use instead:** the generation check; rewrite the tests listed below.
- `_visible`, `_show` and `_hide` are removed; `_state` replaces `_visible`; `_last_trigger_time` becomes the worker's `_last_press`.
- The source guard `test_the_doors_use_no_other_spelling` (`tests/test_web.py`) forbids `/local-auth`, `?code=` and loopback literals in `peek.py`, `tray.py` and `__main__.py`: all URLs come from `_login_url` and `server_url`.
- On non-Windows: `supports_app_mode` is False; the double-tap opens the browser through the same helper the tray uses.

Contract for `web.py`: module `_local_secret_generation = 0`, incremented inside `set_local_secret`, and `def local_secret_generation() -> int`. No route.

Contract for `tray.py`: replace `_peek_stop_callback`/`set_peek_stop_callback` with `_window_controller`/`set_window_controller(ctrl)`; Quit, Restart and `trigger_restart` call `ctrl.stop()` in the same try/except. Extract the menu into `_build_menu(server_url) -> pystray.Menu` so tests can inspect it without running the icon:
```python
pystray.Menu(
    pystray.MenuItem("Open PowerAtlas", on_open_app, default=True),
    pystray.MenuItem("Open in browser", on_open_browser, visible=lambda item: _app_mode_possible()),
    pystray.MenuItem("Copy login link", on_copy_login_link),
    pystray.MenuItem("Logs", on_logs),
    pystray.MenuItem("Restart", on_restart),
    pystray.MenuItem("Quit", on_quit),
)
```
Both open handlers keep today's `warmup_pinned` thread. `on_open_app` calls `ctrl.show_app()` when `_app_mode_available()` (controller set and `ctrl.supports_app_mode`, checked at click time), else opens the browser with a fresh login URL. The `Open in browser` visibility uses `_app_mode_possible()`: static facts only (win32, controller set, `peek.is_available()`), because pystray on Windows builds the menu once at setup, before the window is ready, and rebuilds it only after a menu callback.
  > **Rejected:** `visible=` depending on readiness — pystray evaluates it when the menu is built, which happens before readiness, so the item would stay hidden until the user clicked some other item. **Use instead:** static visibility; readiness decides routing at click time.

Contract for `__main__.py`: `set_window_controller(peek)` replaces `set_peek_stop_callback(peek.stop)`; update the import. Keep `create_peek(server_url, config.peek_hotkey)` (Phase 2 adds the mode argument). The `__main__` source pin in `tests/test_web.py` (`create_peek(server_url`, `run_tray, args=(server_url, config)`) stays valid.

Tests (existing files only). In `tests/test_peek.py`:
- Make the state machine testable without a window: the worker's transition logic calls a small window adapter (show peek, apply app placement, hide, focus, fire `resetOverlays`, sign-in reload). Tests pass a fake adapter that records calls and feed events straight into the worker's handler.
- One test per Hold and common cell of the state-machine table, plus: X after a resize in APP restores the newer placement; peek over a minimized APP returns to minimized; peek over a non-foreground APP restores the previous foreground and z-order; auto-repeat key-down is not an event; a double-tap decided from `tap_origin`; the Hold release-all double-tap from HIDDEN opens APP; a third tap after a double-tap is not a double-tap; tick wrap (`t2 < t1`) is handled; readiness: events before `_ready` are dropped and `show_app` falls back; `_ready` is not set when `native` is `None`.
- The hook layer: the filter enqueues and suppresses on a matching chord, raises pynput's suppression (assert it is not swallowed), and never calls the adapter.
- Closing handler: `UserClosing` cancels and posts `user_close`; `WindowsShutDown` does not cancel; `_stopping` lets everything through; an exception inside results in `Cancel = True`.
- Sign-in: generation unchanged → no reload on show; changed → exactly one reload.
- Rewrite or delete: `TestHotkeyStateMachine._make_peek` (patches `_show`/`_hide`, which no longer exist) and its five tests; `TestHideCallsResetOverlays` (`test_hide_calls_evaluate_js`, `test_hide_evaluate_js_exception_does_not_propagate`, `test_show_navigation_exception_does_not_propagate`, `test_double_tap_browser_exception_does_not_propagate`) — each replaced by the equivalent worker/adapter test above, never by restoring the old behaviour.

In `tests/test_web.py`, class `TestLoopbackDoors`:
- `test_tray_open`, `test_tray_open_goes_through_login_url`: use `_build_menu` and the labels "Open PowerAtlas" (no controller → browser) and "Open in browser".
- `test_the_login_link_is_never_logged`: keep it as the SC-10 guard and extend it to tray Open PowerAtlas fallback, Open in browser, the double-tap browser path (non-Windows branch), and the sign-in reload after a generation change; it must no longer read `load_url.call_args` from per-show navigation.
- `test_peek_double_tap`: double-tap posts an APP transition on Windows and opens the browser with a signed URL elsewhere.
- `test_peek_webview_at_creation`: the fake `create_window` result gains whatever attributes the new code touches; the creation URL is still signed.
- `test_peek_show_mints_a_fresh_code_every_time`: replaced by "a show without a rotation mints no code".
- `test_peek_survives_a_local_secret_rotation`: after `set_local_secret`, the next show reloads exactly once with a signed URL.
- Add: `set_local_secret` bumps `local_secret_generation()`.

In `tests/test_tray.py`: `_build_menu` labels and order, default item, `Open in browser` visible when the menu is built with a controller that is not yet ready (static visibility), hidden with no controller; `Open PowerAtlas` routes to the browser while the controller is not ready.

`__main__.py` also gains the shutdown-tail function the `stop()` watchdog calls (Threading model); `tests/test_web.py`'s `__main__` source pins stay valid.

New comments and commits name this plan by slug (`261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase N`); bare "Phase N" comments from other plans already exist in these files.

**Exit criteria**:
- [x] Every Hold-mode and common cell of the state-machine table, and each extra case listed above, has a passing test in `tests/test_peek.py`.
- [x] `TestLoopbackDoors` updated as listed; `test_the_login_link_is_never_logged` covers every new opener; `tests/test_tray.py` covers `_build_menu`.
- [x] `PYTHONPATH=src .venv-PowerAtlas/Scripts/python -m pytest tests/test_peek.py tests/test_tray.py tests/test_web.py tests/test_config.py --timeout=300` passes from the repo root.
- [x] Taskbar-switch probe run as specified and the chosen mechanism recorded in `## 9) Implementation Divergences from Plan`.
- [x] Unattended window probe passes (works while the session is locked), per `## 7) Verification`: covers show_app right after creation then X via `WM_SYSCOMMAND SC_CLOSE` (must hide, process alive; a bare `WM_CLOSE` arrives as `TaskManagerClosing` and closes the window by design, see section 9); PEEK from HIDDEN, from a normal APP, from a minimized APP and from a maximized APP (peek rect equals the screen each time, and the placement round-trips on end peek); `WS_CAPTION` in APP only, `WS_EX_TOPMOST` in PEEK only, taskbar presence per the Taskbar property; X hides without ending the process.
- [x] Smoke log line is `PowerAtlas window ready` (logged only after the readiness gate), not `Peek webview ready`.
- [x] A unit test feeds a filter whose matching helper raises: no exception escapes and nothing is suppressed; another asserts Esc is suppressed while `_peek_showing` and passes through otherwise; another asserts a second `stop()` does not call `destroy` again.
- [x] Smoke: PowerAtlas restarted; `orchestrator.log` shows `PowerAtlas window ready` and `Peek hotkey listener started` after the restart; `GET /api/settings` with the `pa_local` cookie returns 200.
- [ ] Live QA per `## 7) Verification` for SC-1 to SC-4 and SC-8, and a `target=_blank` link opening the default browser; each check is PASS, FAIL, or BLOCKED when the session is locked (BLOCKED items are listed for the user, never ticked as passed). Deferred to Phase 3 (session locked; see the list below).
- [x] Update `README.md` tray click line (around 52), doors list (around 60-65, dropping "signs in again each time it is shown") and tray description for Open PowerAtlas / Open in browser and app mode.
- [x] Update the `tray.py` `_login_url` docstring ("two doors") and the `plans/tests/260701_POWERATLAS.md` rows naming `set_peek_stop_callback`.

Implementation (2026-10-06, code: 7632be7)
Phase 1 turns the peek overlay into the single PowerAtlas window, with a peek mode and an app mode. In `peek.py` the keyboard hook now only records keys, decides suppression and posts events. The decision runs inside a `try`, and `suppress_event()` is called after it. Esc is suppressed only while a peek shows, and the hook's own timestamp is carried for the double-tap. One worker thread owns the window. It runs the Hold and common rows of the state machine through a small window adapter, which makes Win32 calls on the UI thread through a 2 s bounded `BeginInvoke`. The worker adds: a readiness gate that logs `PowerAtlas window ready`; app mode, with a `WINDOWPLACEMENT` saved on every exit from APP and the D-9 default size the first time; a non-activating peek that restores the previous foreground window and z-order; X-to-hide through a direct `FormClosing` handler that cancels only UserClosing and cancels on an exception; the D-13 sign-in check, which replaces the per-show login reload so the window keeps its page; an idempotent, bounded `stop()`, with a watchdog that runs `__main__`'s shutdown sequence, now moved into a function that runs once. Taskbar presence keeps ShowInTaskbar on and toggles WS_EX_TOOLWINDOW and WS_EX_APPWINDOW. The probe showed that toggling ShowInTaskbar recreates the handle and loses the WebView2 child. `web.py` gains `local_secret_generation()`, bumped by `set_local_secret`. `tray.py` gets **Open PowerAtlas** (the default: app mode, or the browser when app mode is unavailable) and **Open in browser** (shown on Windows only, decided from static facts). It also gets `_build_menu` for tests and `set_window_controller` in place of `set_peek_stop_callback`. The old `_show`/`_hide` tests and per-show navigation tests were rewritten as worker, adapter and hook tests, citing D-2, D-13 and D-21. README and the test-plan doc now describe the new tray items and the two window modes.

Implementation (2026-10-06, code: 0a73501)
Commit `0a73501` applies all 14 review fixes to Phase 1 in `src/power_atlas/peek.py`, `src/power_atlas/__main__.py`, `tests/test_peek.py` and `README.md`. State machine (fixes 1, 2, 4, 6): end peek → APP now runs the sign-in check first; after the non-activating re-place it reads the foreground again, so a maximized show that takes the foreground is handed back to the user's app and PowerAtlas goes back below it; any failed exit from PEEK does a best-effort hide, clears `_peek_showing` and lands in HIDDEN; a timed-out placement read or app show never replaces the saved placement and never advances the state. Hook (fixes 9, 12): an Esc key-up is suppressed exactly when its key-down was; the filter tolerates `stop()` clearing `_listener`. Win32 adapter (fixes 3, 5, 10): `restore_foreground` runs on the UI thread; the readiness check retries a busy UI thread until the deadline, logs a timeout as its own case, and subscribes `FormClosing` only once; `reload` is bounded by a thread join. Worker and shutdown (fixes 8, 11, 13): the worker drains pre-ready events before it sets `_ready`; `__main__` wraps the shutdown tail in `_run_once_or_wait`, so a second caller waits up to 15 s; off Windows, readiness no longer depends on `events.shown`. Changed return values and their consumers were each checked (`_establish_ready`, `_Win32Window.attach`, `_ui`, the HIDDEN landing on failure, `shutdown_tail`). Changed test expectations: the call-tuple assertion in `test_peek_over_a_background_app_restores_foreground_and_z_order` became an outcome assertion, as the finding asked; two worker fakes now return the adapter instead of setting `_ready`. No test expectation was edited to make failing code pass.

QA (Step 5b): BLOCKED for the live hotkey and focus checks (desktop locked; list above). The unattended window probe passed 35/35 after both commits; the tray menu was verified in-process. Review cadence: one review cycle (user override), fixes applied in `0a73501` without re-review.

The working tree held another session's unrelated edits (`docs/KNOWLEDGE.md`, `plans/ROADMAP.md`, `plans/CLOSED_INVESTIGATIONS.md`) during this phase; they were left untouched and that session committed them itself (`01ec95b`).

**Live QA pending the user (session locked, 2026-10-06).** Phase 1's live checks could not run unattended: the desktop locked before Step 5b (`OpenInputDesktop` failed). Run `qa_phase1.py` (kept with the session's scratch files; recipe in section 7) on an unlocked desktop to check: SC-1 hold shows a non-activated peek and release hides it; SC-2 double-tap opens a focused app window, and from a focused app window hides it; SC-3 peek over the app window returns to it with the same placement and focus; SC-4 X (`SC_CLOSE`) hides and the process stays alive; Esc during a peek dismisses it and does not reach the foreground app (and Ctrl+Shift+Esc does not open Task Manager); a `target=_blank` link in app mode opens the default browser. The tray menu structure was verified in-process (Open PowerAtlas default; Open in browser listed whenever a controller exists, before readiness).

### Phase 2: Peek mode setting (Hold / Toggle) [QA]
**Goal**: `peek_mode` exists end to end: config, save validation, settings UI, restart badge, and the Toggle rows of the state machine.
**File scope**: `src/power_atlas/config.py`, `src/power_atlas/web.py`, `src/power_atlas/peek.py`, `src/power_atlas/__main__.py`, `src/power_atlas/templates/partials/settings_modal.html`, `src/power_atlas/templates/index.html`, `README.md`, `tests/test_config.py`, `tests/test_web.py`, `tests/test_peek.py`, `tests/acp_page.test.mjs`
**Covers**: SC-5

- `Config.peek_mode: str = "hold"` next to `peek_hotkey`.
- `web.py`: `"peek_mode": str` in `_SETTING_TYPES`; add to `_RESTART_TO_APPLY` with a comment like the `peek_hotkey` one; write path normalises `value.strip().lower()` and refuses anything but `"hold"`/`"toggle"` with `"Peek mode must be hold or toggle"`; add `"peek_mode"` to the index page context and `/api/settings`.
- `create_peek(server_url, hotkey, mode="hold")`: an unknown mode logs a warning and uses `"hold"`. `__main__.py` passes `config.peek_mode`.
- `peek.py`: implement the Toggle rows of the state machine in the worker; modifier release is a no-op in Toggle; an auto-repeat key-down is never an event (Threading model).
- `settings_modal.html`: a `settings-row peek-mode-group` after the hotkey row with `<select id="peekMode">` (`hold` "Hold to show", `toggle` "Press to show, press again to hide"), saving on `change` with `.then(loadRestartKeys)` like `#peekHotkey`. Update the hotkey row description to: "Hold to peek at PowerAtlas from anywhere; double-tap to open it as a window. Takes effect on the next launch."
- `index.html`: `refreshSettings` sets `#peekMode`; `_RESTART_KEY_LABELS.peek_mode = 'Peek mode'`; `markRestartInputs` owners gains `peek_mode: 'peek-mode-group'`.

- Update the `_RESTART_TO_APPLY` comment that cites `create_peek(server_url, config.peek_hotkey)` to the new signature.

Tests: `tests/test_peek.py` `TestCreatePeek` (`test_invalid_hotkey_fallback`, `test_invalid_hotkey_only_modifier`, `test_valid_hotkey`): update every `mock_init` signature to accept the new arguments and stop setting `_visible`; add a test that `mode` is passed through and that an unknown mode falls back to `hold` with a warning. The docstring in `tests/test_web.py` that cites `create_peek(server_url, config.peek_hotkey)` (restart-to-apply test) is updated with the `web.py` comment. `tests/acp_page.test.mjs`: extend its hardcoded `hosts` map (`.peek-hotkey-group`/`.port-group`) with `.peek-mode-group`. `test_config.py` default, round trip and wrong-type fallback for `peek_mode`; `test_web.py` save accepts `hold`/`toggle` (and `" Toggle "` normalised), refuses `"x"`, payload `expected_keys` includes `peek_mode`, restart-to-apply includes it; `test_peek.py` every Toggle cell plus Toggle auto-repeat; `acp_page.test.mjs` asserts directly that `peek_mode` renders with the label "Peek mode" (not the raw key) and that a pending `peek_mode` puts the badge on `.peek-mode-group`.

**Exit criteria**:
- [x] Toggle-mode cells of the state-machine table and Toggle auto-repeat tested in `tests/test_peek.py`; pass.
- [x] `acp_page.test.mjs` asserts the `peek_mode` label and `.peek-mode-group` badge (a test that passes with the label entry deleted does not count).
- [x] Python suite command from Phase 1 passes; `node tests/acp_page.test.mjs` passes.
- [x] Unattended window probe from Phase 1 re-run with `mode="toggle"` events (press, press after 0.8 s, press-press within 0.5 s) and passes.
- [x] Smoke as in Phase 1.
- [ ] Live QA: settings modal shows the Peek mode select, saving shows the "on relaunch" badge; after restart with `toggle`, injected press/press hides and press/press-within-0.5 s opens app mode (or BLOCKED if locked); `peek_mode` restored to its previous value afterwards.
- [x] Update `README.md` config sample (around line 219) with `peek_mode` and the settings section description (around line 178).

Implementation (2026-10-06, code: 315bb5c)
Phase 2 adds the Peek mode setting from config to window, in commits `315bb5c` and `c889589`. `Config.peek_mode` defaults to `"hold"`. In `web.py`, `peek_mode` is in `_SETTING_TYPES`, in `_RESTART_TO_APPLY` (its comment now cites `create_peek(server_url, config.peek_hotkey, config.peek_mode)`), in the index page context and in `/api/settings`. `/api/save-setting` strips and lowercases the value and refuses anything but hold or toggle with "Peek mode must be hold or toggle". `create_peek(server_url, hotkey, mode="hold")` normalises the mode and falls back to hold with a warning; `__main__.py` passes `config.peek_mode`. In `peek.py`, `PeekWindow` keeps the mode, and the worker adds the Toggle rows of the state machine: a press while a peek shows ends it, back to HIDDEN or to APP per `return_to`; modifier release does nothing in Toggle; Esc and X still end a peek; the double-tap rule is unchanged, so two quick presses open app mode. Auto-repeat was already never an event, and new hook tests cover it under Toggle. The Settings dialog has a Peek mode select after the hotkey row. It saves on change and then calls `loadRestartKeys`, and the hotkey row has the plan's new description. `index.html` sets the select in `refreshSettings`, labels the key "Peek mode" and adds the `.peek-mode-group` badge owner. The README describes Toggle, the settings section and the config sample. Tests cover every Toggle cell and the double-tap rule under Toggle, the `create_peek` mode handling, the config default, round trip and wrong type, save accepts and refusals, the payload keys, restart-to-apply and pending, the rendered select, and a node check that the label and the badge are asserted directly. The plan file has 6 of 7 Phase 2 boxes ticked and is not staged. The unticked one is the injected-keystroke live QA, blocked because the desktop is locked and deferred to Phase 3.

Implementation (2026-10-06, code: 41836bf)
peek_mode is now normalised once, when config.toml is loaded. The value is stripped and lowercased, and anything other than hold or toggle becomes "hold". This means a hand-edited "TOGGLE" or "x" now shows the same mode in four places: the settings select (both the Jinja render and refreshSettings), /api/settings, the restart-pending snapshot, and create_peek at runtime. In the Settings dialog, the Peek mode option labels are now "Hold (show while held)" and "Toggle (press to show, press again to hide)", so the names used in the row description and the README appear in the UI. The Peek hotkey description no longer says "Hold to peek", which was wrong in Toggle mode. New tests cover the load-time normalisation (tests/test_config.py), the API and rendered-select view of a hand-edited value (tests/test_web.py), and refreshSettings applying peek_mode to #peekMode (tests/acp_page.test.mjs). The commit is 41836bf.

QA (Step 5b): unattended window probe in toggle mode PASS 36/36 (press, press after 800 ms hides; press-press within 200 ms from HIDDEN opens APP; release is a no-op; press ends a peek back to APP; normal, minimized and maximized placements round-trip; Esc and `SC_CLOSE` end a toggle peek; page state kept). Settings modal in Playwright PASS 10/10 (select, options, badge on `.peek-mode-group`, cleared on restore). Smoke PASS on three restarts (hold, toggle, hold), each logging the mode and `PowerAtlas window ready`. Live injected-key QA BLOCKED (desktop locked); deferred to Phase 3. The running instance was left on `peek_mode = "hold"`; config.toml now carries an explicit `peek_mode = "hold"` line. Each restart ended any active kiro-cli ACP sessions (restarts were granted for this task).

### Phase 3: Browser shortcut and shared shortcut validation [QA]
**Goal**: `browser_hotkey` exists end to end, and every shortcut save and startup goes through one validator with conflict checks.
**File scope**: `src/power_atlas/hotkeys.py` (new module), `src/power_atlas/peek.py`, `src/power_atlas/config.py`, `src/power_atlas/web.py`, `src/power_atlas/__main__.py`, `src/power_atlas/templates/partials/settings_modal.html`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css` (only if an error style is added), `README.md`, `plans/tests/260701_POWERATLAS.md`, `tests/test_peek.py`, `tests/test_web.py`, `tests/test_config.py`, `tests/acp_page.test.mjs`
**Covers**: SC-6, SC-7

- New pure module `hotkeys.py` (no pywebview/pynput imports, so `web.py` can import it):
  - `MODIFIERS = frozenset({"ctrl", "shift", "alt"})`.
  - `VK_NAMES: dict[int, str]`, the single source for Windows virtual-key names: today's `peek._vk_to_name` table (a-z, 0-9, f1-f24, named and punctuation keys) plus the keys pynput reports under the same names on other platforms and that today's non-suppressing path accepts: `up` 0x26, `down` 0x28, `left` 0x25, `right` 0x27, `insert` 0x2D, `pause` 0x13, `print_screen` 0x2C, `scroll_lock` 0x91, `num_lock` 0x90, `menu` 0x5D. `peek._vk_to_name` becomes a lookup in `VK_NAMES`.
  - `KEY_NAMES = frozenset(VK_NAMES.values()) - {"esc"}`: Esc is the peek dismiss key and is refused as a shortcut key.
  - Backward compatibility (behaviour change, accepted): a stored `peek_hotkey` that works today (a modifier and any non-modifier pynput reports) but fails `hotkey_error` now falls back to `ctrl+shift+z` at startup with a WARNING naming the rejected token. `VK_NAMES` covers the names pynput reports for ordinary keys, so this only affects exotic ones; the README lists the accepted key names.
  - `parse_hotkey(s) -> frozenset[str]` (same splitting and lowercasing as `PeekWindow._parse_hotkey`).
  - `hotkey_error(s) -> str | None`: `None` when valid; valid means at least one modifier, at least one non-modifier, and every token in `MODIFIERS | KEY_NAMES`. Error strings name the problem ("needs a modifier", "unknown key 'foo'").
  - `hotkeys_conflict(a, b) -> bool`: true when the parsed sets are equal or one is a proper subset of the other.
- `web.py` write path: `peek_hotkey` normalised to `strip().lower()`, refused on `hotkey_error` or on conflict with the stored `browser_hotkey`; `browser_hotkey` normalised the same, `""` accepted (off), otherwise refused on `hotkey_error` or conflict with the stored `peek_hotkey`. Error text: `"Shortcut <problem>"` / `"Conflicts with the peek shortcut"` / `"Conflicts with the browser shortcut"`. `browser_hotkey` joins `_SETTING_TYPES`, `_RESTART_TO_APPLY`, both payloads.
  > **Rejected:** validating only in the browser — `/api/save-setting` is the single write path and the current gap. **Use instead:** server-side validation; the UI only displays the returned `error`.
- `Config.browser_hotkey: str = ""`.
- `create_peek(server_url, hotkey, mode="hold", browser_hotkey="")`: peek hotkey invalid → warn and fall back to `ctrl+shift+z` (today); browser hotkey invalid or conflicting → warn and disable it. `__main__.py` passes `config.browser_hotkey`.
- `peek.py`: generalise `_trigger_keys` to a chord table (`{"peek": frozenset, "browser": frozenset | None}`) with one matching function used by both `_win32_event_filter` (Windows) and `_on_press` (other platforms), per the Threading model's matching rule and D-18; per-chord `_triggered`. The browser chord enqueues a `browser` event; the worker opens the browser with a fresh login URL through the tray's helper and leaves the window state unchanged. The modifier-release event stays tied to the peek chord's modifiers.
- UI: `settings_modal.html` row `browser-hotkey-group` with input `#browserHotkey` (placeholder "Off"), same save pattern; both shortcut inputs show the response's `error` inline in an element inside their row and clear it on success. On a refusal the input reverts to the stored value (kept in a `data-saved` attribute set on render and on each successful save). Reuse an existing inline-error class if `style.css` has one (grep `error` in `.settings-row` rules first); a new class that sets `display` also gets a `[hidden]` rule (`AGENTS.md`). `index.html`: `refreshSettings` sets `#browserHotkey`; label `browser_hotkey: 'Browser shortcut'`; owners `browser_hotkey: 'browser-hotkey-group'`.

Tests: `hotkeys.py` behaviour tested inside `tests/test_peek.py` (a new class there, no new file), including `esc` refused and every `VK_NAMES` value accepted; `test_the_login_link_is_never_logged` (`tests/test_web.py`) extended to the browser chord; `test_web.py` save refusals (bad format, equal, nested, empty browser accepted), payload keys; `test_config.py` `browser_hotkey` default and round trip; `test_peek.py` two-chord matching per D-18 and startup disabling of a conflicting browser chord; `TestCreatePeek` asserts `browser_hotkey` pass-through and that an invalid or conflicting one is disabled with a warning; `acp_page.test.mjs` extends its `hosts` map with `.browser-hotkey-group` and asserts the "Browser shortcut" label and badge directly, and the inline error and revert if the harness can drive the modal's `onchange` (record in Divergences if it cannot).

**Exit criteria**:
- [x] Python suite and `node tests/acp_page.test.mjs` pass.
- [x] `POST /api/save-setting` refuses `peek_hotkey="z"`, `browser_hotkey="ctrl+shift+z"` (equal to peek) and `browser_hotkey="ctrl+shift+alt+z"` (contains peek), and accepts `browser_hotkey=""` (live, with the cookie, against a throwaway config value restored afterwards).
- [x] Smoke as in Phase 1.
- [ ] Live QA: with a browser shortcut set and PowerAtlas restarted, injecting it opens a browser tab signed in: the count of `loopback browser signed in with a login code` lines in `orchestrator.log` rises by one (or BLOCKED if locked). Restore the user's original shortcut values afterwards.
- [x] Update `README.md` config sample and settings description with `browser_hotkey` and the validation rule.
- [x] Update `plans/tests/260701_POWERATLAS.md` "validation only at peek startup" row and its settings allowlist (`peek_mode`, `browser_hotkey`).
- [ ] (deferred from Phase 2) Live QA of Phase 2's Toggle mode on an unlocked desktop: after a restart with `toggle`, injected press then press after 0.8 s hides, and press-press within 0.5 s opens app mode; restore `peek_mode` afterwards.
- [ ] (deferred from Phase 1) Live QA of Phase 1's SC-1 to SC-4, SC-8, Esc and `target=_blank` checks on an unlocked desktop, using `qa_phase1.py` per `## 7) Verification`; BLOCKED again if the session is still locked.

Implementation (2026-10-06, code: a8d9c12)
Phase 3 is done, and the code is committed as `a8d9c12`. It adds an optional browser shortcut and checks both shortcuts with a single validator, both on save and at startup. New module `src/power_atlas/hotkeys.py` is pure (no pywebview or pynput import) and holds the key-name table (one source of truth, with the 10 added keys checked against pynput 1.8.2), parsing and validation with Esc refused as a shortcut key, the conflict test (equal or one contains the other), and the D-18 chord matcher (most keys wins, a tie goes to peek). In `peek.py` the listener matches against a table of two chords with per-chord auto-repeat and key-up tracking; the Windows filter and the non-Windows path use the same matcher. The browser chord queues a `browser` event; the worker opens a signed-in tab and changes neither the window state nor the double-tap timing, and handles this event even before the window is ready. Modifier release still ends a Hold peek only for the peek chord's own modifiers. At startup `create_peek` falls back to `ctrl+shift+z` for an invalid peek shortcut and turns off a browser shortcut that is invalid or conflicting, with a warning in each case. `Config.browser_hotkey` defaults to `""` (off); `web.py` adds it to `_SETTING_TYPES`, `_RESTART_TO_APPLY` and both payloads; `/api/save-setting` refuses a bad format ("Shortcut …") and a shortcut that overlaps the other one as it actually runs; `__main__.py` passes the value to `create_peek`. The Settings dialog has a Browser shortcut row; both shortcut fields save through `saveShortcut` in `index.html`, which shows the server's error under the field and restores the stored value from `data-saved` on a refusal. README and `plans/tests/260701_POWERATLAS.md` describe the setting and the validation rule.

Implementation (2026-10-06, code: f8e6863)
Commit f8e6863 applies all ten Phase 3 review fixes. The main fix is to the Windows keyboard filter. Once the filter has suppressed a chord key's key-down, it now treats every further key-down of that key as a repeat: suppressed, with no event, until the key's key-up. Each key's down/up pair therefore stays whole whatever order the modifiers are released in, and a held key can no longer switch to another chord. If the key-up is lost, a gap over 1.5 s counts as a new press, so the key cannot stay swallowed. A key-up now re-arms every chord that contains the key. The constructor turns off an invalid or overlapping browser chord, and a shortcut must have exactly one key besides the modifiers. Saving the peek shortcut now uses the same rule as startup to decide whether the stored browser shortcut is in force. The unreachable `browser` branch in `_handle` is gone, and the login-link log guard now runs through the real worker loop. Stale shortcut errors are cleared when the settings refresh and when the dialog reopens. Config warnings quote raw values with `%r`. The four required test files and the full suite pass, the node suite passes, and the mutation checks confirm the new tests fail without the fixes. PowerAtlas was restarted once, and the log shows the hotkey listener started and the window ready.

QA (Step 5b): live save refusals against the running instance with the `pa_local` cookie (implementer, before `a8d9c12` was committed, on the same tree): `peek_hotkey="z"` → `{ok:false, "Shortcut needs a modifier (ctrl, shift or alt)"}`; `browser_hotkey="ctrl+shift+z"` → `{ok:false, "Conflicts with the peek shortcut"}`; `browser_hotkey="ctrl+shift+alt+z"` → `{ok:false, "Conflicts with the peek shortcut"}`; `browser_hotkey=""` → `{ok:true, restart_required:true}`; afterwards `restart_pending` was empty and the values equalled the originals (config.toml now carries an explicit `browser_hotkey = ""`). Smoke PASS before and after `f8e6863` (listener line with `browser shortcut: off`, then `PowerAtlas window ready`). Unattended window probe re-run by the orchestrator on `a8d9c12`: PASS 35/35. Injected browser-shortcut live check BLOCKED (desktop locked); recipe: set `browser_hotkey` (for example `ctrl+alt+b`), restart, inject it with `pynput.keyboard.Controller`, check that the count of `loopback browser signed in with a login code` lines rises by one, then set it back to `""`.

### Phase 4: Documentation and stale comments
**Goal**: Docs and comments describe the merged window.
**File scope**: `README.md`, `src/power_atlas/web.py` (comments only), `src/power_atlas/__main__.py` (docstring only), `src/power_atlas/peek.py` (module docstring and the comment above `from .tray import _login_url`)

- `web.py` comment above `_LOGIN_CODE_MAX_OUTSTANDING` ("Three doors plus Copy login link") → name the current openers (tray Open PowerAtlas, Open in browser, browser shortcut, the window at creation and after a rotation, Copy login link).
- `__main__.py` `_server_url` docstring ("tray, peek") → current openers.
- `peek.py` module docstring → "The PowerAtlas window: one pywebview window with peek mode and app mode, and its global shortcut listener."
- `README.md` Linux note (around line 295): app mode is Windows only; on Linux the double-tap and tray Open use the browser.
- Grep `README.md` for "peek" and "Open" once more and fix anything the earlier phases missed.

**Exit criteria**:
- [x] `grep -n -i "doors\|on every show" src/power_atlas/*.py` shows only wording that matches the shipped openers (each remaining hit read and judged).
- [x] Every Phase 4 row of `## 8) Documentation Updates` applied.
- [x] README Linux note, doors list, tray list and config sample all match the shipped behaviour (cold read: a fresh reader can say what a double-tap does on Windows and on Linux).
- [x] Python suite passes (comment-only edits, run as a guard).

Implementation (2026-10-06, code: 14d1c2c)
The docs and comments now name the doors that mint a login code, as the code ships them: tray **Open in browser**; tray **Open PowerAtlas**, when it falls back to the browser; the browser shortcut; the double-tap's browser fallback off Windows; the PowerAtlas window, at creation and after a local-secret rotation; **Copy login link**. This list replaces the old "tray, peek double-tap, peek webview" wording in four places in `src/power_atlas/web.py`: the `_LOGIN_CODE_MAX_OUTSTANDING` comment, the `pa_local` max-age comment, the `mint_login_code` docstring and the `login_url` docstring. `login_url` no longer claims a fresh code on every show. I checked that Copy login link goes through `tray._login_url` and then `web.login_url`, so "every door calls this through `login_url`" is true. Other edits: `src/power_atlas/__main__.py`: the `_server_url` docstring lists the same doors. `src/power_atlas/peek.py`: the module docstring describes the one pywebview window with peek mode and app mode (Windows only), plus its shortcut listener; the comment above `from .tray import _login_url` lists the window and browser doors. `README.md`: the Linux note says app mode is Windows only, and on Linux the double-tap and tray **Open PowerAtlas** open a signed-in browser with no **Open in browser** item; the tray intro says the icon click opens the browser where app mode is unavailable and that **Open in browser** is listed only where app mode is available; the fallback sentence now reads "pywebview or pynput missing, or not Windows"; the `peek_mode` sample comment says a double-tap opens the browser off Windows. A cold reader can now tell that on Windows a double-tap opens or hides the app window, and on Linux it opens the browser. No code changed. Commits: 521d8b6 and 14d1c2c.

Per-phase review deferred to Step 9: comment, docstring and README edits only across four files (no executable code; verified with `git diff c148d83..14d1c2c -- src`), and Step 9's holistic review covers documentation completeness.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| R1 Overlapping shortcuts fire two actions | Wrong window action | Conflict refusal on save and disabling at startup (Phase 3, D-18). |
| R2 Dead cookie after a rotation now that shows keep state | Window stuck on the gate page | D-13 generation check on every event ending in PEEK or APP (Phase 1). |
| R3 App placement lost or off screen | App window returns at the wrong size or off screen | `WINDOWPLACEMENT` saved on every exit from APP (D-9). |
| R4 Windows refuses foreground on double-tap | Window flashes in the taskbar instead of focusing | Focus property and fallbacks in Phase 1; deferred to Follow-up if unmet. |
| R5 The keyboard hook blocks or raises | System input stalls; Windows drops the hook; shortcuts die silently | D-21: the hook only enqueues; `suppress_event` outside any `try`; worker handles exceptions. |
| R6 Close interception blocks shutdown or fails open | PowerAtlas blocks logoff, or X quits the app | D-22: cancel only `UserClosing`; fail closed on an exception; `_stopping` lets the final close through. |
| R7 Old tests assert removed behaviour | An implementer restores the per-show reload to make them pass | Phase 1 lists every affected test and its replacement; the rejected-fix line forbids restoring the reload. |
| R8 Taskbar toggling recreates the form handle | Page state or rendering lost after switches | Phase 1 probe with a page marker and child-HWND check before choosing. |
| R9 Unattended overnight run breaks startup | User wakes to a dead PowerAtlas | Smoke exit criterion on every Python phase; `/qdev` fixes forward with new commits before moving on. |
| R10 Session locked overnight | Live hotkey and focus QA cannot run | The unattended window probe covers chrome, placement and X without input; hotkey and focus checks are BLOCKED for the user, never ticked. |
| R11 QA side effects on the user's live sessions | Signed-out browsers, closed ACP sockets, changed settings | No secret rotation during unattended QA (unit tests cover SC-9; the live rotation check is left for the user); settings changed for QA are restored. |
| R12 Narrowed shortcut key names | An existing exotic `peek_hotkey` stops working | `VK_NAMES` extended to the names pynput accepts today; anything else keeps today's fallback with a warning and a README note. |

## 7) Verification

Automated, every phase, from the repo root (not a worktree):
- `PYTHONPATH=src .venv-PowerAtlas/Scripts/python -m pytest tests/test_peek.py tests/test_tray.py tests/test_web.py tests/test_config.py --timeout=300`
- `node tests/acp_page.test.mjs` when a template changed.
- Pre-commit hooks (`_check_test_names.py`, `_check_public_ids.py`) run on commit.

Restart: granted by the user in the turn that invoked `/qplan` on 2026-10-06 ("You can restart PowerAtlas as much as you want"), for this task only. Use `POST /api/restart` with the `pa_local` cookie and a matching `Origin`, or tray Restart; then wait for `Peek hotkey listener started` in `%LOCALAPPDATA%\power-atlas\orchestrator.log`.

Unattended window probe (works while the session is locked; required in Phases 1-3): a throwaway script in the scratchpad that creates its own `PeekWindow` against a local static page served by the script, with `PeekWindow._start_listener` patched to a no-op (no second global hook) and `peek._login_url` patched to return the static URL (no mint, no secret needed). It feeds worker events and calls `show_app()` directly, reads `_hwnd` in-process, and asserts the window with ctypes after each step. Rules for every ctypes check, here and in live QA:
- Call `SetProcessDPIAware()` in the checking process before comparing rects with `GetSystemMetrics`.
- In live QA (another process), find the window by enumerating top-level windows (`EnumWindows` + `GetWindowThreadProcessId`) and keeping the one whose process is PowerAtlas, whose class starts with `WindowsForms10.Window` and whose title is `PowerAtlas` (pystray and a WinForms owner window may also exist). Re-find it after every action, since a handle may be recreated.
- Poll each assertion for up to 3 s (the worker is asynchronous).
- Assertions: `IsWindowVisible`; `GetWindowLongW(h, GWL_STYLE) & 0x00C00000` (WS_CAPTION: framed vs frameless); `GetWindowLongW(h, GWL_EXSTYLE) & 0x8` (WS_EX_TOPMOST); `GetWindowRect` vs `GetSystemMetrics(0/1)`; `GetWindowPlacement` round-trip; X by `PostMessageW(h, 0x0112, 0xF060, 0)` (WM_SYSCOMMAND SC_CLOSE, what the X button and Alt+F4 send; a bare posted `WM_CLOSE` arrives as `TaskManagerClosing` and really closes the window, measured in Phase 1) then not visible and the process alive.

Live QA against the running instance (needs an unlocked desktop):
1. Locked-session check first: `user32.GetForegroundWindow() == 0`, or `OpenInputDesktop(0, False, 0x0100)` failing, means locked; record **BLOCKED** with this recipe for the user, never FAIL and never PASS.
2. Drive shortcuts from a separate process with `pynput.keyboard.Controller`: press modifiers, tap the key once or twice (0.1 s between taps for a double-tap, 0.8 s for two singles; at least 50 ms between any two injected events), release modifiers. The repo's filter does not ignore injected events.
3. Assert with the ctypes rules above, plus `GetForegroundWindow() == h` for focus and, for SC-3, that the pre-peek foreground window is foreground again after the peek ends.
4. Page state kept: count `loopback browser signed in with a login code` lines in `orchestrator.log` before and after a hide/show cycle; without a rotation the count must not change. (A positive control: the browser shortcut or tray Open in browser raises the count by one.)
5. Sign-in refresh after a rotation is **not** run unattended (it signs out the user's other browsers); unit tests cover it, and the step is listed for the user.

Browser-side checks (settings modal, badges, inline errors): Playwright from the venv with the `pa_local` cookie, per `AGENTS.md § Verification Setup`. Restore every setting changed for QA to its previous value.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Tray click line (around 52, "Click to open the dashboard UI"), doors list (around 60-65, including the now-false "signs in again each time it is shown"), tray items, app mode and double-tap behaviour | 1 |
| `src/power_atlas/tray.py` | `_login_url` docstring "its two doors" | 1 |
| `plans/tests/260701_POWERATLAS.md` | Test-plan rows naming `set_peek_stop_callback`/`create_peek` (around 573-579) | 1 |
| `plans/tests/260701_POWERATLAS.md` | "validation only at peek startup" row (around 315-318) and settings allowlist (around 330) | 3 |
| `src/power_atlas/web.py` | Comment citing `create_peek(server_url, config.peek_hotkey)` in `_RESTART_TO_APPLY` | 2 |
| `README.md` | Settings description (around 178) and config sample (around 219): `peek_mode` | 2 |
| `README.md` | Config sample and settings: `browser_hotkey`, validation rule | 3 |
| `README.md` | Linux note (around 295): app mode Windows only | 4 |
| `src/power_atlas/web.py` | "Three doors" comment above `_LOGIN_CODE_MAX_OUTSTANDING`; the doors wording near the `pa_local` cookie comment ("The doors mint a fresh code"); `mint_login_code` docstring door list; `login_url` docstring ("the peek webview at creation and on every show") | 4 |
| `src/power_atlas/peek.py` | Comment above `from .tray import _login_url` ("Both peek doors — the webview and the double-tap browser") | 4 |
| `AGENTS.md` | `## Terminology` "login code" entry lists "(tray, peek double-tap, peek webview)" | doc-table-only (governance file; needs user approval; see Follow-up) |
| `memory/MEMORY.md` | Entry on driving the tray and peek doors for live QA (double-tap no longer yields a browser login) | doc-table-only (memory; needs user approval; see Follow-up) |
| `src/power_atlas/__main__.py` | `_server_url` docstring door list | 4 |
| `src/power_atlas/peek.py` | Module docstring | 4 |
| `src/power_atlas/templates/partials/settings_modal.html` | Peek hotkey row description; new rows | 2, 3 |
| `AGENTS.md` | Terminology entries and a hotkey-injection QA recipe | doc-table-only (needs user approval; see Follow-up) |

## 9) Implementation Divergences from Plan

Phase 1 (code `7632be7`):
- **Taskbar mechanism: candidate (b).** `ShowInTaskbar` stays True; `WS_EX_TOOLWINDOW` (peek) and `WS_EX_APPWINDOW` (app) are toggled with `SetWindowLongW`, the window hidden around the restyle, then `SWP_FRAMECHANGED`. Probe 2026-10-06, 20 switches each: (a) toggling `ShowInTaskbar` recreated the handle on 19 of 20 switches and lost the visible WebView2 child on every peek switch; (b) kept the handle, `window.__probe` and `location.href` on all 20, and a minimized APP parks at -32000 (taskbar, not a desktop stub). Taskbar presence was checked through style bits (in taskbar = `WS_EX_APPWINDOW`, or no owner and not `WS_EX_TOOLWINDOW`), not the taskbar UI.
- **X simulation in QA is `WM_SYSCOMMAND SC_CLOSE`, not `WM_CLOSE`.** Measured: a bare posted `WM_CLOSE` reaches WinForms as `CloseReason.TaskManagerClosing`, which D-22 lets through, so it closes the window and quits PowerAtlas. The X button and Alt+F4 send `SC_CLOSE` (`UserClosing`), which is cancelled. Consequence for users: Task Manager's End task and tools such as AutoHotkey `WinClose` quit PowerAtlas (README notes it).
- **End peek → APP when the app was foreground re-focuses (activating).** Mechanism (b) hides the window around the restyle, which hands the foreground elsewhere; the restore rule cannot return it because `_prev_foreground` is the window itself. Re-focusing preserves SC-3's "same focus state".
- **`create_window(on_top=False)`.** Z-order is set only with `SetWindowPos(HWND_TOPMOST/HWND_NOTOPMOST)`; the WinForms `TopMost` property is reapplied on style updates and its setter may activate.
- **`show_app()` falls back to the browser itself** when app mode is unavailable, in addition to the tray's click-time check.
- **`_signed_gen` is written on the main thread** in `_run_webview`, before the creation mint, rather than by the worker (the form does not exist yet at that point).
- **The worker checks the adapter's `has_app_mode`** rather than `supports_app_mode`, keeping the worker testable without WinForms; on Windows the Win32 adapter exists only after readiness, so the two agree.
- **`Peek webview ready` and the `start(func=...)` callback were removed**; the readiness signal is `PowerAtlas window ready`.

Phase 1 review fixes (code `0a73501`):
- **A failed exit from PEEK lands in HIDDEN** (a transition the state machine does not have): any raising re-place, hide or `resetOverlays` in end peek, or a timed-out app show from PEEK, does a best-effort hide, clears `_peek_showing`, sets HIDDEN and re-raises. Staying in PEEK would keep Esc swallowed system-wide.
- **Esc key-up follows its key-down**, not `_peek_showing`: a key-up is suppressed exactly when its key-down was, so an Esc pressed before a peek showed passes its key-up through. Known edge: a lost key-up (secure-desktop switch) makes the next Esc key-down count as a repeat once.
- **Off Windows, a missing `shown` event logs a WARNING and readiness still completes** (`_PortableWindow` needs no form handle). On GTK, pywebview 6.2.1 fires `shown` for hidden windows (`gtk.py` around 205, 369-373, 487-494), so this guards other backends only.
- **A timed-out Win32 reload logs a WARNING and does not retry**; `_signed_gen` advances and the posted `load_url` runs when the UI thread recovers. `_PortableWindow.reload` keeps the synchronous call.
- **A timed-out app show** from HIDDEN or APP changes neither state nor placement; from PEEK it falls back to HIDDEN (rule above); inside end peek → APP the state still advances, since the posted re-place normally runs late.

Phase 2 (code `315bb5c`, `c889589`, `41836bf`):
- **`create_peek` normalises the mode with strip/lower**, and since `41836bf` `load_config` normalises `peek_mode` too (unknown → `hold`), so disk, settings UI, restart snapshot and runtime agree. `create_peek`'s "invalid mode" warning therefore no longer fires for a hand-edited value; `load_config` logs nothing because it is uncached and called often.
- **The Peek mode select reuses the `port-mode` CSS class** (no `style.css` change; `style.css` was outside Phase 2's scope).
- **The listener start log line names the mode**: `Peek hotkey listener started (hotkey: ..., mode: ...)`; the smoke grep still matches.
- **Toggle is handled in the worker only**: the hook still posts `release`, and the worker ignores it in Toggle (D-21's single owner).
- **Extra comment-fix commit `c889589`** (startup-snapshot comment counts) because amending is banned; two stale template comments updated.
- **Settings copy differs from the plan's verbatim text**: option labels "Hold (show while held)" and "Toggle (press to show, press again to hide)"; the hotkey row reads "Shows PowerAtlas from anywhere (see Peek mode); double-tap to open it as a window. Takes effect on the next launch." The plan's "Hold to peek…" was wrong in Toggle mode (review finding).

Phase 3 (code `a8d9c12`, `f8e6863`):
- **The browser event is handled before readiness** (in the pre-ready drain and the main loop): it needs no window. The plan's readiness gate drops other window events only.
- **Save-time conflict checks compare against the other shortcut as it runs**: an invalid stored peek shortcut runs as `ctrl+shift+z`; a stored browser shortcut that is invalid, or overlaps the effective peek shortcut, is off (`hotkeys.effective_peek_hotkey`, `effective_browser_hotkey`). `create_peek` and the constructor apply the same rule.
- **A shortcut has exactly one key besides the modifiers** (`ctrl+a+b` is now refused; the plan's validator allowed several). Startup falls back with a WARNING for a stored value that fails.
- **Held-key repeat rule**: a key-down for a chord key already held counts as a repeat (suppressed, no event) if it comes within 1.5 s of that key's previous key-down; a longer gap is a new press, so a lost key-up cannot swallow the key permanently.
- **`_triggered` is a per-chord dict**; four test assertions moved from `pw._triggered is False` to `pw._triggered["peek"] is False` (same expected value).
- **Error wording**: "is empty", "cannot use esc, which dismisses the peek", "has an unknown key 'foo'", "needs a modifier (ctrl, shift or alt)", "needs a key besides the modifiers", "has more than one key besides the modifiers", each prefixed "Shortcut ".
- **The listener log line adds `browser shortcut: <value or off>`.**
- **Inline errors reuse `.pa-modal-field-error`** (already has a `[hidden]` rule); no `style.css` change. Errors are also cleared when the dialog reopens.

Phase 4 (code `521d8b6`, `14d1c2c`):
- **Two commits**: the first called the doors "openers"; the fixup restores the project's term "door" (`TestLoopbackDoors`, the `tray.py` door list). No amend.
- **Edits beyond the listed rows**: README tray intro (icon click opens the browser where app mode is unavailable; Open in browser listed only where app mode is available), the fallback sentence ("pywebview or pynput missing, or not Windows"), and the `peek_mode` sample comment ("the browser off Windows"). The `peek.py` module docstring is longer than the plan's one-liner.

## Follow-up Work (Deferred)

1. **Terminology proposal for `AGENTS.md`.** Proposed entries: **PowerAtlas window** (the single pywebview window; not "peek window" for the whole, not a separate "app window"), **peek mode** and **app mode** (its two presentations). Needs the user's Save / Skip / Edit; the user was away during planning.
2. **Stale opener lists outside the code.** `AGENTS.md § Terminology` ("login code" entry) and the `memory/MEMORY.md` entry on driving the tray and peek doors both describe the double-tap as opening a browser. Propose edits to the user; both need approval.
3. **QA recipe for `AGENTS.md § Verification Setup`.** The pynput-injection plus ctypes window checks in section 7, once proven by `/qdev`. Needs user approval (governance file).
4. **R4 focus fallback**, if the Phase 1 focus property cannot be met.
5. **Browser shortcuts inside the window.** pywebview runs with `debug=False`, which turns off WebView2 accelerator keys and the context menu: no F5/Ctrl+R reload, no Ctrl+F, no right-click copy/paste menu in app mode. Accepted for now; enabling them in app mode is a follow-up.
6. **Same-origin new-window links.** A PowerAtlas link opened with `target=_blank` from app mode goes to the default browser without a cookie and lands on the gate page. Routing same-origin links through a login URL is a follow-up.
7. **Live sign-in refresh check** (SC-9) after a real rotation, left for the user because it signs out other browsers.
8. **WebView2 initialization failure.** pywebview only logs a failed WebView2 init; app mode would then show a blank window instead of falling back to the browser. Detecting it (for example waiting for the first `loaded` event) is a follow-up.
10. **Settings show the stored shortcut, not the one running.** A hand-edited invalid peek shortcut runs as `ctrl+shift+z` and an invalid or conflicting browser shortcut is off, but the Settings fields show the stored value with no badge; only the log says so. Exposing the effective values in `/api/settings` is a follow-up (Phase 3 review, Low).
9. **Alt-based shortcuts and the foreground app's menu bar.** With a chord such as `alt+f1`, the user's app sees Alt down and up around the suppressed key and may activate its menu bar. Default and ctrl-based chords are unaffected. Masking (as AutoHotkey does) is a follow-up.

## Review Log

### 2026-10-06 -- Plan Review cycle 1 (via /qplan)

Personas: Architect (gap-critic lens), Senior engineer, Reliability engineer (Windows desktop UI). Plus the doc-impact sub-agent. Findings merged and deduplicated (A = Architect, S = Senior engineer, R = Reliability).

59 findings before merge; 31 after (9 High, 15 Medium, 7 Low). All auto-resolved in the plan; none needed a user decision.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Window transitions ran inside the low-level keyboard hook under a shared lock (A3, S6, R1). | Fixed -- D-21 and the Threading model: hook enqueues, one worker owns the window. |
| 2 | High | `evaluate_js` on the UI thread deadlocks and can block 20 s or forever (A4, R2). | Fixed -- nothing waits on the page from the UI thread; `resetOverlays` fired without waiting. |
| 3 | High | `stop()` lock behaviour undefined, so Quit could hang (A4, R3). | Fixed -- `stop()` never waits on the worker; bounded 5 s destroy join. |
| 4 | High | Closing handler failed open on exceptions and cancelled Windows shutdown (A2, S5, R4, R5). | Fixed -- D-22 direct `FormClosing` handler, `UserClosing` only, fail closed. |
| 5 | High | App state saved only on APP to PEEK, so X or double-tap hide lost resizes (A1, S8). | Fixed -- Save rule on every exit from APP. |
| 6 | High | Minimized app window saved off-screen bounds (A8, R7). | Fixed -- D-9 uses `WINDOWPLACEMENT`; minimized round-trips. |
| 7 | High | SC-3 focus restore impossible because `show()` always activates (A7, S4, R6). | Fixed -- D-20 non-activating peek plus previous-foreground restore. |
| 8 | High | `TestLoopbackDoors` and old peek tests encode removed behaviour; Phase 1 gate would fail (S1, S3, A13). | Fixed -- every affected test listed with its replacement; rejected-fix line forbids restoring the per-show reload. |
| 9 | High | SC-10 log-leak guard broken and new openers unguarded (S2, A15). | Fixed -- `test_the_login_link_is_never_logged` extended in Phases 1 and 3; Phase 4 no longer claims SC-10. |
| 10 | Medium | QA counted `/local-auth` access-log lines that do not exist (A5, S9, R13). | Fixed -- count the `loopback browser signed in with a login code` INFO line, with a positive control. |
| 11 | Medium | Fake-window unit tests and locked-session QA could not catch wrong chrome code (A10, R16). | Fixed -- unattended ctypes window probe as an exit criterion in Phases 1-3. |
| 12 | Medium | Unattended secret rotation would sign out the user's browsers (A11, R17). | Fixed -- rotation check left for the user; unit tests cover SC-9. |
| 13 | Medium | Restart grant not cited to a user turn (A12, S open question). | Fixed -- cites the `/qplan` invocation turn of 2026-10-06. |
| 14 | Medium | WebView2 init failure or early events not handled; `_webview_ok` forced true (A6, R11). | Fixed -- readiness gate and EdgeChromium check in `supports_app_mode`. |
| 15 | Medium | Narrowed key names would silently break exotic existing shortcuts (A9, S13). | Fixed -- `VK_NAMES` extended to pynput's names; startup keeps today's fallback; README note. |
| 16 | Medium | Toggle auto-repeat not modelled (S7). | Fixed -- auto-repeat is never an event, both modes; tested. |
| 17 | Medium | Alt+F4 or `WM_CLOSE` in PEEK undefined (A2, R8). | Fixed -- user close in PEEK behaves like Esc. |
| 18 | Medium | `resize`/`move` misdescribed as marshalled; DPI units unstated (A17, R9). | Fixed -- Current State corrected; all geometry physical pixels via Win32/`native`. |
| 19 | Medium | `native.Handle` read off the UI thread (R10). | Fixed -- D-14 reads it inside `Invoke` and caches it. |
| 20 | Medium | `suppress_event` could be swallowed by a never-raise wrapper (R12, S12). | Fixed -- called outside any `try`; tested. |
| 21 | Medium | Double-tap timed when processed, not when pressed (R18). | Fixed -- hook timestamp carried in the event. |
| 22 | Medium | Node-test gate would pass with labels missing (S10). | Fixed -- direct label and badge assertions in Phases 2 and 3. |
| 23 | Medium | QA window lookup by title and a cached HWND give false results (R14, A20, S16). | Fixed -- enumerate the process's windows, re-find per action, poll 3 s, DPI-aware checker. |
| 24 | Medium | Taskbar probe passed on `1+1` even if the page reset (R15). | Fixed -- page marker, `href` and child-HWND checks. |
| 25 | Low | Esc held with the chord was suppressed (S11). | Fixed -- a key matches a chord only if it is the chord's own key; Esc refused as a shortcut key. |
| 26 | Low | Tray Open while already in APP skipped the sign-in check (S14). | Fixed -- check runs on every event ending in PEEK or APP. |
| 27 | Low | Generation read order vs creation mint unstated (R19). | Fixed -- read before minting. |
| 28 | Low | `AttachThreadInput` thread and cleanup unstated (R20). | Fixed -- inside `Invoke`, detached in `finally`. |
| 29 | Low | Tray menu had no test seam (A14). | Fixed -- `_build_menu(server_url)`. |
| 30 | Low | `debug=False` disables reload, find and context menu; same-origin `_blank` links hit the gate (A18, S15, R21). | Escalated -- recorded as Follow-up 5 and 6. |
| 31 | Low | Gate command lacked `PYTHONPATH=src`; refused input value behaviour unstated; misleading source-pin note (A16, S18, S17). | Fixed -- command updated; input reverts to the stored value; note corrected. |

Doc-impact sub-agent: 12 uncovered hits added to section 8 and the phase exit criteria (README tray click line, `web.py` door docstrings, `peek.py`/`tray.py` door comments, the test-plan doc); `AGENTS.md` and `memory/MEMORY.md` hits recorded as Follow-up 2 (need user approval).

### 2026-10-06 -- Plan Review cycle 2 (via /qplan)

Same three personas, fresh context. 32 findings before merge; 22 after (4 High, 10 Medium, 8 Low). All auto-resolved. Two reviewer open questions were answered by the planner with reversible defaults, flagged for the user: Esc is suppressed while a peek shows, and a Toggle double-press from PEEK accepts a brief flicker rather than delaying every press.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Readiness gate fired before the form existed, so X could quit PowerAtlas (all three personas). | Fixed -- readiness waits on `events.shown`, then `native` and its handle, then subscribes `FormClosing`; new log line `PowerAtlas window ready`. |
| 2 | High | Tray `Open in browser` hidden at startup because pystray builds the menu before readiness. | Fixed -- static visibility; readiness decides routing at click time; tested. |
| 3 | High | HIDDEN x double-tap marked n/a, regressing the whole-chord double-tap. | Fixed -- double-tap rule applies in every state; a third tap resets; tests added. |
| 4 | High | Non-activating peek sent the dismiss Esc to the user's app. | Fixed -- Esc suppressed while `_peek_showing`; Alt-chord menu-bar side effect recorded as Follow-up 9. |
| 5 | Medium | Restoring a non-foreground APP activated it and left it above the user's app. | Fixed -- non-activating show commands, maximized repaired by the foreground restore, z-order put back. |
| 6 | Medium | No real-window check for peek from minimized or maximized APP; the two show-without-activate options differ. | Fixed -- normative Win32 primitives; probe steps added. |
| 7 | Medium | Any non-suppression exception in the filter stops pynput's listener. | Fixed -- decision computed inside `try`, suppression called after it; tested. |
| 8 | Medium | `stop()` bound did not guarantee Quit ends the process. | Fixed -- idempotent `stop()`, `Application.Exit`, then a watchdog running the shutdown tail; 15 s guarantee. |
| 9 | Medium | No taskbar-presence assertion; `WS_EX_APPWINDOW` not named. | Fixed -- candidate (b) spelled out; probe asserts owner and ex-style, foreground retention and minimize. |
| 10 | Medium | Window lookup ambiguous in the probe and live QA. | Fixed -- process, class prefix and title; in-process `_hwnd` in the probe. |
| 11 | Medium | `TestCreatePeek` mocks and the node test `hosts` map would break Phases 2-3. | Fixed -- listed in Phase 2 and 3 tests. |
| 12 | Medium | The `TopMost` setter may activate the window. | Fixed -- topmost through `SetWindowPos` with `SWP_NOACTIVATE` only. |
| 13 | Medium | Taskbar probe did not check focus survival across a handle recreation. | Fixed -- added to the taskbar property. |
| 14 | Medium | A hung UI thread blocked the worker silently. | Fixed -- `BeginInvoke` with a 2 s bounded wait and a WARNING. |
| 15 | Low | `data.time` wraps every 49.7 days; `0` is an unsafe sentinel. | Fixed -- masked arithmetic and a `None` sentinel. |
| 16 | Low | The probe would install a second global hook and mint without a secret. | Fixed -- listener and `_login_url` patched in the probe. |
| 17 | Low | D-9 default written into a workspace-coordinate placement. | Fixed -- default applied with `SetWindowPos`, then captured. |
| 18 | Low | `AllowSetForegroundWindow` is not a self-focus fallback. | Fixed -- dropped. |
| 19 | Low | Foreground check order around hide unstated. | Fixed -- captured before hiding. |
| 20 | Low | Backward-compat bullet misdescribed today's behaviour. | Fixed -- reworded as an accepted change with a WARNING naming the token. |
| 21 | Low | Stale test docstring and the door-spelling source guard not mentioned. | Fixed -- both named in Phases 1-2. |
| 22 | Low | Off Windows a modifiers-held double-tap never fires. | Fixed -- `_triggered` resets on the non-modifier key-up on every platform. |

Reviewer readiness confidence: Architect 35%, Senior engineer 55% (80% with these fixes), Reliability 65%. Ready state reached (no unresolved High, no auto-fixable Medium left), so cycle 3 was not run. The remaining risk is runtime behaviour that the Phase 1 probes and live QA check during `/qdev`.

### 2026-10-06 -- Implementation Review (after Phase 1, persona: Reliability engineer (Windows desktop UI), Senior engineer)

Implementation health: Yellow (all findings fixed in `0a73501`; live QA BLOCKED by a locked session).
16 findings after merge (3 High, 7 Medium, 6 Low). One review cycle per the user's override; fixes not re-reviewed (Step 9 covers them).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | End peek back to APP skipped the D-13 sign-in check; no test pinned it. | Fixed — check added first in that branch, with a rotation test. |
| 2 | High | A maximized background APP re-placed after a peek kept the foreground; the restore was dead code. | Fixed — foreground re-read after the re-place, handed back, window put below; outcome test. |
| 3 | High | `restore_foreground` ran Win32 calls on the worker, not inside `BeginInvoke`. | Fixed — routed through the UI helper; three tests. |
| 4 | Medium | Section 9 was empty although the commit shipped six divergences. | Fixed — section 9 written by the orchestrator. |
| 5 | Medium | Ticked probe criterion said `WM_CLOSE`, which actually quits PowerAtlas. | Fixed — criterion reworded to `SC_CLOSE`; behaviour recorded in section 9 and README. |
| 6 | Medium | A raising window call left `_peek_showing` set, swallowing Esc system-wide. | Fixed — failed exits from PEEK land in HIDDEN; five tests. |
| 7 | Medium | Readiness had one 2 s attempt; a busy UI thread disabled the window for the run. | Fixed — retries until the deadline; distinct timeout log. |
| 8 | Medium | A `_ui` timeout stored `None` as the saved placement. | Fixed — previous placement and state kept; five tests. |
| 9 | Medium | Bounded `_ui` wait and `Application.Exit` branch of `stop()` untested. | Fixed — both tested with fake natives. |
| 10 | Medium | Live-QA BLOCKED items were not listed for the user. | Fixed — list added under Phase 1; check deferred to Phase 3. |
| 11 | Low | A `show_app` posted between `_ready.set()` and the drain was dropped. | Fixed — drain before setting ready; race test. |
| 12 | Low | Esc key-up could reach the user's app after a suppressed key-down. | Fixed — key-up follows its key-down. |
| 13 | Low | Win32 reload could block the worker forever on a hung UI thread. | Fixed — bounded thread join with a WARNING. |
| 14 | Low | Second `shutdown_tail` caller returned at once, possibly skipping relaunch. | Fixed — second caller waits, bounded at 15 s. |
| 15 | Low | `stop()` clearing `_listener` could raise in the filter. | Fixed — local read and guard. |
| 16 | Low | Bare `WM_CLOSE` quits PowerAtlas, undocumented. | Fixed — README sentence; D-22 behaviour unchanged. |

Reviewers also asked to verify the Linux `shown` event for hidden windows: confirmed from pywebview's `gtk.py` and hardened (section 9). Mutation testing by the Senior engineer killed all five targeted mutations.

### 2026-10-06 -- Implementation Review (after Phase 2, persona: Senior engineer)

Implementation health: Green (all findings fixed in `41836bf` or the plan; live QA BLOCKED and deferred).
5 findings (0 High, 1 Medium, 4 Low). One review cycle per the user's override; fixes not re-reviewed.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | Section 9 had no Phase 2 divergences. | Fixed — Phase 2 block written to section 9 by the orchestrator. |
| 2 | Low | A hand-edited case variant or invalid `peek_mode` ran one mode while the UI showed another. | Fixed — `load_config` normalises `peek_mode`; 14 new cases. |
| 3 | Low | Copy named "Hold"/"Toggle" but no option carried those names. | Fixed — option labels renamed to start with Hold and Toggle. |
| 4 | Low | The plan-dictated hotkey description said "Hold to peek", wrong in Toggle. | Fixed — mode-neutral wording; recorded as a divergence. |
| 5 | Low | Nothing tested `refreshSettings` setting `#peekMode`. | Fixed — node check drives the line in a sandbox. |

Mutation testing by the reviewer killed all six targeted mutations (Toggle press return target, release no-op, Hold press no-op, save refusal, restart label, badge owner).

### 2026-10-06 -- Implementation Review (after Phase 3, persona: Senior engineer (validation lens), Reliability engineer (Windows input hooks))

Implementation health: Green (all code findings fixed in `f8e6863`; one Low moved to Follow-up 10; live injection BLOCKED).
13 findings after merge (1 High, 2 Medium, 10 Low). One review cycle per the user's override; fixes not re-reviewed.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Releasing a modifier before the chord key leaked repeated key-downs while the key-up stayed suppressed (since Phase 1). | Fixed — a held chord key's key-downs stay suppressed until its key-up, with a 1.5 s lost-key-up heal. |
| 2 | Medium | No filter-path test covered a modifier release mid-hold or a chord switch while held. | Fixed — four filter tests, mutation-checked. |
| 3 | Medium | The plan had no Phase 3 notes, divergences or evidence for the live save refusals. | Fixed — notes, QA evidence and section 9 written by the orchestrator. |
| 4 | Low | A chord switch mid-hold re-armed only the new chord. | Fixed — key-up re-arms every chord containing the key. |
| 5 | Low | The constructor accepted an unvalidated browser chord. | Fixed — invalid or overlapping chord disabled with a WARNING. |
| 6 | Low | Several non-modifier keys were accepted but might never fire. | Fixed — exactly one non-modifier key required; recorded as a divergence. |
| 7 | Low | The `browser` branch in `_handle` was unreachable; the log guard tested it directly. | Fixed — branch removed; guard drives the real worker loop. |
| 8 | Low | Saving the peek shortcut was refused because of a browser shortcut that was not running. | Fixed — same in-force rule as startup. |
| 9 | Low | Settings showed stored, not running, shortcut values after a hand-edit. | Escalated — recorded as Follow-up 10 for the user. |
| 10 | Low | Inline errors survived a refresh and a reopen. | Fixed — cleared in `refreshSettings` and on open. |
| 11 | Low | Config warnings logged raw values with `%s`. | Fixed — `%r`. |
| 12 | Low | No pure-function test pinned "only the chord's own key matches". | Fixed — two `match_chord` cases. |
| 13 | Low | A browser press during the 30 s readiness wait is acted on after the wait, one tab per press; smoke ran on the pre-commit tree. | Escalated — noted here for the user; smoke re-run after `f8e6863` passed. |

## Harness Improvement Opportunities

- `/qdev` Step 5b treats a QA BLOCKED verdict as a hard stop, but an unattended overnight run with a locked desktop BLOCKs every live window check while the unattended probe passes — cost: the orchestrator had to choose between stopping the whole run and overriding the gate; it continued and deferred the checks — suggested change: let a plan declare a locked-session fallback (probe evidence counts, live checks deferred to the user) that keeps auto-continue.
- `/qdev`'s dirty-tree stop fired on another session's unrelated, disjoint edits while the user was asleep — cost: a judgment call to continue against the letter of the rule — suggested change: allow continuing when the foreign files are disjoint from every remaining phase's scope and all commits are pathspec-scoped, recording the file list.
- `/qexplore` Step 1.5 dispatch had to be restarted when the user asked for a different sub-agent model mid-dispatch — cost: three agents' partial work discarded, about 2 minutes — suggested change: let `/qexplore` read a model preference for exploration sub-agents from memory before dispatch.
