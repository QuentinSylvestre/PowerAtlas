# Configurable App Port

> **Date**: 2026-07-05
> **Status**: Complete  <!-- Status lifecycle: Exploring → Draft → In Progress → Complete -->
> **Scope**: Make the web server port configurable (static or random) via the dashboard UI

---

## Intent

### Problem statement & desired outcomes

PowerAtlas currently binds its web server to a random OS-assigned port (`port=0`) on every launch. This means:
- The URL changes every restart — browser bookmarks and peek window targets rotate.
- External tools or scripts cannot reliably address the dashboard.

The desired outcome is a user-configurable port setting that lets the user choose between random (current behavior) and a static port, with the ability to generate a random-but-stable port value via a "randomize" button.

### Success criteria

- SC1: A `port` field exists in `config.toml` (int, default 0 = random, backward-compatible).
- SC2: The index page's top banner exposes a port control: mode toggle (random/static), numeric input, and a randomize button (picks a value in 49152–65535).
- SC3: Saving a new port value auto-triggers a restart via the existing `/api/restart` mechanism.
- SC4: If the configured static port is occupied at startup, the app falls back to random and logs a warning.
- SC5: The orphaned `/settings` page route and `settings.html` template are removed.

### Scope boundaries & non-goals

**In scope:**
- New `port: int` field on Config dataclass + TOML persistence
- Port binding logic change in `__main__.py` (read from config instead of hardcoded 0)
- Fallback-to-random with log warning on port-in-use
- Banner UI control for port (settings area of the index page)
- Auto-restart on port change
- Deletion of unused `/settings` route + template
- Test and README updates

**Non-goals:**
- Exposing the server on non-localhost interfaces (stays 127.0.0.1)
- Port validation probe at save-time (only validated at startup)
- Any changes to the peek or tray subsystems (they already receive `server_url` at startup — a static port just means it stays the same across restarts)


## Context

PowerAtlas runs a uvicorn web server on `127.0.0.1` with `port=0` (OS-assigned random port) at `__main__.py:233`. The resolved port forms a `server_url` string consumed by the peek window (pywebview) and tray icon (browser open). Changing port requires a full process restart — the existing `/api/restart` mechanism handles this cleanly. The config system is a simple TOML-backed dataclass (`config.py`) with thread-safe load/save. The index page's topbar contains inline settings (terminal dropdown, autostart toggle, peek hotkey input) that save via `POST /api/save-setting` with a `_SETTING_TYPES` allowlist.

## Files to modify

| File | Change |
|---|---|
| `src/power_atlas/config.py` | Add `port: int = 0` field to Config dataclass |
| `src/power_atlas/__main__.py` | Read `config.port` for uvicorn; add fallback-to-random on bind failure with proper cleanup |
| `src/power_atlas/web.py` | Add `"port": int` to `_SETTING_TYPES` with bool guard + range validation; remove `/settings` route + `save_settings` handler; pass `port` to index template context; add custom terminal option to topbar |
| `src/power_atlas/templates/index.html` | Add port control group to topbar (mode toggle, numeric input, randomize button, Apply for both modes); add custom terminal input |
| `src/power_atlas/templates/settings.html` | Delete file |
| `tests/test_config.py` | Add port round-trip test, bool rejection test |
| `tests/test_web.py` | Add port save-setting tests (valid, out-of-range, bool); remove settings page tests |
| `README.md` | Add `port` to Configuration section example |

## External Dependencies

None — code-only change, localhost binding.

## Rollout / Migration / Cleanup

None — existing configs without `port` key default to `0` (random), preserving current behavior.

## Step-by-step

### 1. Config field + save validation [QA]

Add `port: int = 0` to the `Config` dataclass at `config.py:29`.

```python
@dataclass
class Config:
    port: int = 0  # 0 = random (OS-assigned), >0 = static port
    peek_hotkey: str = "ctrl+shift+z"
    terminal_command: str = ""
    # ... rest unchanged
```

TOML integers deserialize as Python `int`, so `load_config()`'s type-check (`isinstance(v, expected)`) works without changes. Existing configs without the key get default `0`.

Add to `_SETTING_TYPES` in `web.py:492`:

```python
_SETTING_TYPES: dict[str, type] = {
    "port": int,
    "terminal_command": str,
    "peek_hotkey": str,
    "pinned_folders": list,
    "pinned_sessions": list,
}
```

Add bool guard and range validation to `save_setting` in `web.py` — Python's `isinstance(True, int)` is `True`, so booleans pass the int type check:

```python
    if isinstance(value, bool):
        return {"ok": False, "error": f"Invalid type for {key}"}
    # ... existing isinstance check ...
    # Port-specific range validation
    if key == "port" and isinstance(value, int):
        if value != 0 and not (1024 <= value <= 65535):
            return {"ok": False, "error": "Port must be 0 (random) or 1024–65535"}
```

Also pass `port` to the `index()` template context:

```python
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    config = load_config()
    ctx = _terminal_context()
    return templates.TemplateResponse(request, "index.html", {
        "port": config.port,  # Add this
        "terminal_command": config.terminal_command,
        # ... rest unchanged
    })
```

**Test**: Add `test_port_round_trip` in `test_config.py` — write `port = 9876`, reload, assert value. Verify missing key → default 0. Add `test_save_setting_port_bool_rejected` — post `{key: "port", value: true}`, assert rejected. Add `test_save_setting_port_out_of_range` — post port=99999, assert rejected.

#### Implementation (2026-07-05, code: 2ef6774)

Added `port: int = 0` as the first field in the Config dataclass, added `"port": int` to `_SETTING_TYPES`, added a bool guard before the isinstance check in `save_setting` (with comment explaining the Python `isinstance(True, int)` gotcha), added port-specific range validation (0 or 1024-65535), and passed `config.port` to the index template context. Review also identified and fixed a missing bool guard in `load_config()` where TOML `port = true` would have passed `isinstance(True, int)` — added `if isinstance(v, bool) and expected is not bool: continue` before the type check (fix commit: 196cc2c).

**Divergence**: The code commit (2ef6774) includes pre-existing unrelated working-tree changes (~60 lines of session refresh, shift-click selection, Claude Code fallback scan) alongside the Phase 1 changes. These were uncommitted changes from a prior session that the implementation sub-agent staged with `git add`.

### 2. Port binding with fallback [QA]

In `__main__.py:233`, replace the hardcoded `port=0`:

```python
    # Determine port: 0 = random, >0 = attempt static with random fallback
    desired_port = config.port

    def _make_patched_startup(srv, evt):
        """Factory to create a patched startup coroutine for a given server instance."""
        orig = srv.startup
        async def _patched(sockets=None):
            await orig(sockets=sockets)
            evt.set()
        return _patched

    uv_config = uvicorn.Config(app, host="127.0.0.1", port=desired_port, log_level="warning")
    server = uvicorn.Server(uv_config)
    ready_event = threading.Event()
    server.startup = _make_patched_startup(server, ready_event)

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    ready_event.wait(timeout=10)

    # Detect failure: either timeout or thread died (port-in-use exits run() immediately)
    if (not ready_event.is_set() or not server.servers) and desired_port > 0:
        # Static port failed — shut down failed server, fall back to random
        log.warning("Port %d unavailable, falling back to random port", desired_port)
        server.should_exit = True
        server_thread.join(timeout=3)

        uv_config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        server = uvicorn.Server(uv_config)
        ready_event = threading.Event()
        server.startup = _make_patched_startup(server, ready_event)
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        ready_event.wait(timeout=10)

    if not ready_event.is_set() or not server.servers:
        print("ERROR: Server failed to start", file=sys.stderr)
        _remove_pid()
        sys.exit(1)
```

Key fixes vs naive approach:
- `_make_patched_startup` is a factory — each server instance gets its own closure referencing its own `original_startup`.
- Failed server is explicitly shut down (`should_exit=True` + `join`) before retrying, preventing orphaned threads/sockets.
- Check `server_thread.is_alive()` isn't needed: if the thread died from a bind error, `server.servers` will be empty and `ready_event` won't be set — both conditions already trigger the fallback.

#### Implementation (2026-07-05, code: c243b9d)

Replaced hardcoded `port=0` with `desired_port = config.port` and implemented fallback-to-random logic. Uses `_make_patched_startup` factory pattern for per-instance closure safety. On bind failure when `desired_port > 0`: logs warning, sets `should_exit=True`, joins thread (3s timeout with orphan warning if alive), then retries on port 0. Added `log.error` before catastrophic exit path. Review fix commit: 9b26815.

### 3. Banner UI control [QA]

Add a port control group in the topbar of `index.html`, after the peek hotkey group:

```html
<div class="port-group">
  <label class="port-label" for="portMode">Port</label>
  <select class="port-mode" id="portMode" onchange="togglePortInput(this.value)">
    <option value="random" {% if not port %}selected{% endif %}>Random</option>
    <option value="static" {% if port %}selected{% endif %}>Static</option>
  </select>
  <input type="number" id="portValue" class="port-input" min="1024" max="65535"
         aria-label="Port number"
         value="{{ port if port else '' }}"
         style="display:{% if port %}inline-block{% else %}none{% endif %}"
         placeholder="e.g. 49152"
         onkeydown="if(event.key==='Enter')savePort()">
  <button class="port-randomize" id="portRandomize" onclick="randomizePort()"
          aria-label="Randomize port"
          style="display:{% if port %}inline-block{% else %}none{% endif %}"
          title="Pick a random port">🔄</button>
  <button class="port-save-btn" id="portSave" onclick="savePort()"
          style="display:{% if port %}inline-block{% else %}none{% endif %}">Apply</button>
</div>
```

Also add the custom terminal text input to the topbar (migrated from the deleted settings page):

```html
<input type="text" id="customTerminal" class="topbar-input"
       value="{{ terminal_command if terminal_command not in terminal_values else '' }}"
       style="display:{% if terminal_command not in terminal_values %}inline-block{% else %}none{% endif %}"
       placeholder="{cwd} = path, {cmd} = command"
       onchange="fetch('/api/save-setting',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:'terminal_command',value:this.value})})">
```

And update the terminal `<select>` to include the "Custom" option (remove the `{% if value != 'custom' %}` filter).

JavaScript (inline in the existing `<script>` block):

```javascript
function togglePortInput(mode) {
  var show = mode === 'static';
  document.getElementById('portValue').style.display = show ? 'inline-block' : 'none';
  document.getElementById('portRandomize').style.display = show ? 'inline-block' : 'none';
  document.getElementById('portSave').style.display = show ? 'inline-block' : 'none';
}
function randomizePort() {
  var port = 49152 + Math.floor(Math.random() * (65535 - 49152));
  document.getElementById('portValue').value = port;
}
function savePort() {
  var v = parseInt(document.getElementById('portValue').value, 10);
  if (!v || v < 1024 || v > 65535) { showToast('<div class="toast toast-error">Port must be 1024\u201365535<button class="toast-dismiss" onclick="this.parentElement.remove()">\u00d7</button></div>'); return; }
  savePortAndRestart(v);
}
function savePortAndRestart(portValue) {
  // Save, then restart. Show overlay and poll for reconnection.
  fetch('/api/save-setting', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({key: 'port', value: portValue})})
    .then(function() {
      showRestartOverlay(portValue);
      return fetch('/api/restart', {method: 'POST'});
    }).catch(function() { /* server died — expected */ });
}
function showRestartOverlay(newPort) {
  var overlay = document.createElement('div');
  overlay.className = 'restart-overlay';
  overlay.innerHTML = '<div class="restart-msg">Restarting\u2026</div>';
  document.body.appendChild(overlay);
  // Poll new port (or same port for random=0) until responsive
  var target = newPort > 0 ? 'http://127.0.0.1:' + newPort : window.location.origin;
  var attempts = 0;
  var poll = setInterval(function() {
    attempts++;
    if (attempts > 30) { clearInterval(poll); overlay.innerHTML = '<div class="restart-msg">Restart timed out. Refresh manually.</div>'; return; }
    fetch(target + '/api/last-refresh', {mode: 'no-cors'}).then(function() {
      clearInterval(poll);
      window.location.href = target;
    }).catch(function() { /* still down, keep polling */ });
  }, 500);
}
```

When switching to "Random" mode, the dropdown change only toggles visibility — no auto-restart. The user must click "Apply" (which calls `savePortAndRestart(0)`). Add an Apply button for the random mode transition too, shown briefly:

```javascript
function togglePortInput(mode) {
  var show = mode === 'static';
  document.getElementById('portValue').style.display = show ? 'inline-block' : 'none';
  document.getElementById('portRandomize').style.display = show ? 'inline-block' : 'none';
  // Show Apply for both modes when changing from current state
  document.getElementById('portSave').style.display = 'inline-block';
  document.getElementById('portSave').onclick = function() {
    if (mode === 'random') { savePortAndRestart(0); }
    else { savePort(); }
  };
}
```

This prevents accidental restarts from a single dropdown change (review finding #7).

#### Implementation (2026-07-05, code: 78603e1)

Added port control group to topbar with mode select (Random/Static), numeric input (min 1024, max 65535, Enter-key handler), randomize button (49152-65535), and conditional Apply button. JavaScript functions: togglePortInput (shows/hides based on mode), randomizePort (inclusive range), savePort (client-side validation), savePortAndRestart (sequential save→restart), showRestartOverlay (no-cors polling with 15s timeout + dismiss link). CSS added to style.css matching existing topbar patterns. Review fixes: Apply button visibility conditional on port value, randomize range off-by-one, overlay dismiss mechanism. Cycle 2 caught a CORS regression from removing no-cors — restored (opaque response correctly detects cross-port server liveness). Fix commits: f7180bf, 6827d52.

### 4. Delete settings page + migrate custom terminal

The topbar select in `index.html` currently filters out the "Custom" terminal option (`{% if value != 'custom' %}`). Before deleting `settings.html`, restore the custom option:

1. Remove the `{% if value != 'custom' %}` filter from the terminal `<select>` in `index.html`
2. Add an `onchange` handler to show/hide the custom terminal input when "custom" is selected
3. Add the custom terminal text input (see Step 3 HTML above)

Then remove:
- The `settings_page` route handler (`GET /settings`) and `save_settings` function (`POST /api/settings`) from `web.py`
- The file `src/power_atlas/templates/settings.html`
- The test `test_save_settings` in `test_web.py` (exercises the deleted `POST /api/settings` form endpoint — this test uses the legacy `pinned_folders` as `list[str]` format which is already a dead path)

Keep the `POST /api/autostart` endpoint (still used by the topbar toggle).

#### Implementation (2026-07-05, code: 4c6e1fb)

Removed `settings_page` (GET /settings) and `save_settings` (POST /api/settings) routes from web.py. Deleted `settings.html` template. Removed `test_save_settings` from test_web.py (218 tests remain). Migrated custom terminal to topbar: removed `{% if value != 'custom' %}` filter from terminal select, added onchange handler that shows custom input when "custom" selected, added `#customTerminal` text input with conditional visibility, added `terminal_values` to template context for the conditional. Per-phase review deferred to Step 9 — mechanical migration with no new logic.

### 5. Tests and README [QA]

**Tests:**
- `test_config.py`:
  - `test_port_round_trip` — write `port = 9876`, reload, assert value
  - `test_port_missing_defaults_zero` — empty config → port=0
  - `test_port_bool_in_toml_rejected` — write `port = true` in TOML, verify load_config returns default 0 (TOML bool is not int)
- `test_web.py`:
  - `test_save_setting_port_valid` — post port=8080, verify config updated
  - `test_save_setting_port_bool_rejected` — post `{key: "port", value: true}`, assert error response
  - `test_save_setting_port_out_of_range` — post port=99999, assert error
  - `test_save_setting_port_zero_accepted` — post port=0 (random mode), assert success
  - Remove `test_save_settings` (the `POST /api/settings` form test for the deleted endpoint)

**README.md** — add `port` to the Configuration section:

```toml
port = 0  # 0 = random (default), or set e.g. 8080 for a fixed port
```

#### Implementation (2026-07-05, code: b10908a)

Added 7 new tests: 3 in test_config.py (port round-trip, missing defaults to 0, bool in TOML rejected) and 4 in test_web.py (valid port, bool rejected, out-of-range, zero accepted). Updated README.md Configuration section with the `port` field. Total: 225 tests pass. Per-phase review deferred to Step 9.

## Verification

1. `pytest` — all tests pass
2. Run `power-atlas --foreground` with default config → random port (same as before)
3. Set `port = 9876` in config.toml, restart → binds to 9876
4. Set `port = 9876`, have another process occupy that port, restart → falls back to random with log warning visible in `orchestrator.log`
5. Open dashboard → port control visible in topbar, toggle works, randomize button fills a value, Apply triggers restart with reconnection overlay
6. Confirm `/settings` returns 404
7. Confirm custom terminal option appears in topbar dropdown and text input shows/hides correctly
8. Verify `POST /api/save-setting` with `port: true` → rejected; with `port: 99999` → rejected; with `port: 0` → accepted

## Documentation updates

- README.md: add `port` field to Configuration example (Step 5)
- Confirm README Configuration section shows custom terminal is configurable via UI (no longer needs manual TOML editing documented separately)

## Review Log

### 2026-07-05 — Plan Review (via /qplan)

14 findings (7 High, 7 Medium). 12 auto-resolved, 2 noted.

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | High | `index()` handler doesn't pass `config.port` to template context — `UndefinedError`. | Resolved — added `"port": config.port` to Step 1. |
| 2 | High | `isinstance(True, int)` passes — boolean values accepted as port. | Resolved — added explicit bool guard in Step 1. |
| 3 | High | No server-side port range validation. | Resolved — added range check (0 or 1024-65535) in Step 1. |
| 4 | High | Fallback `_patched_startup` closure references wrong server instance. | Resolved — replaced with `_make_patched_startup` factory in Step 2. |
| 5 | High | Port-in-use detection via timeout is fragile; failed server thread not cleaned up. | Resolved — added `should_exit=True` + `join` in Step 2. |
| 6 | High | Browser gets dead connection after restart; no reconnection to new port. | Resolved — added `showRestartOverlay` + polling logic in Step 3. |
| 7 | High | Random mode dropdown immediately restarts without confirmation. | Resolved — both modes require explicit Apply click in Step 3. |
| 8 | Medium | Save-then-restart race condition. | Resolved — JS chains fetch sequentially (save awaits, then restart). |
| 9 | Medium | Orphaned server thread on retry. | Resolved — explicit shutdown before retry in Step 2. |
| 10 | Medium | No UI indication when fallback-to-random fired. | Noted — deferred; requires passing actual-vs-configured port to template, low priority for first pass. |
| 11 | Medium | Missing accessibility labels. | Resolved — added `for`, `aria-label` attributes in Step 3 HTML. |
| 12 | Medium | Custom terminal input only in settings.html — deleting it loses functionality. | Resolved — migrated custom terminal to topbar in Steps 3-4. |
| 13 | Low | Test coverage gaps (bool, fallback). | Resolved — expanded test list in Step 5. |
| 14 | Low | Files table claimed "add port-save-with-restart endpoint" but step-by-step reuses existing. | Resolved — removed misleading claim from Files table. |

### 2026-07-05 — Implementation Review (after Phase 1, persona: Senior engineer, Security auditor, Reliability engineer, Maintainability reviewer)

Implementation health: Green.
4 findings auto-fixed (cycle 1), 0 regressions (cycle 2). QA: PASS (11 probes, 2 adversarial).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `load_config()` has no bool guard — TOML `port = true` passes as int. | Fixed — added `if isinstance(v, bool) and expected is not bool: continue` (196cc2c). |
| 2 | Medium | Bool guard in `save_setting` has no comment explaining WHY. | Fixed — added inline comment about `isinstance(True, int)` (196cc2c). |
| 3 | Medium | `_SETTING_TYPES` key order doesn't match Config field order. | Fixed — reordered to match Config declaration (196cc2c). |
| 4 | Low | Redundant `isinstance(value, int)` in port range check. | Fixed — simplified to `if key == "port":` (196cc2c). |

Noted (not blocking): tests deferred to Phase 5 per plan structure; commit 2ef6774 includes pre-existing unrelated working-tree changes (mixed scope).

### 2026-07-05 — Implementation Review (after Phase 2, persona: Senior engineer, Reliability engineer)

Implementation health: Green.
2 findings auto-fixed, 3 informational (no action). Cycle 2 skipped — auto-fixes add only log lines (3 LOC, purely additive, zero behavior change).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | No log warning when join(timeout=3) times out and thread is orphaned. | Fixed — added `if server_thread.is_alive(): log.warning(...)` (9b26815). |
| 2 | Medium | `ready_event.set()` fires after `server.servers` populated — ordering correct. | No action — reviewer confirmed correct behavior. |
| 3 | Low | No `log.error` on catastrophic failure (both port attempts fail). | Fixed — added `log.error(...)` before stderr print (9b26815). |
| 4 | Low | Negative port values skip fallback branch. | No action — save-setting validation already rejects. |
| 5 | Low | Factory closure captures `srv.startup` eagerly. | No action — verified safe (uvicorn doesn't mutate). |

### 2026-07-05 — Implementation Review (after Phase 3, persona: Senior engineer, End-user advocate)

Implementation health: Green.
4 findings auto-fixed (cycle 1), 1 regression caught and fixed (cycle 2). QA: PASS (code-level; runtime at Step 9b).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | Apply button hidden on initial render in static mode. | Fixed — conditional display matching sibling pattern (f7180bf). |
| 2 | Low | randomizePort excludes 65535 (off-by-one). | Fixed — added +1 to range calculation (f7180bf). |
| 3 | High | no-cors opaque response causes premature redirect on cross-port change. | Fixed then reverted — no-cors is correct (opaque=reachable, error=down) (6827d52). |
| 4 | High | Timeout overlay permanently blocks page with no dismiss. | Fixed — added Dismiss link (f7180bf). |
| 5 | Medium | Custom terminal migration absent (expected in Phase 4). | Not a divergence — Phase 4 owns this per plan structure. |

### 2026-07-05 — Post-Implementation Review

Overall implementation health: Green.
Personas: Senior engineer, Reliability engineer.
6 findings (0 High, 0 Medium, 6 Low).
QA verification: PASS (7 port tests, integration checks, deleted settings 404 verified).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Low | `_SETTING_TYPES` includes unreachable list entries. | Pre-existing, no action. |
| 2 | Low | Phase 1 commit mixed with unrelated changes. | Noted, no runtime impact. |
| 3 | Low | Timeout overlay gives no fallback port indication. | Deferred (plan review #10). |
| 4 | Low | No debounce on Apply button click. | UX polish, acceptable for v1. |
| 5 | Low | No load-time port range validation in load_config. | Self-heals via fallback, acceptable. |
| 6 | Low | api_restart response may not reach client. | Handled by catch() + overlay. |

All success criteria verified:
- SC1: `port` field in config.toml (int, default 0, backward-compatible) ✓
- SC2: Index page port control (mode toggle, numeric input, randomize, Apply) ✓
- SC3: Saving port triggers restart via /api/restart ✓
- SC4: Static port occupied → fallback to random with log warning ✓
- SC5: /settings route and settings.html removed ✓

Invoked on fully-executed plan; performed standalone holistic review.
