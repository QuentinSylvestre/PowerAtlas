# Merged Peek and App Window with Configurable Shortcuts

> **Date**: 2026-10-06
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Turn the peek overlay into one PowerAtlas window with a peek mode and an app mode, add configurable shortcuts, and offer app window and browser from the tray

---

## Intent

### Problem statement & desired outcomes

PowerAtlas opens its UI in two ways today: the default browser (tray Open, peek double-tap) and the peek overlay, a frameless full-screen pywebview window shown while the hotkey is held. There is no desktop app window, and the only shortcut setting is `peek_hotkey`.

The user wants a desktop app window without adding Electron. The peek overlay already runs a WebView2 window through pywebview, so the outcome is one PowerAtlas window with two modes:

- **Peek mode**: frameless, always on top, full screen, off the taskbar. Shown by the peek shortcut.
- **App mode**: a normal framed window in the taskbar, focused, at its last size and position. Opened by a double-tap of the peek shortcut or by the tray.

The window keeps its page state across hides and mode switches, instead of resetting to the dashboard on every show. The browser stays available from the tray and from an optional shortcut.

### Success criteria

1. With the window hidden, holding the peek shortcut shows peek mode; releasing it hides the window. The page shown is the app's current page, not a reset to `/`.
2. A double-tap of the peek shortcut, from any state, switches the window to app mode, framed, focused, in the taskbar, at its last size, position and maximized state. If the window is already in app mode and focused, the double-tap hides it.
3. With the window open in app mode, holding the peek shortcut shows peek mode temporarily; releasing it returns to app mode with the same bounds, maximized state and focus state.
4. The app window's X button hides the window and keeps its state. It never quits PowerAtlas.
5. A **Peek mode** setting offers **Hold** (default, today's behaviour) and **Toggle**. In Toggle, a press shows peek mode and the next press hides it; a second press within the double-tap interval opens app mode instead.
6. An optional **Browser shortcut** setting, empty (off) by default, opens a new signed-in browser tab.
7. Saving a shortcut rejects an invalid format and rejects a shortcut that equals, or contains, the other shortcut.
8. The tray menu has **Open PowerAtlas** (default action, app mode) and **Open in browser**, followed by the existing Copy login link, Logs, Restart and Quit. When the window is unavailable (pywebview or pynput missing or failing to load), Open PowerAtlas opens the browser and Open in browser is hidden.
9. After a local-secret rotation, the next show signs the window in again instead of leaving it on the gate page.
10. Every login URL is still minted in-process, used once and never logged.

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

## Exploration Discovery
<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### Existing patterns & constraints

- **Thread layout** (`__main__.py:847-883`): uvicorn on a daemon thread; with peek available, the tray runs on a daemon thread and `peek.start(on_main_thread=True)` blocks the main thread in `webview.start()`. Without peek, `run_tray` owns the main thread and no webview exists.
- **pywebview 6.2.1** (installed; pin `>=5.0` at `pyproject.toml:19`): `start()` must run on the main thread (`webview/__init__.py:239-240`). On WinForms, the loop ends only when the last window instance closes (`webview/platforms/winforms.py:372-398`). `closing` is cancellable: a handler returning `False` sets `args.Cancel` (`winforms.py:400-403`). `window.native` is the WinForms form (`winforms.py:195`). Window calls are marshalled by the backend with `Invoke` (`winforms.py:491-501`). Backend is EdgeChromium (`is_chromium True`, checked).
- **pynput 1.8.2**: callbacks are serialised on one listener thread. `GlobalHotKeys` cannot parse the repo's `ctrl+shift+z` format (checked: `HotKey.parse('ctrl+shift+z')` raises) and has no suppression. The repo's own `_win32_event_filter` (`peek.py:144-186`) suppresses the hotkey key and is hard-wired to one `_trigger_keys` set.
- **Hotkey state machine** (`peek.py:211-319`): show on full combo; hide when any required modifier is released; Esc dismisses; double-tap is detected in `_show` via `_last_trigger_time` and `_DOUBLE_TAP_INTERVAL = 0.5` (`peek.py:52`), and depends on `_triggered` being reset on the key-up of the non-modifier key (`peek.py:178-186`). Matching is `issubset`, so a superset chord also fires.
- **Nothing may raise out of the pynput hook**: an escaping exception stops the listener (`peek.py:221-225`, `262-269`). `_hide`'s `win.hide()` and `_show`'s `show/resize/move/native.Invoke` are currently unguarded (`peek.py:247-251`, `287`).
- **Login code** (`web.py:2015-2093`, `2570-2587`): single-use, 120 s TTL, cap 64, minted in-process only, never via a route. Every opener goes through `tray._login_url` (`tray.py:48-63`). `/local-auth` always redirects to a literal `/` (`web.py:5007`). The pywebview logger is clamped to INFO because DEBUG logs URLs carrying codes (`peek.py:19-31`). Terminology: "login code", never "nonce" or "token" (`AGENTS.md § Terminology`).
- **Cookie jars**: two pywebview windows in one process do **not** share cookies (checked by probe), matching the comment at `peek.py:105`. A merged single window keeps one jar.
- **Config key precedent**: dataclass field in `config.py` (`peek_hotkey` at `config.py:94`), `_SETTING_TYPES` (`web.py:5812-5830`), `_RESTART_TO_APPLY` (`web.py:5847-5858`), settings payloads (`web.py:2692`, `web.py:5521`), UI in `templates/partials/settings_modal.html:43-60`, restart labels and owner map in `index.html:9282-9289` and `index.html:9418`. Enumerated values follow `parse_permission_mode` (`config.py:62-80`): load never raises and falls back to a safe value; write path validates explicitly (example at `web.py:5972-5985`). `load_config` keeps only correctly typed values (`config.py:849-858`).
- **Save validation gap**: `/api/save-setting` checks strings only for length and control characters (`web.py:5931-5935`); an invalid hotkey is caught only at startup by `create_peek`, which falls back silently (`peek.py:352-358`).
- **Page has no door detection**: no `window.pywebview`, query param or UA check (grep of templates and static). `resetOverlays()` (`index.html:5616`) is only called by `peek._hide` through `evaluate_js` (`peek.py:282`).
- **Tray** (`tray.py:167-222`): fixed menu built once; single `_peek_stop_callback` slot (`tray.py:22-28`) used by Quit, Restart and `trigger_restart`; `on_open` also starts a `warmup_pinned` thread (`tray.py:170-174`). The peek double-tap opens the browser directly with `webbrowser.open` (`peek.py:227`) and skips that warmup.
- **Prior art**: `plans/done/260630-1607_PEEK_WINDOW.md:45,57` records that pywebview was once removed and rejected "for main UI, not overlay". This plan reintroduces it for the main UI role; `/qplan` should record why that rejection no longer applies (WebView2 backend, proven in peek). `plans/done/260707-1259_CONFIG_HOT_RELOAD_AND_PEEK_RESET.md` introduced `resetOverlays` on peek hide.
- **Governance** (`AGENTS.md § Doc & Test Guidelines`): Python changes need a restart; never restart without a grant. Template inline-script changes run `node tests/acp_page.test.mjs`. A class setting `display` on a `hidden`-toggled element needs a `[hidden]` rule. Pre-commit hooks `_check_test_names.py` and `_check_public_ids.py`.
- Step 1.5 dispatched the code-tracing trio (on Sonnet, at the user's request): in-scope files were predominantly Python source (`peek.py`, `tray.py`, `__main__.py`, `web.py`, `config.py`) plus template script.

### Risks & mitigations

- **R1 Overlapping shortcuts** fire two actions (`issubset` at `peek.py:169,305`). Mitigation: save-time conflict check (SC7) plus the same check at startup.
- **R2 Dead cookie after secret rotation** now that shows no longer reload the login URL (`web.py:6114-6135`). Mitigation: re-sign on show when the window is signed out (SC9); detection method left to `/qplan`.
- **R3 Restoring app mode after a peek** loses maximized state if only bounds are saved. Mitigation: save `WindowState` with the bounds.
- **R4 Focus stealing**: Windows may refuse foreground to a background process, so a double-tap could flash the taskbar instead of focusing. Unverified; live test.
- **R5 Hook exceptions** stop the listener. Mitigation: every new action invoked from the hook is wrapped, as `peek.py:221-231` does today.
- **R6 Shutdown with a hidden-on-close window**: the `closing` cancel handler must be removed or bypassed on Quit and Restart, or `peek.stop()` cannot destroy the window. The probe removed the handler before `destroy()`.
- **R7 Test fixtures bypass `__init__`**: `tests/test_peek.py:241-257` replaces `PeekWindow.__init__` with a fixed attribute list; new attributes break those tests until the fixture is updated.
- **R8 Stale docs and comments**: `README.md:60-65` (doors list), `README.md:178`, `README.md:219`, `README.md:295`; `web.py:2028` ("Three doors"); `__main__.py:422` docstring.
- **Sub-agent conflict**: none on facts. One report said the tray has no tests; `tests/test_tray.py` exists (icon tests only).

### Resolved decisions

- Q1: What should closing the app window do, and what should showing it again do? — A: Option A — Decision: X hides the window and keeps its state; showing it again restores it as left, reloading only when sign-in has expired. The toggle shortcut hides the window when it is visible and focused, and brings it to the front when it is visible but behind other windows.
- Q2: One window with two modes, or two windows; and how does a single press behave? — A: Ok (merge, with the proposed behaviour; the user had offered the merge) — Decision: one window with peek mode and app mode. Hidden: hold shows peek, release hides. Double-tap from any state: app mode. App mode open: hold shows peek temporarily, release returns to app mode as before. X hides.
- Q3: Which shortcut settings exist? — A: Ok — Decision: Peek shortcut (default `ctrl+shift+z`); Peek mode Hold (default) or Toggle, with a second press inside the double-tap interval meaning app mode; double-tap always toggles app mode (no browser option); optional Browser shortcut, empty by default. Esc dismisses peek mode only.
- Q4: Tray menu layout and default action? — A: Ok — Decision: Open PowerAtlas (default, app mode), Open in browser, then Copy login link, Logs, Restart, Quit. Without a window, Open PowerAtlas opens the browser and Open in browser is hidden.

Proposed terminology for `/qplan`'s Terminology bootstrap:
- **PowerAtlas window**: the single pywebview window. Not "peek window" for the whole thing, and not "app window" as a separate object.
- **peek mode** / **app mode**: its two presentations. Not "overlay" for the mode name in UI text.

### Open items

- (execution-contingent) Whether `show()` plus `Activate()` from the hook thread gains focus on a double-tap, or needs a foreground workaround (R4).
- (execution-contingent) How `target=_blank` links (`transcript-renderer.js:495`) and `confirm()` dialogs behave in app mode. Links should open in the default browser.
- (deterministic) How to detect "signed out" for SC9: current URL of the window, a page signal, or the cookie. Decide from `web.py` gate behaviour.
- (deterministic) Whether `resize/move` to full screen is still the right way to enter peek mode on a window that may be maximized, or whether `WindowState` must be set to Normal first.

### Assumptions (unconfirmed)

The user accepted these at the assumptions checkpoint without changing them.

- New shortcut and mode settings are restart-to-apply. (Migration & rollout)
- Shortcut save validation checks format and conflicts. (Edge cases)
- The window starts hidden at launch and on autostart. (UX flow)
- App mode remembers bounds and maximized state for the current run only. (Data model)
- App mode shows in the taskbar; peek mode does not. (UX flow)
- `resetOverlays` runs only when a peek hides the window completely, not when it returns to app mode. (UX flow)
- Windows only for app mode; Linux keeps today's behaviour. (Constraints)
- Docs and tests: update README doors list and settings rows, `test_peek`, `test_config`, `test_web`, `test_tray`, `acp_page.test.mjs`, and the `web.py:2028` comment. No new test files. (Testing strategy)

### Recommended approach

1. **Config and settings**: add `peek_mode` (`hold`/`toggle`) and `browser_hotkey` (empty means off) to `Config`, `_SETTING_TYPES`, `_RESTART_TO_APPLY`, both settings payloads and the settings modal. Add one shared hotkey validator used by the save path (format, conflict) and by startup.
2. **Window**: keep the single window created before `webview.start()`. Add mode switching through `window.native` with `Invoke`: peek mode sets frameless, `TopMost`, full-screen bounds and hides from the taskbar; app mode restores border, `TopMost` off, taskbar, saved bounds and `WindowState`. Register a `closing` handler that cancels and hides, removed before final `stop()`.
3. **Shortcut handling**: generalise `_win32_event_filter` and `_on_press`/`_on_release` from one `_trigger_keys` set to a small table of chords and actions, keeping suppression and the double-tap reset. Implement Hold and Toggle for the peek chord; double-tap dispatches to app mode; the browser chord opens a signed-in tab.
4. **State and sign-in**: stop reloading the login URL on every show; load it at creation and when the window is signed out.
5. **Tray**: new menu items and fallback; replace the single stop callback with a window controller the tray can call (show app mode, stop).
6. **Docs and tests**: README, settings copy, comments, and the existing test files listed above.

### QA environment

- Python suite: `.venv-PowerAtlas/Scripts/python -m pytest tests/test_peek.py tests/test_config.py tests/test_tray.py tests/test_web.py --timeout=300`.
- Page script: `node tests/acp_page.test.mjs` after any settings-modal or `index.html` script change.
- Live behaviour needs a PowerAtlas restart, which needs the user's grant (`AGENTS.md`). Shortcuts, focus, mode switching and the tray must be checked by hand on the Windows desktop, since global hotkeys and native window chrome cannot be driven by Playwright. The settings modal can be checked with the `pa_local` cookie recipe in `AGENTS.md § Verification Setup`.
- Window-only behaviour can be pre-checked with a throwaway pywebview script, as done during exploration (creation after start, close-cancel, runtime chrome switch, separate cookie jars).
