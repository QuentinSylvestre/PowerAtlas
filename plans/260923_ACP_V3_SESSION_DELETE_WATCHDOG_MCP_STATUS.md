# ACP v3: Session Delete, Watchdog, and MCP Status Panel

> **Date**: 2026-09-23
> **Status**: In Progress  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Three improvements to the ACP supervisor and /acp UI — `session/delete` wire close, crash-detection watchdog, and `_kiro/mcp/status` toolbar panel with OAuth connect flow.
> **Estimated effort**: 3–5 days

---

## Intent

### Problem statement & desired outcomes

Three independent gaps in the ACP supervisor and /acp UI, all confirmed buildable on kiro-cli v3 2.23.1:

**Session close (now unblocked).** `CLOSE_METHOD = None` since the v3 engine cutover; the idle sweeper's "close" only releases PowerAtlas's own data structures — kiro-cli's internal session registry is never told. Probed 2026-09-23: `session/delete` returns `{}` on v3 2.23.1 and removes the session from kiro-cli's registry. `sessionCapabilities.delete` is advertised in every `initialize` response measured.

**Supervisor crash detection.** When kiro-cli.exe crashes mid-session, bun.exe and node.exe (grandchildren in the tree `kiro-cli.exe → bun.exe → node.exe`) inherit the stdout pipe write handle and keep the reader thread blocked on `read1()` indefinitely. The only current crash-detection path is `_send → proc.poll()`, which fires only when a client writes — an unattended session stalls silently until `PROMPT_SILENCE_SECONDS` (1800 s) triggers a cancel. A periodic asyncio watchdog polling `proc.poll()` every 5 s detects the crash promptly.

**MCP status panel.** `_kiro/mcp/status` notifications have been arriving from kiro-cli on every session start and periodically (~50 min) since v3 launched. PowerAtlas has been logging them as unhandled and discarding them. Each notification carries the full server list with connection state (`connected`/`connecting`/`failed`/`disabled`), a `tools` array when connected, and `authorizationUrl` when `failedAuthorization: true`. The feature is fully buildable without any new kiro-cli support.

### Success criteria

- **SC-1**: `session/delete` is sent as the wire-close call before local cleanup whenever `sessionCapabilities.delete` was advertised in the last `initialize` response and the agent is alive. `-32000` and all other `AgentRejected` responses are logged as WARNING; other `AcpError` (including timeout) is silently swallowed; local cleanup always proceeds. Uses `CLOSE_TIMEOUT_SECONDS = 5.0` (not `REQUEST_TIMEOUT_SECONDS`) to prevent the sweeper from stalling at capacity. If `sessionCapabilities.delete` is absent from `initialize`, `CLOSE_METHOD` falls back to `None` and the behavior is identical to today. `_sweep_once` docstring updated to describe v3 actual behavior (no per-session MCP process tree; releases session from kiro-cli's registry).

- **SC-2**: `WATCHDOG_INTERVAL_SECONDS = 5.0` constant in `acp.py`. `_watchdog_loop()` asyncio coroutine: sleep-first, snapshot `self._proc`, call `proc.poll()`, call `self._on_agent_death(proc)` if non-None result. `start_watchdog()` module-level function returning an `asyncio.Task`. Started from `web.py` lifespan alongside `start_sweeper()`; cancelled and awaited in the same `finally` block. `WATCHDOG_INTERVAL_SECONDS` added to `acp_fast` fixture save/restore dict.

- **SC-3**: `"mcp_servers"` added to `SERVER_TYPES`. `_kiro/mcp/status` notification handled: `servers` extracted, stored as `meta["mcpServers"]` (replace-all), broadcast as `envelope("mcp_servers", {"servers": servers}, session_id)` via `_registry.broadcast`. Replayed to new subscribers in both `_handle_subscribe` and `_handle_new`.

- **SC-4**: Persistent MCP indicator in /acp's session toolbar. Compact state: icon + connected-server count, or warning indicator when any server is `"failed"` or has `failedAuthorization: true`. Expanded state (toggle): server list with name, status badge per server, and "Connect" button for `failedAuthorization: true` entries where `authorizationUrl` is a valid `https://` URL. Connect button calls `window.open(entry.authorizationUrl, '_blank', 'noopener,noreferrer')` only after validating the scheme is `https://`. Panel updates on every `mcp_servers` frame. Hidden until first `mcp_servers` frame arrives. Resets on session change. Styles match existing `/acp` patterns.

### Scope boundaries & non-goals

- MCP server configuration, enable/disable, or modifying which servers connect (`mcpServers: []` continues to be passed to `session/new`)
- The existing `display_error` → `agent_error` transcript path — kept as-is
- `/mcp` slash-command invocation (three hypotheses probed, all failed; see `plans/CLOSED_INVESTIGATIONS.md`)
- Newly discovered notification types `_kiro/governance/state`, `_kiro/tools/didChange`, `_kiro/powers/items_changed` — continue to log as INFO

---

## 1) Current State

**session/delete:**
- `CLOSE_METHOD: "str | None" = None` at `acp.py:645`. Comment at `acp.py:643–650` states "No JSON-RPC session-close method exists (probe AS-5, 2026-08-19)."
- `close_session()` at `acp.py:4912`: no wire call, executes per-session local cleanup only. Docstring at `acp.py:4916–4919` notes `CLOSE_METHOD = None`.
- `ensure_started()` at `acp.py:2861`: reads `initialize` response but does not read `sessionCapabilities.delete`; logs `agentInfo.version` and `protocolVersion` only (`acp.py:2967–2972`).
- `_sweep_once` docstring at `acp.py:6568–6582` describes "`_kiro.dev/session/terminate` frees 3 processes / 161 MB" — v2 behavior, no longer true; v3 uses one shared process tree.
- Probed 2026-09-23 on kiro-cli v3 2.23.1: `session/delete` → `{}` on success; `-32000` if already deleted. Advertised at `agentCapabilities.sessionCapabilities.delete` in `initialize` response.

**Supervisor crash detection:**
- `_reader_loop()` at `acp.py:3273`: reads `proc.stdout` via `stream.read1(READ_BLOCK_BYTES)`. On EOF → `finally: self._post(self._on_agent_death, proc)`.
- Crash blind spot: kiro-cli.exe spawns bun.exe (`acp.py:4519–4531` spawns `kiro-cli.exe`; bun.exe is its child; node.exe is bun.exe's child — confirmed by process inspection). Both bun.exe and node.exe inherit the stdout pipe write handle. When kiro-cli.exe crashes, the pipe stays open; `read1()` blocks indefinitely.
- Current crash detection: only `_send()` at `acp.py:3265` checks `proc.poll() is not None`. Fires only when a client writes.
- `_on_agent_death()` idempotency guard at `acp.py:3818`: `if proc is not self._proc: return`. Safe for double-invocation.
- `_sweep_loop` at `acp.py:6580`; `start_sweeper()` at `acp.py:6608`; wired at `web.py:549`; cancelled at `web.py:553`; gathered at `web.py:562`. Template for watchdog.
- `acp_fast` fixture save/restore at `test_web.py:8887–8893`. Must include `WATCHDOG_INTERVAL_SECONDS`.

**MCP notification handling:**
- `_kiro/mcp/status` falls through to `log.info("ACP notification %s ...", ...)` at `acp.py:4383`.
- Notification payload (measured): `{"sessionId": "...", "servers": [{name, status, authType?, failedAuthorization?, authorizationUrl?, tools?, _meta?}]}`. Fires immediately after `session/new` completes and periodically (~50 min). `session_id` is always present → SC-1 early-frame buffer (`acp.py:4065–4079`) handles arrival before `sessions[sid]` is registered.
- `SERVER_TYPES` frozenset at `acp.py:171–202` — `"mcp_servers"` absent.
- Commands/skills resend in `_handle_subscribe` at `acp.py:5773–5778`; same in `_handle_new` at `acp.py:5955–5960`. Template for `mcpServers` resend.
- `setSessionCommands` at `composer-chrome.js:560`; `setSessionSkills` at `composer-chrome.js:568`; `resetCommandPalette` at `composer-chrome.js:579`. Templates for MCP equivalents.
- `commands` frame handler at `acp.html:5051`; `skills` frame handler at `acp.html:5057`. Templates for `mcp_servers` frame handler.

## 2) Goal

Implement `session/delete` as the v3 wire-close method with graceful degradation; add a 5 s `proc.poll()` watchdog that detects kiro-cli.exe crashes before a client write; and surface `_kiro/mcp/status` notifications as a persistent, polished MCP status panel in /acp's session toolbar with per-server status badges and OAuth connect buttons.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Wire-close method for v3 | `session/delete` read from `sessionCapabilities.delete` at runtime | Hardcode as module constant | Runtime read degrades gracefully when capability absent; survives future kiro-cli builds that remove the method |
| Error handling for `session/delete` | `AgentRejected` logged as WARNING; other `AcpError` silently swallowed; local cleanup always proceeds | Re-raise on unknown codes | Local cleanup is idempotent and essential; a failed wire call should never block it |
| Close timeout | `CLOSE_TIMEOUT_SECONDS = 5.0` separate from `REQUEST_TIMEOUT_SECONDS` | Reuse `REQUEST_TIMEOUT_SECONDS` (90 s) | 8 sessions × 90 s = 720 s potential sweeper stall at capacity; 5 s is adequate for a local RPC to a co-located process |
| Crash detection mechanism | Periodic `proc.poll()` asyncio watchdog (Option A) | Pipe inheritance prevention via `CreateProcessW + PROC_THREAD_ATTRIBUTE_HANDLE_LIST` (Option B) | Option B is uncertain: kiro-cli.exe re-inherits stdout to bun.exe/node.exe via its own CreateProcess call, outside PowerAtlas's control. Option A is safe, provably correct, and follows the existing `_sweep_loop` pattern |
| Watchdog interval | 5 s | 1 s (too aggressive), 60 s (same as sweeper, long unattended stall) | 5 s detects crash within one polling interval; short enough to reduce user-visible stall, long enough not to thrash `proc.poll()` |
| MCP panel placement | Persistent toolbar indicator with compact + expanded toggle | Inline transcript event only | Toolbar is always visible; user sees auth-required state before starting a turn. Inline-only would only surface the issue after blocking a turn |
| `_pending_mcp_servers` buffer | Not needed — SC-1 `_pending_early_frames` handles arrival before session registration | Separate pending buffer (as in `_pending_commands`) | `_kiro/mcp/status` always carries `sessionId`; SC-1 early-frame buffer re-dispatches through `_on_notification` after registration. The `_pending_commands` buffer exists only because `_kiro.dev/commands/available` has no `sessionId` |
| `_on_notification` event-loop serialization | `_on_notification` runs on the event loop (via `call_soon_threadsafe`); replace-all to `meta["mcpServers"]` is safe without locking | External lock | Python's asyncio event loop is single-threaded; all notification handlers run sequentially. No race condition possible between two rapid `_kiro/mcp/status` notifications. |
| One plan vs three plans | One combined plan | Three separate plans | User preference; phases are independent within the plan |

## 4) External Dependencies & Costs

### Required external changes

| Category | Change needed | Owner | Status |
|---|---|---|---|
| CI/CD | None | — | N/A |
| IAM / Permissions | None | — | N/A |
| Cloud resources | None | — | N/A |
| Data migration / backfill | None | — | N/A |
| Rollout / cutover | None — code-only | — | N/A |
| Cleanup after rollback window | None | — | N/A |
| Secrets / Env vars | None | — | N/A |
| DNS / Networking | None | — | N/A |

### Cost impact

None. No new infrastructure, processes, or API calls.

## 5) Implementation Phases

### Phase 1: session/delete wire close [QA]

**Goal**: Replace `CLOSE_METHOD = None` with a runtime-read `session/delete` capability; update `close_session()` to send the wire call before local cleanup; update stale docstrings.

**Covers**: SC-1

**File scope**: `src/power_atlas/acp.py`, `tests/test_web.py`

> **Note on line references**: Line numbers in this section are approximate; the file has grown since these were recorded. Use function/symbol names (e.g. grep for `def close_session`, `def ensure_started`) to locate insertion points rather than line numbers.

**Changes:**

Add `CLOSE_TIMEOUT_SECONDS: Final[float] = 5.0` near `SWEEP_INTERVAL_SECONDS` (acp.py). Using a short dedicated timeout prevents the sweeper from stalling up to 720 s at max-session capacity if kiro-cli is slow.

Add `self._close_method: str | None = None` to `_Supervisor.__init__` (after `self._start_lock` field):
```python
self._close_method: str | None = None  # Set in ensure_started from sessionCapabilities.delete
```

In `ensure_started()`, after `self._ready = True` (`acp.py:2907`), add:
```python
_caps = (result or {}).get("agentCapabilities") or {}
_session_caps = _caps.get("sessionCapabilities") or {}
if _session_caps.get("delete") is not None:
    self._close_method = "session/delete"
    log.info("ACP: sessionCapabilities.delete present — wire close enabled")
else:
    self._close_method = None
    log.info("ACP: sessionCapabilities.delete absent — wire close unavailable (local cleanup only)")
```

In `close_session()` (`acp.py:4912`), add the wire call before the local cleanup block. Replace the current docstring comment "No wire close for v3 (CLOSE_METHOD is None)." and add before `self.sessions.pop(...)`:
```python
# Wire close: session/delete on kiro-cli v3 2.23.1+ (read from sessionCapabilities.delete
# at initialize time). -32000 = already gone; any error logged as WARNING, never re-raised —
# local cleanup must always run regardless.
if self._close_method and self.alive():
    try:
        await self._request(
            self._close_method, {"sessionId": session_id},
            timeout=CLOSE_TIMEOUT_SECONDS)
    except AgentRejected as exc:
        # -32000 = already gone; any other code is unexpected — warn but proceed.
        log.warning(
            "ACP: %s for session %s returned: %s — proceeding with local cleanup",
            self._close_method, session_id, exc)
    except AcpError:
        # Covers AgentDied, asyncio.TimeoutError (wrapped), and other channel errors.
        pass  # agent went away or timed out mid-close; local cleanup is correct
```

Update `CLOSE_METHOD` module comment (`acp.py:643–650`) to describe it as the historical constant, superseded by `self._close_method` at runtime.

Update `_sweep_once` docstring (`acp.py:6568–6582`): remove "frees the session's own MCP processes (~3 processes / ~161 MB on kiro-cli 2.16.0…)" and replace with: "On kiro-cli v3, all sessions share one process tree — `session/delete` releases the session from kiro-cli's registry; no per-session processes are freed."

Also update `close_session()` docstring (`acp.py:4916–4919`) to describe the wire call.

**Exit criteria**:
- [x] `CLOSE_TIMEOUT_SECONDS = 5.0` constant added to `acp.py`
- [x] `self._close_method` field present in `_Supervisor.__init__`
- [x] `ensure_started()` reads `sessionCapabilities.delete` and sets `self._close_method`; uses truthiness test (`if _session_caps.get("delete"):` not `is not None`); log line confirms enable/disable
- [x] `close_session()` attempts `self._close_method` wire call when set and `alive()` is True; uses `CLOSE_TIMEOUT_SECONDS`
- [x] `AgentRejected` logged as WARNING; local cleanup always runs
- [x] Other `AcpError` (including timeout) silently swallowed; local cleanup always runs
- [x] `_sweep_once` docstring no longer claims "frees 3 processes / 161 MB"
- [x] `pytest tests/test_web.py -k "close_session" --timeout=60` passes with new tests covering: (a) wire call sent when `_close_method` set and `alive()`, (b) `-32000` response swallowed (local cleanup runs), (c) `sessionCapabilities.delete` absent → `_close_method` stays `None`

### Phase 2: Crash detection watchdog [QA]

**Goal**: Add a 5 s periodic `proc.poll()` watchdog that detects kiro-cli.exe crashes independently of the reader thread or client writes.

**Covers**: SC-2

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/web.py`, `tests/test_web.py`

**Changes:**

Add constant near `SWEEP_INTERVAL_SECONDS` (`acp.py:786`):
```python
WATCHDOG_INTERVAL_SECONDS: Final[float] = 5.0
_WATCHDOG_MAX_ERRORS = 3  # consecutive exceptions before the watchdog cancels itself
```

Add `_watchdog_loop()` as a method on `_Supervisor`, near `_sweep_loop`:
```python
async def _watchdog_loop(self) -> None:
    """Detect a dead kiro-cli.exe before the reader thread sees EOF.

    kiro-cli.exe spawns bun.exe which spawns node.exe; both grandchildren
    inherit the stdout pipe write handle. When kiro-cli.exe crashes the
    reader thread's ``read1()`` blocks indefinitely rather than seeing EOF.
    ``proc.poll()`` on the kiro-cli.exe Popen object correctly detects the
    crash regardless of grandchild state, because ``_proc`` refers to the
    wrapper, not the node subprocess.

    Calls ``_on_agent_death`` directly (not via ``_post``) because this
    coroutine already runs on the event loop. ``_post`` is only for the
    OS-thread reader.

    Sleep-first: avoids a spurious check immediately after spawn, same
    discipline as ``_sweep_loop``.
    """
    _errors = 0
    while True:
        try:
            await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
            proc = self._proc
            if proc is None or not self._ready:
                _errors = 0
                continue
            rc = proc.poll()  # bind once — avoids double syscall in log format
            if rc is not None:
                log.warning(
                    "ACP watchdog: kiro-cli.exe (pid %d) has exited "
                    "(rc=%s) before the reader thread saw EOF; "
                    "triggering agent death cleanup",
                    proc.pid, rc)
                self._on_agent_death(proc)
            _errors = 0
        except asyncio.CancelledError:
            raise
        except Exception:
            _errors += 1
            log.exception(
                "ACP watchdog: unexpected error (consecutive=%d/%d)",
                _errors, _WATCHDOG_MAX_ERRORS)
            if _errors >= _WATCHDOG_MAX_ERRORS:
                log.error(
                    "ACP watchdog: too many consecutive errors — "
                    "stopping watchdog; crash detection inactive")
                return
```

Add `start_watchdog()` module-level function near `start_sweeper()` (`acp.py:6608`):
```python
def start_watchdog() -> asyncio.Task:
    """Start the crash-detection watchdog. Called from web.py lifespan.

    Returns the asyncio.Task so the caller can cancel and await it.
    """
    return asyncio.create_task(
        _supervisor._watchdog_loop(), name="acp-watchdog")
```

In `web.py` lifespan (`web.py:549`), add watchdog start alongside sweeper:
```python
sweeper = acp.start_sweeper() if acp is not None else None
watchdog = acp.start_watchdog() if acp is not None else None
```

In the `finally` block, add watchdog cancel (before `asyncio.gather`):
```python
if sweeper is not None:
    sweeper.cancel()
if watchdog is not None:
    watchdog.cancel()
await asyncio.gather(
    *(t for t in (task, sweeper, watchdog) if t is not None),
    return_exceptions=True)
```

Add `WATCHDOG_INTERVAL_SECONDS` to `acp_fast` fixture save/restore dict (`test_web.py:8887–8893`):
```python
_saved["WATCHDOG_INTERVAL_SECONDS"] = acp.WATCHDOG_INTERVAL_SECONDS
acp.WATCHDOG_INTERVAL_SECONDS = 0.01
# ... in teardown:
acp.WATCHDOG_INTERVAL_SECONDS = _saved["WATCHDOG_INTERVAL_SECONDS"]
```

**Exit criteria**:
- [x] `WATCHDOG_INTERVAL_SECONDS = 5.0` and `_WATCHDOG_MAX_ERRORS = 3` constants present in `acp.py`
- [x] `_watchdog_loop()` implemented: sleep-first, `rc = proc.poll()` bound once, `_on_agent_death` call on non-None rc, `CancelledError` re-raised, consecutive-error counter halts watchdog after 3 strikes
- [x] Comment in `_watchdog_loop` explains why `_on_agent_death` is called directly (not via `_post`)
- [x] `start_watchdog()` module-level function returns `asyncio.Task`
- [x] `web.py` lifespan starts watchdog alongside sweeper, cancels and awaits it in `finally`
- [x] `WATCHDOG_INTERVAL_SECONDS` in `acp_fast` save/restore
- [x] `pytest tests/test_web.py -k "watchdog" --timeout=60` passes with tests covering: (a) `proc.poll()` returns non-None → `_on_agent_death` fires within one tick; (b) double-fire is idempotent (second call after `_detach` sets `_proc = None`); (c) `_proc is None` → watchdog skips without error

### Phase 3: MCP notification extraction and broadcast [QA]

**Goal**: Handle `_kiro/mcp/status` notifications, store server list in `meta["mcpServers"]`, broadcast as `mcp_servers` frame.

**Covers**: SC-3 (extraction and broadcast)

**File scope**: `src/power_atlas/acp.py`, `tests/test_web.py`

**Changes:**

Add `"mcp_servers"` to `SERVER_TYPES` (`acp.py:171`):
```python
SERVER_TYPES = frozenset({
    ...,
    "mcp_servers",  # _kiro/mcp/status notification; see _on_notification
})
```

Add handler in `_on_notification`, before the `_kiro.dev/clear/status` handler and the fallthrough `log.info` at `acp.py:4371`. The SC-1 early-frame buffer (`acp.py:4065–4079`) already handles arrival before session registration — no separate pending buffer needed.

```python
if method == "_kiro/mcp/status":
    # Fired by kiro-cli on session/new completion and periodically (~50 min).
    # Carries the full server list; replace-all semantics (not delta).
    # SC-1 early-frame buffer handles arrival before sessions[sid] is set.
    servers = list(params.get("servers") or [])[:MAX_COMMANDS_COUNT]
    if isinstance(session_id, str) and session_id in self.sessions:
        self.sessions[session_id]["mcpServers"] = servers
        _registry.broadcast(
            session_id,
            envelope("mcp_servers", {"servers": servers}, session_id))
    return
```

Note: `MAX_COMMANDS_COUNT = 200` (`acp.py:224`) is the existing cap used for commands/skills; reuse here to bound the stored list.

**Exit criteria**:
- [x] `"mcp_servers"` present in `SERVER_TYPES`
- [x] `_kiro/mcp/status` handler placed before the `log.info` fallthrough
- [x] `meta["mcpServers"]` set on notification; replace-all on repeated notifications
- [x] `_registry.broadcast` fires with `"mcp_servers"` frame type
- [x] Handler returns without falling through to `log.info` — no duplicate log line for `_kiro/mcp/status`
- [x] Notification arriving before session registration: SC-1 buffer holds it and replays after registration (verify via existing SC-1 buffer tests, no new code needed)
- [x] `pytest tests/test_web.py -k "mcp_status or mcp_servers" --timeout=60` passes with tests covering: (a) notification stored in `meta["mcpServers"]`; (b) broadcast frame type is `"mcp_servers"`; (c) `servers` list capped at `MAX_COMMANDS_COUNT`; (d) replace-all on second notification

### Phase 4: MCP replay paths [QA]

**Goal**: Add `meta["mcpServers"]` resend to `_handle_subscribe` and `_handle_new`, matching the commands/skills pattern.

**Covers**: SC-3 (replay)

**File scope**: `src/power_atlas/acp.py`, `tests/test_web.py`

**Changes:**

In `_handle_subscribe` (`acp.py:5773–5778`), after the skills resend block, add (inside the `if meta is not None:` guard that already encloses the commands/skills resend):
```python
mcp_servers = meta.get("mcpServers")
if mcp_servers is not None:
    conn.send(envelope("mcp_servers", {"servers": mcp_servers}, session_id))
```

In `_handle_new` (`acp.py:5955–5960`), after the skills resend block, add (inside the same `if meta is not None:` guard):
```python
mcp_servers = meta.get("mcpServers")
if mcp_servers is not None:
    conn.send(envelope("mcp_servers", {"servers": mcp_servers}, session_id))
```

Both sites mirror the pattern at `acp.py:5773–5778` / `acp.py:5955–5960` exactly, placed inside the existing `if meta is not None:` block that already guards the commands/skills resend. Add inline comment: `# See also: commands/skills resend above — same pattern.`

**Exit criteria**:
- [ ] `_handle_subscribe` resends `meta.get("mcpServers")` when non-None
- [ ] `_handle_new` resends `meta.get("mcpServers")` when non-None
- [ ] Neither site resends when `meta["mcpServers"]` is absent (session never received a `_kiro/mcp/status`) — indicator correctly stays hidden
- [ ] `pytest tests/test_web.py -k "mcp_servers" --timeout=60` passes with new tests: (a) subscribe when `meta["mcpServers"]` set → `mcp_servers` frame delivered; (b) subscribe when absent → no `mcp_servers` frame; (c) `_handle_new` mirrors same behavior

### Phase 5: MCP toolbar HTML/CSS [QA]

**Goal**: Add the MCP status indicator DOM element to the session toolbar and its CSS rules to `style.css`. No JavaScript in this phase.

**Covers**: SC-4 (structure and styles)

**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/static/style.css`

**Changes (acp.html):**

Locate the session toolbar element (search for context-window meter / `acpContextBar` near the session header). Add adjacent to it:
```html
<!-- MCP server status indicator. Shown once first mcp_servers frame arrives.
     JS (composer-chrome.js setSessionMcpServers) drives hidden/visible and list content. -->
<div class="acp-mcp-indicator" id="acpMcpIndicator" hidden>
  <button class="acp-icon-btn acp-mcp-toggle" id="acpMcpToggle"
          type="button" title="MCP servers" aria-expanded="false"
          aria-controls="acpMcpPanel">
    <svg class="acp-mcp-icon" width="14" height="14" viewBox="0 0 16 16"
         fill="none" aria-hidden="true">
      <!-- Hexagon outline representing a server node -->
      <path d="M8 1L14.5 4.75V11.25L8 15L1.5 11.25V4.75L8 1Z"
            stroke="currentColor" stroke-width="1.5"/>
    </svg>
    <span class="acp-mcp-compact" id="acpMcpCompact"></span>
  </button>
  <div class="acp-mcp-panel" id="acpMcpPanel" hidden role="tooltip">
    <ul class="acp-mcp-list" id="acpMcpList" aria-label="MCP servers"></ul>
  </div>
</div>
```

**Changes (style.css):**

Add to the `/acp` section (near `.acp-context-bar` or similar toolbar styles):
```css
/* MCP server status indicator */
.acp-mcp-indicator {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.acp-mcp-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.75rem;
  line-height: 1;
  color: inherit;
}

.acp-mcp-toggle[aria-expanded="true"] ~ .acp-mcp-panel { display: block; }

.acp-mcp-toggle.acp-mcp-warn { color: var(--color-error, #ef4444); }
.acp-mcp-toggle.acp-mcp-caution { color: var(--color-warning, #f59e0b); }

.acp-mcp-panel {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 100;
  min-width: 220px;
  max-width: 340px;
  background: var(--surface-2, #1e1e2e);
  border: 1px solid var(--border, rgba(255,255,255,0.08));
  border-radius: 6px;
  padding: 8px 0;
  box-shadow: 0 4px 16px rgba(0,0,0,0.4);
}

.acp-mcp-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.acp-mcp-server {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  font-size: 0.8125rem;
}

.acp-mcp-server-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* Status badges — reuse dashboard colour tokens */
.acp-mcp-badge { display: inline-block; width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.acp-mcp-badge-connected  { background: var(--color-success, #22c55e); }
.acp-mcp-badge-connecting { background: var(--color-warning, #f59e0b); }
.acp-mcp-badge-failed     { background: var(--color-error,   #ef4444); }
.acp-mcp-badge-disabled   { background: var(--color-muted,   #6b7280); }

.acp-mcp-connect-btn {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 0.6875rem;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid var(--border, rgba(255,255,255,0.08));
  background: transparent;
  color: var(--color-accent, #6366f1);
  cursor: pointer;
  white-space: nowrap;
}
.acp-mcp-connect-btn:hover { background: rgba(99,102,241,0.15); }
```

Verify existing CSS variable names in `style.css` before finalising the snippet above — align with whatever `--color-success`, `--color-error`, etc. are actually called in the file. If different, use the actual names.

Also add this rule to prevent `display: inline-flex` from overriding the `hidden` attribute (the UA `[hidden] { display: none }` rule has lower specificity than author rules):
```css
/* Prevent .acp-mcp-indicator { display: inline-flex } from overriding [hidden] */
#acpMcpIndicator[hidden] { display: none !important; }
```

**Exit criteria**:
- [ ] `#acpMcpIndicator` element present in `acp.html` with `hidden` attribute (starts hidden)
- [ ] `#acpMcpToggle`, `#acpMcpCompact`, `#acpMcpPanel`, `#acpMcpList` IDs present
- [ ] `aria-expanded` and `aria-controls` on toggle button
- [ ] CSS rules for `.acp-mcp-indicator`, `.acp-mcp-badge-{connected,connecting,failed,disabled}`, `.acp-mcp-connect-btn` present in `style.css`
- [ ] Hard reload (`Ctrl+Shift+R`) shows the new element in the toolbar (confirm via Playwright snapshot — element hidden, correct structure present)
- [ ] `#acpMcpIndicator[hidden] { display: none !important; }` rule in `style.css` (prevents inline-flex from overriding the `hidden` attribute)
- [ ] CSS colour variables match those already used in the file (no new undefined variables)

### Phase 6: MCP JavaScript — frame handler and panel logic [QA]

**Goal**: Wire the `mcp_servers` frame into the MCP indicator; implement `setSessionMcpServers`, `_renderMcpIndicator`, connect button; reset on session change.

**Covers**: SC-4 (full behavior)

**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/static/composer-chrome.js`

**Changes (acp.html frame dispatch):**

In the frame type dispatch (near `commands` at `acp.html:5051`, `skills` at `acp.html:5057`), add:
```javascript
if (type === 'mcp_servers') {
    setSessionMcpServers(payload && payload.servers);
    return;
}
```

Also wire the toggle button click to show/hide the panel (add near DOMContentLoaded or where other toolbar buttons are wired):
```javascript
var acpMcpToggleEl = document.getElementById('acpMcpToggle');
var acpMcpPanelEl  = document.getElementById('acpMcpPanel');
if (acpMcpToggleEl && acpMcpPanelEl) {
    acpMcpToggleEl.addEventListener('click', function() {
        var expanded = acpMcpToggleEl.getAttribute('aria-expanded') === 'true';
        acpMcpToggleEl.setAttribute('aria-expanded', !expanded);
        acpMcpPanelEl.hidden = expanded;
    });
    // Close panel on outside click
    document.addEventListener('click', function(e) {
        if (!acpMcpToggleEl.contains(e.target) && !acpMcpPanelEl.contains(e.target)) {
            acpMcpToggleEl.setAttribute('aria-expanded', 'false');
            acpMcpPanelEl.hidden = true;
        }
    });
}
```

**Changes (composer-chrome.js):**

Add module-level variable near `sessionCommands` / `sessionSkills` (`composer-chrome.js:297`):
```javascript
var sessionMcpServers = null;  // null = no notification yet; array = known server list
```

Add `setSessionMcpServers` and `_renderMcpIndicator` near `setSessionCommands` (`composer-chrome.js:560`):
```javascript
function setSessionMcpServers(list) {
    sessionMcpServers = (Array.isArray(list) && list.length > 0) ? list : (list && list.length === 0 ? [] : null);
    _renderMcpIndicator();
}

function _renderMcpIndicator() {
    var indicatorEl = document.getElementById('acpMcpIndicator');
    var compactEl   = document.getElementById('acpMcpCompact');
    var listEl      = document.getElementById('acpMcpList');
    if (!indicatorEl) return;

    if (!sessionMcpServers) {
        indicatorEl.hidden = true;
        return;
    }

    indicatorEl.hidden = false;
    var toggleEl = document.getElementById('acpMcpToggle');

    // Count connected servers; flag failed / auth-needed state
    var connected = 0, needsAction = false;
    (sessionMcpServers || []).forEach(function(srv) {
        if (srv.status === 'connected') connected++;
        if (srv.status === 'failed' || srv.failedAuthorization) needsAction = true;
    });

    // Compact state
    if (compactEl) {
        compactEl.textContent = connected + ' connected';
    }
    if (toggleEl) {
        toggleEl.classList.toggle('acp-mcp-warn', needsAction);
        toggleEl.classList.toggle('acp-mcp-caution', !needsAction && connected < (sessionMcpServers || []).filter(function(s) { return s.status !== 'disabled'; }).length);
        toggleEl.title = needsAction ? 'MCP servers — action needed' : ('MCP servers — ' + connected + ' connected');
    }

    // Expanded list
    if (listEl) {
        listEl.innerHTML = '';
        (sessionMcpServers || []).forEach(function(srv) {
            var li = document.createElement('li');
            li.className = 'acp-mcp-server';

            var badge = document.createElement('span');
            var _VALID_STATUSES = {connected:1, connecting:1, failed:1, disabled:1};
            var safeStatus = _VALID_STATUSES[srv.status] ? srv.status : 'disabled';
            badge.className = 'acp-mcp-badge acp-mcp-badge-' + safeStatus;
            li.appendChild(badge);

            var nameEl = document.createElement('span');
            nameEl.className = 'acp-mcp-server-name';
            nameEl.textContent = srv.name || '?';
            li.appendChild(nameEl);

            if (srv.failedAuthorization && srv.authorizationUrl) {
                var url = srv.authorizationUrl;
                // Security: only open https:// URLs; reject javascript:, file:, data:, etc.
                if (typeof url !== 'string' || !url.startsWith('https://')) {
                    url = null;
                }
                if (url) {
                    var btn = document.createElement('button');
                    btn.className = 'acp-mcp-connect-btn';
                    btn.type = 'button';
                    btn.textContent = 'Connect';
                    btn.addEventListener('click', function() {
                        window.open(url, '_blank', 'noopener,noreferrer');
                    });
                    li.appendChild(btn);
                }
            }

            listEl.appendChild(li);
        });
    }
}
```

Add MCP reset to `resetCommandPalette()` (`composer-chrome.js:579`):
```javascript
function resetCommandPalette() {
    sessionCommands = [];
    sessionSkills = [];
    sessionMcpServers = null;  // added
    _renderMcpIndicator();     // hide indicator
    // ... existing reset code (hide dropdown, etc.) ...
}
```

**Exit criteria**:
- [ ] `mcp_servers` frame handler wired in `acp.html` frame dispatch
- [ ] `setSessionMcpServers` and `_renderMcpIndicator` exported/accessible from `composer-chrome.js`
- [ ] Toggle button opens/closes panel; outside click closes panel
- [ ] Compact state shows connected-server count
- [ ] `acp-mcp-warn` class on toggle when any `failedAuthorization` or `failed` status present
- [ ] Expanded list renders one `<li>` per server with status badge
- [ ] `failedAuthorization: true` + `authorizationUrl` → "Connect" button rendered; click calls `window.open(url, '_blank', 'noopener,noreferrer')`
- [ ] `authorizationUrl` validated as `https://` scheme before `window.open`; non-https or non-string values do not open a window
- [ ] `srv.status` validated against `{connected, connecting, failed, disabled}` before `className` construction; unknown values fall back to `disabled`
- [ ] `sessionMcpServers = null` on `resetCommandPalette`; indicator hidden
- [ ] Hard reload + start a kiro-cli session (Playwright): indicator appears in toolbar, shows server list, Connect button present for Atlassian if auth needed

### Phase 7: MCP acp_page tests

**Goal**: JavaScript-level tests for the MCP panel behavior (frame delivery, indicator rendering, connect button).

**Covers**: SC-4 (test verification)

**File scope**: `tests/acp_page.test.mjs`

**Changes:**

Add test cases following the pattern of `commandsFramePopulatesSessionCommands` (`acp_page.test.mjs:8784`):

```javascript
// MCP panel tests
test('mcp_servers frame shows indicator with connected count', async () => {
    // deliver mcp_servers frame with two connected servers
    // assert: #acpMcpIndicator not hidden, compact text contains "2 connected"
});

test('mcp_servers frame with failedAuthorization shows connect button', async () => {
    // deliver mcp_servers frame with one failed OAuth server
    // assert: Connect button rendered inside #acpMcpList
    // assert: acp-mcp-warn class on toggle button
});

test('mcp_servers frame with all connected has no connect button or warn class', async () => {
    // deliver mcp_servers frame with all status:connected
    // assert: no .acp-mcp-connect-btn in list
    // assert: no .acp-mcp-warn on toggle
});

test('null sessionMcpServers hides indicator', async () => {
    // call setSessionMcpServers(null) or deliver a session-reset frame
    // assert: #acpMcpIndicator has hidden attribute
});
```

**Exit criteria**:
- [ ] `node tests/acp_page.test.mjs` passes with new MCP tests
- [ ] Connect button renders in the test DOM for `failedAuthorization: true` entries
- [ ] Indicator hidden when no `mcp_servers` frame received
- [ ] Test for `acp-mcp-warn` class on toggle when failed/OAuth state
- [ ] Test: Connect button with a non-`https://` `authorizationUrl` does not render the button (or click is a no-op)

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| R1: `session/delete` `-32000` is a transient cloud error, not "already gone" | Local cleanup proceeds (correct); session remains in kiro-cli's registry until its own timeout | Accepted: no mechanism to distinguish the two cases. WARNING is logged, making the pattern observable in production. Noted in Follow-up Work. |
| R2: `sessionCapabilities.delete` removed in a future kiro-cli build | `_close_method` stays `None`; behavior reverts to today's local-only close | Mitigated within this plan: runtime read in `ensure_started()` with graceful fallback. |
| R3: Watchdog calls `_on_agent_death` while reader thread is still blocked | Both fire; second call is a no-op | Mitigated: idempotency guard at `acp.py:3818` (`if proc is not self._proc: return`) handles this. |
| R4: `_kiro/mcp/status` notification absent or renamed in a future build | Panel stays hidden; no regression over current behavior (logs as INFO) | Mitigated: hidden-until-first-frame design means absent notification = no panel. |
| R5: CSS variable names differ from those used in style.css | Colour tokens not applied; badges render in browser default colors | Phase 5 exit criterion requires verifying variable names match before finalising. |
| R6: `authorizationUrl` uses a non-https scheme | `window.open` would open arbitrary URLs | Mitigated in Phase 6: validate `url.startsWith('https://')` before calling `window.open`. Add to exit criteria. |

**Update after R6 identification**: Phase 6 exit criteria must include: `authorizationUrl` validated as `https://` scheme before `window.open`. Applies to the connect button click handler.

## 7) Verification

```bash
# Python test suite (all phases)
.venv-PowerAtlas\Scripts\python -m pytest tests/test_web.py --timeout=300

# acp_page tests (Phase 7)
node tests/acp_page.test.mjs

# Manual / Playwright
# 1. Start PowerAtlas: .venv-PowerAtlas\Scripts\power-atlas
# 2. Hard reload: Ctrl+Shift+R → http://127.0.0.1:4915/acp
# 3. Create a new session (workspace: PowerAtlas)
# 4. Verify MCP indicator appears in toolbar within ~5s of session start
# 5. Expand indicator → see server list with Playwright (connected) and Atlassian (failed/connect button)
# 6. Phase 1 verification: idle sweeper close → confirm kiro-cli no longer lists the session (reload rail)
# 7. Phase 2 verification: start a session, kill kiro-cli.exe in Task Manager, confirm /acp shows agent_died within 5s
```

Python changes require a PowerAtlas restart; HTML/CSS/JS changes need only a hard reload.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `docs/KNOWLEDGE.md` | Already updated (2026-09-23 commit 8047582): session/delete findings, MCP status findings, process tree; add "Implemented in plan 260923_..." note once phases ship | doc-table-only |
| `plans/ROADMAP.md` | Line ~136: "161 MB RSS and 3 processes" is v2 data; remove or qualify as v2-era measurement | doc-table-only |
| `plans/tests/260701_POWERATLAS.md` | Section 2.16/2.17 references idle sweeper timer topology — watchdog adds a third lifespan-managed task; update the task-count statement unconditionally (does not require those exact identifiers to appear) | 2 |
| `src/power_atlas/acp.py` (module docstring) | Module docstring near top references session-close architecture; update "Phase 6 adds session/close" comment to reflect `session/delete` | 1 |
| `README.md` | "there's no separate 'Connect' prompt or MCP status panel" (or equivalent) becomes false after Phase 6; update to describe the new toolbar panel | 6 |

## 9) Implementation Divergences from Plan

### Phase 1 (2026-09-24, code: d0faa33, fix: 93c4a71)

- `self._close_method = None` also added to `_discard()` (plan only specified `__init__` and `ensure_started`): prevents stale wire-close attempt after process teardown. `_discard` calls `_detach` which directly resets the field — sound addition, no plan text prohibits it.

### Phase 2 (2026-09-24, code: ae3f906)

- Three pre-existing test fixtures (`TestAcpLifespanWiring`, `TestGenerationRunsAtStartup._fake_acp`, `_run_lifespan_capturing_gate`) needed `start_watchdog` stub added — they broke because lifespan now calls `acp.start_watchdog()`. Added stubs.
- `test_watchdog_fires_on_crashed_process`: tracking stub clears `_proc` after first call to simulate `_on_agent_death/_detach` behavior and prevent repeated fires across ticks.

### Phase 3 (2026-09-24, code: a62ce27, fix: 21d13dc)

No divergences from plan.

*Remaining phases reserved — filled during /qdev execution.*

## Follow-up Work (Deferred)

1. **`session/delete` -32000 ambiguity.** The `-32000` response cannot be distinguished from a transient cloud error (R1). If the kiro-cli cloud service starts returning `-32000` transiently under load, session closes will silently succeed locally while kiro-cli retains the session. Monitor via the WARNING log; if observed in practice, file a kiro-cli issue to return a more specific error code. Source: R1.

2. **kiro-cli v3 internal per-session idle timeout.** Whether kiro-cli v3 has an internal per-session idle timeout that would eventually free session resources even without `session/delete` is unverified (probe deferred — requires 30+ min observation). This limits the "worst case" of R1 but is unquantified. Source: `/qexplore` Discovery item 7.

3. **`_kiro/governance/state`, `_kiro/tools/didChange`, `_kiro/powers/items_changed` notification handling.** Three new notification types observed during Phase 3 probing; params shapes unknown; currently logged as INFO. Investigate in a future session. Source: qexplore Discovery.

## Review Log

### 2026-09-24 — Phase 3 review (full effort, 4 personas)

Senior engineer, Security auditor, Reliability engineer, Maintainability reviewer. 1 auto-fix cycle.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| F3-1 | Medium | `list(params.get("servers") or [])` unsafe for non-list truthy values (dict→keys, str→chars, int→TypeError) | Fixed — `isinstance(_raw, list)` guard; empty list fallback (commit 21d13dc) |
| F3-2 | Low | `record.message` unreliable in caplog assertion — only set after `Formatter.format()` | Fixed — replaced with `record.getMessage()` (commit 21d13dc) |
| F3-3 | Low | No dedicated SC-1 buffer test for `_kiro/mcp/status` | Fixed — added `test_mcp_status_sc1_buffer_holds_and_replays` (commit 21d13dc) |
| F3-4 | Low | Unknown-session drop path silent after Phase 3 (no log trace) | Fixed — added `log.debug` for dropped/unknown-session case (commit 21d13dc) |
| F3-5 | Low | `envelope()` evaluated after meta write — partial state on unlikely error | Fixed — compute `frame = envelope(...)` before meta write (commit 21d13dc) |

Health: **Green** (1 Medium fixed, 4 Low fixed). 1809 tests passing.

### 2026-09-24 — Phase 2 review (full effort, 4 personas)

Senior engineer, Reliability engineer, Performance engineer, Maintainability reviewer.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| F2-1 | Low | `WATCHDOG_INTERVAL_SECONDS` annotated `"Final[float]"` but is rebindable by tests — inconsistent with other rebindable tunables (`ACP_IDLE_TTL_SECONDS`, `SWEEP_INTERVAL_SECONDS`) which carry no annotation | Noted — remove `"Final[float]"` annotation; follow up at Step 9 |
| F2-2 | Low | `test_watchdog_halts_after_max_consecutive_errors` hardcodes restore value `5.0` instead of saving/restoring — risks state leak if default changes | Noted — save/restore pattern; follow up at Step 9 |
| F2-3 | Low | `test_watchdog_double_fire_is_idempotent` tests loop-level guard only, not the real `_on_agent_death` idempotency guard | Noted — acceptable at per-phase; real guard covered by `_on_agent_death` existing tests |
| F2-4 | Low | `_noop_death` defined after `TestAcpCrashWatchdog` that uses it — functional but counter-intuitive ordering | Noted — move before the class at Step 9 |

Reliability engineer and Performance engineer: no findings.

Health: **Green** (0 High, 0 Medium). 1803 tests passing. Low findings noted for Step 9.

### 2026-09-24 — Phase 1 review (full effort, 4 personas)

Senior engineer, Reliability engineer, Security auditor, Maintainability reviewer. 1 auto-fix cycle.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| F1-1 | Medium | `CancelledError` from `_request` bypasses both `except` clauses — local cleanup not guaranteed on task cancellation | Fixed — introduced `_wire_exc: BaseException \| None = None` capture pattern; cleanup runs unconditionally, `CancelledError` re-raised after (commit 93c4a71) |
| F1-2 | Medium | Two stale docstrings (`_Supervisor` class, `_handle_close`) still said "no wire call / no JSON-RPC close method" | Fixed — both updated to describe conditional `session/delete` behavior (commit 93c4a71) |
| F1-3 | Medium | Inline comment above wire-call block: "any error logged as WARNING" — wrong; only `AgentRejected` is WARNING, `AcpError` is silently swallowed | Fixed — comment clarified to distinguish the two paths (commit 93c4a71) |
| F1-4 | Medium | No test for truthy non-bool `sessionCapabilities.delete` values (1, `"session/delete"`) — truthiness test contract not executable | Fixed — added `test_close_session_close_method_set_when_delete_capability_truthy_nonbool` (commit 93c4a71) |
| F1-5 | Medium | `CLOSE_METHOD` module constant is dead code — nothing reads it; creates confusing dual-existence with `self._close_method` | Fixed — constant removed; AS-5 historical note moved to `close_session` docstring (commit 93c4a71) |
| F1-6 | Low | No test for `CancelledError` from wire call not preventing cleanup | Fixed — added `test_close_session_cancelled_error_from_wire_does_not_skip_cleanup` (commit 93c4a71) |
| F1-7 | Low | Four stale test comments said "no wire call at all" unconditionally | Fixed — updated to "no wire call in this test because `_close_method` is None" (commit 93c4a71) |
| F1-8 | Low | `__init__` comment said "reset to `None` by `_discard`" — actually `_detach` | Fixed — corrected to `_detach` (commit 93c4a71) |
| F1-9 | Low | `acp_session` fixture teardown did not reset `_close_method` | Fixed — added `_close_method = None` reset to `acp_session` and `acp_store` teardowns (commit 93c4a71) |
| F1-10 | Low | `test_other_acp_error_swallowed` did not assert no WARNING emitted (silent path not pinned) | Fixed — added `caplog` + `assert not any(r.levelno == WARNING ...)` (commit 93c4a71) |

Security auditor: no findings.

Health after auto-fix: **Green** (0 High, all Medium/Low fixed). 1798 tests passing.

### 2026-09-23 — Plan Creation (via /qplan, full effort)

4 personas (Architect, Senior engineer, Security auditor, Reliability engineer). 1 doc-impact sub-agent. Cycle 1 only; reached ready state after auto-fix.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Dead `except AgentRejected` branch — subclass of `AcpError` caught first; WARNING is unreachable dead code | Fixed — reordered to `except AgentRejected` first, then `except AcpError` |
| 2 | High | `display: inline-flex` overrides `[hidden]`; indicator visible on load before any frame arrives | Fixed — added `#acpMcpIndicator[hidden] { display: none !important; }` to Phase 5 and exit criteria |
| 3 | High | `window.open(url)` without `https://` validation — open redirect on attacker-controlled URL | Fixed — added scheme guard in Phase 6 snippet, exit criteria, SC-4, and Phase 7 test |
| 4 | Medium | `session/delete` used `REQUEST_TIMEOUT_SECONDS` (90 s) — sweeper stalls 720 s at capacity | Fixed — added `CLOSE_TIMEOUT_SECONDS = 5.0` constant; Phase 1 snippet updated |
| 5 | Medium | Watchdog `except Exception: log + continue` with no failure counter — permanent bug logs every 5 s forever | Fixed — added `_WATCHDOG_MAX_ERRORS = 3` counter; watchdog self-cancels after 3 strikes |
| 6 | Medium | Phase 4 `_handle_new` snippet outside `if meta is not None:` guard — `AttributeError` on null-meta path | Fixed — clarified both snippets as inside the existing guard block |
| 7 | Medium | `sessionCapabilities.delete` checked with `is not None` — would activate on `"delete": false` | Fixed — changed to truthiness test `if _session_caps.get("delete"):` in SC-1 and exit criteria |
| 8 | Medium | `badge.className` concatenates unvalidated `srv.status` — CSS class injection possible | Fixed — added `_VALID_STATUSES` allowlist check before class construction |
| 9 | Medium | Phase 6 exit criteria and SC-4 did not include the `https://` check that R6 required | Fixed — both updated to name the scheme guard explicitly |
| 10 | Medium | Line references off by ~60–100 lines (consistent drift since file has grown) | Fixed — added advisory note in Phase 1 to use symbol/grep rather than line numbers |
| 11 | Low | `acp_fast` fixture snippet showed wrong dict-access pattern (should use `setattr`) | Fixed — Phase 2 snippet updated to show `setattr` pattern matching the existing fixture |
| 12 | Low | Double `proc.poll()` call in watchdog log (redundant syscall) | Fixed — bound `rc = proc.poll()` once |
| 13 | Low | `watchdog` missing `_post` comment explaining on-loop invocation | Fixed — comment added to `_watchdog_loop` snippet |
| 14 | Low | Doc-impact: `plans/tests/260701_POWERATLAS.md` lifespan task count stale; `README.md` MCP statement stale | Fixed — both added to Documentation Updates table unconditionally |

Health: **Green** (all High fixed; all Medium fixed).

## Harness Improvement Opportunities

- Asking three questions in one turn violated per-question discipline (`shared/skills/qexplore/shared.md`); user had to correct this — cost: one extra round-trip — suggested change: add an enforcement note to the kiro qexplore overlay that batching questions is a rationalization pattern, not a time-saving one.
