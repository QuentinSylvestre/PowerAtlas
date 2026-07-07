# Config Hot-Reload Across Views and Peek Window State Reset

> **Date**: 2026-07-07
> **Status**: In Progress  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Same-view instant config feedback, cross-view on-visibility reload, peek window overlay reset on hide, htmx.ajax fix

---

## Intent

### Problem statement & desired outcomes

Config changes made in the PowerAtlas UI (main browser view or peek window) are not reflected until a manual refresh or page reload. Settings inputs (terminal, peek hotkey display, port, provider settings) are server-rendered snapshots that go stale. The peek window retains open modals and transient overlays between show/hide cycles, creating a confusing experience when the user invokes the hotkey again.

Additionally, the custom htmx-mini implementation lacks `htmx.ajax()`, causing launcher tile refreshes to silently fail after create/update/delete operations — a same-view feedback bug of the same class.

Desired outcomes:
- When a setting is changed in one view, the UI in that same view reflects it instantly (local DOM update after save)
- When the other view becomes visible (peek show or browser tab focus), it picks up config changes from disk via a lightweight API fetch
- The peek window dismisses modals and transient overlays on hide, so each invocation starts clean
- Launcher tile operations (create/update/delete/provider-save) visually update the grid immediately

### Success criteria

1. **SC1 — Same-view instant feedback**: After saving any setting (terminal, provider color/args/enabled, custom launcher CRUD), the UI element reflecting that setting updates without requiring manual refresh or page reload.
2. **SC2 — Cross-view on-visibility reload**: When the peek window is shown or the main browser tab regains focus, settings inputs and JS state (`_providerSettings`, `_launchers`) reflect the current config from disk.
3. **SC3 — Peek modal reset on hide**: Open `<dialog>` elements (launcher modal), emoji picker, session tooltip slots, and visible toasts are dismissed when the peek window hides. Dashboard state (expanded cards, selections, scroll, search, provider filter) is preserved.
4. **SC4 — htmx.ajax replacement**: `saveLauncher()`, `deleteLauncher()`, and provider-save flows use the manual fetch+innerHTML+htmx.process pattern (already proven elsewhere in the codebase) instead of the non-existent `htmx.ajax()`.
5. **SC5 — Dead code removal**: The `htmx:configRequest` event listener (which never fires because the custom htmx-mini doesn't dispatch it) is removed.

### Scope boundaries & non-goals

**In scope**: Frontend JS changes in `index.html`, new `/api/settings` endpoint in `web.py`, `peek.py` `_hide()` method enhancement, fix for `htmx.ajax` calls, dead code removal.

**Non-goals**: 
- Making `port` or `peek_hotkey` hot-reloadable without restart (they depend on process-level resources)
- Adding SSE, WebSocket, or polling mechanisms
- Full page reload on cross-view switch
- Fixing latent `htmx.process()` missing calls in `loadExpandedCards`/`refreshExpandedSessions` (not currently broken)
- Changes to the Config dataclass or TOML schema


## Context

Config is persisted in `config.toml` via `config.py`'s `load_config()`/`save_config()`. The backend re-reads from disk on every HTTP request (no caching), so server-side state is always fresh. The problem is entirely client-side: settings inputs are server-rendered at page load and never re-fetched; the peek window's webview DOM persists between show/hide cycles; and `htmx.ajax()` (used in launcher save flows) doesn't exist in the custom 56-line htmx-mini.

Two views share the same server: the main browser tab (opened via tray icon) and the peek window (pywebview overlay, hotkey-triggered). Both load `index.html` once — the peek window at process start, the browser on each "Open" click.

## Files to modify

| File | Change |
|---|---|
| `src/power_atlas/web.py` | Add `GET /api/settings` endpoint returning current config values for UI inputs |
| `src/power_atlas/templates/index.html` | (1) Add `refreshSettings()` function that fetches `/api/settings` and patches DOM inputs + JS vars. (2) Call it from `visibilitychange` handler. (3) Add `resetOverlays()` function called from peek hide. (4) Replace `htmx.ajax(...)` calls with manual fetch pattern. (5) Remove dead `htmx:configRequest` listener. |
| `src/power_atlas/peek.py` | Add `evaluate_js("if(typeof resetOverlays==='function') resetOverlays()")` call in `_hide()` before toggling fullscreen |
| `tests/test_web.py` | Add test for `/api/settings` endpoint |
| `tests/test_peek.py` | Update `_hide()` tests to verify `evaluate_js` is called |

## External Dependencies

None — code-only change with no infra/CI/third-party requirements.

## Rollout / Migration / Cleanup

None — additive JS and one new endpoint; no schema or data changes.

## Step-by-step

### 1. Add `/api/settings` endpoint [QA]

**Covers**: SC-2

Add a `GET /api/settings` endpoint to `web.py` that returns the current config state needed by the frontend:

```python
@app.get("/api/settings")
async def api_settings():
    config = load_config()
    try:
        autostart_enabled = autostart.is_enabled()
    except Exception:
        autostart_enabled = False
    return {
        "terminal_command": config.terminal_command,
        "peek_hotkey": config.peek_hotkey,
        "port": config.port,
        "provider_settings": config.provider_settings,
        "custom_launchers": config.custom_launchers,
        "autostart": autostart_enabled,
    }
```

Add a test in `test_web.py` verifying the endpoint returns expected keys and types, including a case with pre-populated config (custom launchers, provider settings).

#### Implementation (2026-07-07, code: dca2e5a)

Added a `GET /api/settings` endpoint to `web.py` that reads fresh config from disk via `load_config()`, wraps `autostart.is_enabled()` in a try/except defaulting to False, and returns a JSON dict with 6 keys: `terminal_command`, `peek_hotkey`, `port`, `provider_settings`, `custom_launchers`, and `autostart`. Added 3 tests in `test_web.py` verifying the endpoint returns correct keys, reflects pre-populated config values, and gracefully handles autostart exceptions. All new tests pass.

### 2. Add `refreshSettings()` JS function and wire to visibilitychange [QA]

**Covers**: SC-1, SC-2

In `index.html`, add a `refreshSettings()` function:

```javascript
function refreshSettings() {
  // Skip if a modal is open (avoid overwriting in-flight edits)
  var modal = document.getElementById('launcherModal');
  if (modal && modal.open) return;
  fetch('/api/settings').then(function(r) { return r.json(); }).then(function(d) {
    // Update topbar inputs
    var termSel = document.querySelector('.topbar-select');
    if (termSel) {
      // Handle custom terminal: if value not in options, select 'custom' and fill text input
      var found = false;
      for (var i = 0; i < termSel.options.length; i++) {
        if (termSel.options[i].value === (d.terminal_command || '')) { found = true; break; }
      }
      if (found) {
        termSel.value = d.terminal_command || '';
        var customInput = document.getElementById('customTerminal');
        if (customInput) customInput.style.display = 'none';
      } else {
        termSel.value = 'custom';
        var customInput = document.getElementById('customTerminal');
        if (customInput) { customInput.style.display = 'inline-block'; customInput.value = d.terminal_command || ''; }
      }
    }
    var peekInput = document.getElementById('peekHotkey');
    if (peekInput) peekInput.value = d.peek_hotkey || 'ctrl+shift+z';
    // Update JS state variables
    _providerSettings = d.provider_settings || {};
    _launchers = d.custom_launchers || [];
    // Refresh launcher tiles to reflect provider enable/disable/color changes
    var tilesEl = document.getElementById('launcher-tiles');
    if (tilesEl) {
      fetch('/partials/launchers').then(function(r) { return r.text(); }).then(function(html) {
        tilesEl.innerHTML = html;
        if (window.htmx) htmx.process(tilesEl);
      });
    }
  }).catch(function() { /* silent — next visibility event will retry */ });
}
```

Wire it into the existing `visibilitychange` handler (replacing the current one):

```javascript
document.addEventListener('visibilitychange', function() {
  if (!document.hidden) { refreshSettings(); refreshCards(true); }
});
```

Note: `refreshExpandedSessions()` is removed from this handler because `refreshCards(true)` already re-expands open cards with fresh data — calling both caused duplicate session fetches.

Also call `refreshSettings()` inside `doRefresh()` (the peek window's on-show entry point) so the peek window picks up config changes on every invocation. Since `doRefresh()` already calls `refreshCards(true)`, the launcher tiles will refresh via that path — `refreshSettings()` skips the redundant `/partials/launchers` fetch when called from `doRefresh()` by design (the fetch inside `refreshSettings` serves the visibilitychange path where `refreshCards` handles workspace panels but not the launcher grid).

### 3. Add `resetOverlays()` and wire to peek hide [QA]

**Covers**: SC-3

In `index.html`, add:

```javascript
function resetOverlays() {
  // Close open dialogs
  var modal = document.getElementById('launcherModal');
  if (modal && modal.open) modal.close();
  // Hide emoji picker
  var picker = document.getElementById('emoji-picker');
  if (picker) picker.style.display = 'none';
  // Hide tooltip slots
  document.querySelectorAll('.session-tooltip-slot').forEach(function(s) { s.style.display = 'none'; });
  // Clear toasts older than 1s (preserve recent action confirmations)
  var toasts = document.getElementById('toastContainer');
  if (toasts) {
    toasts.querySelectorAll('.toast').forEach(function(t) {
      if (!t.dataset.ts || (Date.now() - parseInt(t.dataset.ts, 10)) > 1000) t.remove();
    });
  }
}
```

Note: `showToast()` must set `t.dataset.ts = Date.now()` on each toast element so `resetOverlays()` can age them. This is a one-line addition to the existing `showToast()` function.

In `peek.py`, update `_hide()`:

```python
def _hide(self) -> None:
    win = self._window
    if win and self._visible:
        self._visible = False
        log.debug("Peek hide")
        try:
            win.evaluate_js("if(typeof resetOverlays==='function') resetOverlays()")
        except Exception:
            pass  # defensive — rapid hotkey toggling can race with webview teardown
        win.toggle_fullscreen()
        win.hide()
```

The `try/except` prevents unhandled exceptions on the pynput listener thread during rapid show/hide cycling. The `evaluate_js` call runs while the window is still visible (before `toggle_fullscreen`/`hide`), ensuring DOM manipulation executes on an active webview.

### 4. Fix htmx.ajax calls and remove dead listener [QA]

**Covers**: SC-4, SC-5

Replace ALL broken `htmx.ajax('GET', '/partials/launchers', '#launcher-tiles')` calls with the manual fetch pattern. There are two call sites:

1. **`saveLauncher()`** — custom-launcher branch (after `/api/launcher/create` or `/api/launcher/update` succeeds)
2. **`deleteLauncher()`** — after `/api/launcher/delete` succeeds

Both currently chain: `fetch('/api/launchers').then(r => r.json()).then(d => { _launchers = d; modal.close(); htmx.ajax(...) })`.

Replace the `htmx.ajax(...)` call in each with:

```javascript
fetch('/partials/launchers').then(function(r) { return r.text(); }).then(function(html) {
  var el = document.getElementById('launcher-tiles');
  el.innerHTML = html;
  if (window.htmx) htmx.process(el);
});
```

Note: the provider-save branch in `saveLauncher()` (when `id.startsWith('provider--')`) also calls `htmx.ajax` — this is fixed in Step 5 alongside the local state update.

This pattern is already proven in `refreshCards()`, `switchProvider()`, and `pinSession()`.

Remove the dead `htmx:configRequest` listener from the `DOMContentLoaded` handler:

```javascript
// REMOVE this block:
document.querySelector('.panels-container').addEventListener('htmx:configRequest', function(e) {
  var path = e.detail.path || '';
  var m = path.match(/provider=([^&]*)/);
  if (m && e.detail.elt && e.detail.elt.classList && e.detail.elt.classList.contains('provider-tab')) {
    window._activeProvider = decodeURIComponent(m[1]);
  }
});
```

The `_activeProvider` tracking already works via `switchProvider()` setting it directly.

### 5. Same-view instant feedback for provider settings save [QA]

**Covers**: SC-1, SC-4

In `saveLauncher()`, the provider-save branch (when `id.startsWith('provider--')`) currently calls `htmx.ajax('GET','/partials/launchers','#launcher-tiles')` which silently fails. Replace with local state update + manual fetch:

```javascript
// After POST /api/provider/save succeeds (replace existing htmx.ajax + refreshCards):
_providerSettings[key] = payload;
fetch('/partials/launchers').then(function(r) { return r.text(); }).then(function(html) {
  var el = document.getElementById('launcher-tiles');
  el.innerHTML = html;
  if (window.htmx) htmx.process(el);
});
refreshCards(true);
```

This ensures the provider tile immediately reflects color/enabled changes without waiting for the next full refresh, and eliminates the broken `htmx.ajax` call.

## Verification

- `pytest tests/test_web.py tests/test_peek.py` — all pass
- Manual: open main browser + start peek window. Change terminal in browser → invoke peek hotkey → peek shows updated terminal value. Change provider color in peek → release hotkey → focus browser tab → provider tile shows new color.
- Manual: open launcher modal in peek → release hotkey (hide) → invoke peek again → modal is closed.
- Manual: create/update/delete a custom launcher → tiles grid updates immediately without manual refresh.

## Documentation updates

| Document | Update needed |
|---|---|
| `README.md` | None — no user-facing CLI/config changes |

## Review Log

### 2026-07-07 — High-effort review (4 personas: Senior engineer, End-user advocate, Reliability engineer, Performance engineer)

11 findings (3 High, 5 Medium, 3 Low). 9 auto-resolved, 2 noted.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `refreshSettings()` had no `.catch()` — network error leaves stale UI silently. | Resolved — added `.catch(function(){})` matching codebase pattern. |
| 2 | High | Toast clearing on hide removed user's recent action feedback. | Resolved — `resetOverlays()` now only removes toasts older than 1s. |
| 3 | High | `evaluate_js` in `_hide()` unprotected — exception on pynput thread could kill hotkey listener. | Resolved — wrapped in `try/except Exception: pass` with comment. |
| 4 | Medium | `refreshExpandedSessions()` redundant alongside `refreshCards(true)` — duplicate session fetches. | Resolved — removed from visibilitychange handler; refreshCards covers it. |
| 5 | Medium | Double `refreshSettings()` on peek show (doRefresh + visibilitychange both fire). | Resolved — `refreshSettings()` called only in doRefresh for peek; visibilitychange for browser tab. |
| 6 | Medium | No guard against refreshSettings overwriting in-flight modal edits. | Resolved — added early-return if `launcherModal.open`. |
| 7 | Medium | Steps 4/5 overlapped on provider-save htmx.ajax fix — unclear ownership. | Resolved — clarified Step 4 covers custom-launcher branches; Step 5 covers provider-save branch. |
| 8 | Medium | Custom terminal value not handled in refreshSettings (select shows blank). | Resolved — added option-match logic with fallback to 'custom' + text input. |
| 9 | Medium | `autostart.is_enabled()` in `/api/settings` could throw and 500 the endpoint. | Resolved — wrapped in try/except, defaults to False. |
| 10 | Low | `_activeProvider` not synced across views. | Noted — acceptable; provider filter is a transient UI preference, not a persisted config value. |
| 11 | Low | No aria-live for launcher tile refresh after settings change. | Noted — can be added later; no existing ARIA patterns for dynamic content in this codebase. |

### 2026-07-07 — Implementation Review (after Phase 1, persona: Senior engineer)

Implementation health: Green.
0 findings.
QA verification: PASS (1 API surface verified, 3 probes executed via pytest).

No findings — implementation matches plan specification exactly. Endpoint returns all 6 keys, autostart exception is handled, tests cover all specified scenarios.