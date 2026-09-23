# ACP v3: Session Delete, Watchdog, and MCP Status Panel

> **Date**: 2026-09-23
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Three improvements to the ACP supervisor and /acp UI — `session/delete` wire close, crash-detection watchdog, and `_kiro/mcp/status` toolbar panel with OAuth connect flow.

---

## Intent

### Problem statement & desired outcomes

Three independent gaps in the ACP supervisor and /acp UI, all confirmed buildable on kiro-cli v3 2.23.1:

**Session close (now unblocked).** `CLOSE_METHOD = None` since the v3 engine cutover; the idle sweeper's "close" only releases PowerAtlas's own data structures — kiro-cli's internal session registry is never told. Probed 2026-09-23: `session/delete` returns `{}` on v3 2.23.1 and removes the session from kiro-cli's registry. `sessionCapabilities.delete` is advertised in every `initialize` response measured.

**Supervisor crash detection.** When kiro-cli.exe crashes mid-session, bun.exe and node.exe (grandchildren in the tree `kiro-cli.exe → bun.exe → node.exe`) inherit the stdout pipe write handle and keep the reader thread blocked on `read1()` indefinitely. The only current crash-detection path is `_send → proc.poll()`, which fires only when a client writes — an unattended session stalls silently until `PROMPT_SILENCE_SECONDS` (1800 s) sends a cancel. A periodic asyncio watchdog polling `proc.poll()` every 5 s detects the crash promptly.

**MCP status panel.** `_kiro/mcp/status` notifications have been arriving from kiro-cli on every session start and periodically (~50 min) since v3 launched. PowerAtlas has been logging them as unhandled and discarding them. Each notification carries the full server list with connection state (`connected`/`connecting`/`failed`/`disabled`), a `tools` array when connected, and `authorizationUrl` when `failedAuthorization: true`. The feature is fully buildable without any new kiro-cli support.

### Success criteria

- **SC-1 — Wire close.** `CLOSE_METHOD` set to `"session/delete"` at runtime after reading `agentCapabilities.sessionCapabilities.delete` from `initialize`'s response. `close_session()` sends `session/delete` before local cleanup when the agent is alive. `-32000` responses (already deleted / session not found) logged as WARNING and treated as success; other error codes re-raised. If `sessionCapabilities.delete` is absent, `CLOSE_METHOD` stays `None` and degrades silently. `_sweep_once` docstring updated: removes "frees 3 processes / 161 MB" (v2; in v3 all sessions share one process tree) and states that the call releases the session from kiro-cli's internal registry.

- **SC-2 — Crash watchdog.** `WATCHDOG_INTERVAL_SECONDS = 5.0` constant in `acp.py`. `_watchdog_loop()` asyncio coroutine: sleep first, snapshot `self._proc`, call `proc.poll()`, call `self._on_agent_death(proc)` if dead. `start_watchdog()` returns an `asyncio.Task`. Started from `web.py` lifespan alongside `start_sweeper()`; cancelled and awaited in the same `finally` block. `WATCHDOG_INTERVAL_SECONDS` added to `acp_fast` fixture's save/restore dict (`test_web.py:8887–8893`). Double-invocation handled by the existing idempotency guard at `acp.py:3818`.

- **SC-3 — MCP notification handling.** `_kiro/mcp/status` handled in `_on_notification` (branch on `method == "_kiro/mcp/status"`). Server list extracted from `params.get("servers") or []` and stored as `meta["mcpServers"]` (replace-all semantics). `"mcp_servers"` added to `SERVER_TYPES`. Frame broadcast via `_registry.broadcast` (same as commands/skills — not in history). A `_pending_mcp_servers: list | None = None` field on `_Supervisor` buffers the first notification arriving in the SC-1 window; flushed in `_flush_pending_commands`. Replayed to new subscribers in `_handle_subscribe` and `_handle_new`, mirroring the commands/skills resend at `acp.py:5773–5778` and `acp.py:5955–5960`.

- **SC-4 — MCP toolbar panel.** Persistent MCP indicator in /acp's session toolbar, visible whenever a session is active. Compact state: icon + count of connected servers, or a warning indicator when any server is `failed`/needs auth. Expanded state (toggle): list of all servers with name and status badge (`connected`=green / `connecting`=amber / `failed`=red / `disabled`=grey). For entries with `failedAuthorization: true` and `authorizationUrl` present: a "Connect" button that calls `window.open(authorizationUrl, '_blank')`. Panel updates automatically when the next `_kiro/mcp/status` notification arrives. Resets on session change. Styles match existing patterns in `style.css` (status-dot colour tokens and badge shapes from the dashboard).

### Scope boundaries & non-goals

- MCP server configuration, enable/disable, or modifying which servers connect — not in scope; `mcpServers: []` continues to be passed to `session/new`
- The existing `display_error` → `agent_error` transcript path — kept as-is; handles non-status MCP errors that don't appear in `_kiro/mcp/status`
- `/mcp` slash-command invocation from /acp — three hypotheses probed, all failed; excluded (moved to `plans/CLOSED_INVESTIGATIONS.md`)
- Newly discovered notification types `_kiro/governance/state`, `_kiro/tools/didChange`, `_kiro/powers/items_changed` — out of scope; continue to log as INFO

---

## Exploration Discovery
<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

**session/delete wire contract (measured 2026-09-23, kiro-cli v3 2.23.1):**
- `session/delete` → `{}` on success; `-32000 "Something went wrong with the cloud session service. Please try again."` if already deleted or session not found (not idempotent)
- Advertised at `agentCapabilities.sessionCapabilities.delete` in `initialize` response; absent → degrade to `CLOSE_METHOD = None`
- `CLOSE_METHOD` module-level constant at `acp.py:645`; comment at `acp.py:643–650`
- Error propagation: `_on_response` at `acp.py:3430` raises `AgentRejected(f"{text} (code {err.get('code')})")` — `acp.py:3465`
- `close_session()` at `acp.py:4912`; already guarded against inflight sessions (sweeper condition 4 `acp.py:6459`; HTTP handler `acp.py:6309`)
- `_sweep_once` docstring with v2 process-count claims at `acp.py:6568–6572`

**Watchdog pattern:**
- Existing peer: `_sweep_loop` at `acp.py:6580`; `start_sweeper()` at `acp.py:6608`
- `web.py` lifespan start at `web.py:549`; `asyncio.gather` teardown at `web.py:562`
- `acp_fast` fixture save/restore at `test_web.py:8887–8893`
- Idempotency guard at `acp.py:3818`: `if proc is not self._proc: return`
- `self._loop` set once at `acp.py:2932`, never cleared — watchdog runs on event loop, no `_post` needed
- `proc.poll()` safe to call from event loop concurrently with `_send`'s `_write_lock`-guarded write path

**MCP notification paths:**
- `_kiro/mcp/status` currently falls through to `log.info` at `acp.py:4383` (unhandled fallback)
- `_pending_commands` buffer at `acp.py:2688–2689`: `tuple[list, list] | None`, consumed as `(commands, skills)` — do NOT extend; use a separate `_pending_mcp_servers: list | None = None` field
- `_flush_pending_commands` at `acp.py:3849`: extend to also flush `_pending_mcp_servers`
- `SERVER_TYPES` frozenset at `acp.py:171–202` — add `"mcp_servers"`
- commands/skills resend in `_handle_subscribe` at `acp.py:5773–5778`; same pattern for mcpServers
- commands/skills resend in `_handle_new` at `acp.py:5955–5960`; same pattern for mcpServers
- `setSessionCommands` / `setSessionSkills` in `composer-chrome.js:560–572` — template for `setSessionMcpServers`
- `commands` frame handler in `acp.html:5051`; `skills` frame handler in `acp.html:5057` — templates
- `resetCommandPalette` at `composer-chrome.js:579` — add MCP state reset here
- No existing MCP UI in /acp; no MCP routes in `web.py` (confirmed absent)
- CSS reference: existing `.acp-*` status patterns in `style.css`; dashboard session-status dot colours

**Test templates:**
- Python extraction: `test_single_inflight_stores_commands_and_broadcasts` at `test_web.py:16299`
- `_v3` helper: `_sv3_with_session(monkeypatch)` + `_session_info_msg()` at `test_web.py:20280–20295`
- Subscribe resend: `test_subscribe_replays_skills_frame` at `test_web.py:16790`; `test_handle_subscribe_resends_cached_commands_and_skills` at `test_web.py:19613`
- `_handle_new` resend: `test_handle_new_delivers_buffered_commands_and_skills_to_creator` at `test_web.py:19519`
- JS frame: `commandsFramePopulatesSessionCommands` at `acp_page.test.mjs:8784`

### 5. Risks & mitigations

**R1 — `-32000` masking genuine transient delete failures.** No error code distinguishes "already gone" from a transient cloud error; both return `-32000`. Accepted: log a WARNING so the pattern is observable. Local cleanup always proceeds regardless. Re-raise on any other code.

**R2 — `sessionCapabilities.delete` removed in a future kiro-cli build.** Mitigated by the runtime check in SC-1: read capability at startup, degrade to `None` if absent. Same graceful-degradation pattern as the v2 terminate fallback.

**R3 — Watchdog + reader thread racing to call `_on_agent_death`.** Safe: idempotency guard at `acp.py:3818` handles both paths. `_discard` fires once, `_close_job` fires once.

**R4 — `_kiro/mcp/status` absent or renamed in a future build.** Panel shows nothing when no notification has arrived — valid empty state. Not a regression: current behaviour is also nothing (logged-and-discarded).

**R5 — `authorizationUrl` rotating between consecutive notifications.** Replace-all semantics mean the Connect button always uses the latest URL. A stale click (from an older render) opens a stale URL — acceptable; user can retry.

### 6. Resolved decisions

- Q1: Should `session/delete` be implemented as `CLOSE_METHOD`? — A: implement — Decision: `CLOSE_METHOD = "session/delete"` set from `initialize` result at runtime; `-32000` treated as success with WARNING
- Q2: `WATCHDOG_INTERVAL_SECONDS`? — A: ok (5 s) — Decision: `WATCHDOG_INTERVAL_SECONDS = 5.0`
- Q3: MCP feature scope? — A: full workflow (panel + OAuth connect button) — Decision: SC-3 + SC-4 with all status states and OAuth flow
- Q4: MCP panel placement? — A: toolbar element (always visible, compact + expanded toggle) — Decision: persistent session-toolbar indicator, polished, matching existing styles
- Q5: One vs. three plans? — A: one combined plan — Decision: single plan, 7 phases

### 7. Open items

None. All items above are execution-contingent (resolved once code is written) rather than genuinely undecided.

### 8. Recommended approach

**Phase 1 — session/delete wire close** (`acp.py`, `test_web.py`): Read `agentCapabilities.sessionCapabilities.delete` from the `initialize` result in `ensure_started()` and set an instance-level `close_method` attribute (overriding the module `CLOSE_METHOD` default). In `close_session()`: guard with `if self.close_method and self.alive()`, send `self.close_method` via `_request`, catch `AgentRejected` containing `"(code -32000)"` → WARNING + continue, re-raise others. Update `_sweep_once` docstring. Tests: verify wire call fired, -32000 swallowed, runtime-absent capability degrades.

**Phase 2 — Crash detection watchdog** (`acp.py`, `web.py`, `test_web.py`): Add `WATCHDOG_INTERVAL_SECONDS = 5.0`. Implement `_watchdog_loop()` (sleep, snapshot `_proc`, poll, call `_on_agent_death` if dead). Add `start_watchdog()`. Wire into lifespan alongside sweeper. Add constant to `acp_fast` fixture. Tests: mock `proc.poll()` returning non-None, verify `_on_agent_death` fires; verify double-fire is idempotent.

**Phase 3 — MCP Python extraction** (`acp.py`, `test_web.py`): Add `_pending_mcp_servers: list | None = None` field. Add `_kiro/mcp/status` handler in `_on_notification` (method-based dispatch, before the fallthrough `log.info`). Extract `servers`, buffer or store in `meta["mcpServers"]`. Extend `_flush_pending_commands` to also flush `_pending_mcp_servers`. Tests: notification handler stores correctly, buffer flushed on session registration, replace-all semantics.

**Phase 4 — MCP frame routing and replay** (`acp.py`, `test_web.py`): Add `"mcp_servers"` to `SERVER_TYPES`. Broadcast `envelope("mcp_servers", {"servers": servers}, session_id)` via `_registry.broadcast`. Add replay in `_handle_subscribe` (after commands/skills resend). Add replay in `_handle_new` (same). Tests: broadcast fires, subscribe replays, new-connection replays.

**Phase 5 — MCP toolbar HTML/CSS** (`acp.html`, `style.css`): Add the MCP indicator DOM element in the session toolbar. Compact state: icon + connected count or warning indicator. Expanded state: hidden panel (toggle), server list. Add CSS: `.acp-mcp-*` status-badge and indicator classes using existing palette colours.

**Phase 6 — MCP JavaScript** (`acp.html`, `composer-chrome.js`): Add `mcp_servers` frame handler in `acp.html`. Add `setSessionMcpServers(list)` and `sessionMcpServers` variable in `composer-chrome.js`. Add MCP reset to `resetCommandPalette`. Render compact indicator state (connected count, warning dot). Render expanded server list: name, status badge, "Connect" button when `failedAuthorization: true`. Connect button: `window.open(entry.authorizationUrl, '_blank')`. Hide indicator when `sessionMcpServers` is `null` (no notification received yet).

**Phase 7 — Tests** (`test_web.py`, `tests/acp_page.test.mjs`): pytest for Phases 3–4 (extraction, buffer, frame type, subscribe and new-connection replay). `acp_page.test.mjs` for Phases 5–6 (indicator renders when mcp_servers frame arrives, connect button present for `failedAuthorization` entry, hidden when list empty/null).

### 9. QA environment

- Python suite: `.venv-PowerAtlas\Scripts\python -m pytest tests/test_web.py --timeout=300`
- acp_page tests: `node tests/acp_page.test.mjs`
- PowerAtlas live: `.venv-PowerAtlas\Scripts\power-atlas` → http://127.0.0.1:4915/acp
- Hard reload (`Ctrl+Shift+R`) picks up acp.html / style.css / composer-chrome.js changes; Python changes require restart
- MCP OAuth probe: Atlassian MCP is enabled and will produce `_kiro/mcp/status` with `failedAuthorization: true` on a fresh session without additional setup. Playwright MCP connects automatically and can be used to verify the "connected" panel state.

## Harness Improvement Opportunities

- Asking three questions in one turn violated the per-question discipline (`shared/skills/qexplore/shared.md`); the user had to correct this — cost: one extra round-trip before interview resumed correctly — suggested change: add an enforcement reminder to the kiro qexplore overlay that batching questions is a rationalization pattern, not a time-saving one.
