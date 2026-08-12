# Kiro Extensions Support + Date-Sort Fix

> **Date**: 2026-08-12
> **Status**: Exploring
> **Scope**: ACP slash command palette, session management notifications, groupby-date auto-poll fix

---

## Intent

### Problem statement & desired outcomes

Two related improvements to the `/acp` surface:

**1. Kiro extensions support** — the ACP session pane currently ignores three categories of kiro-private notifications that the TUI handles natively:
- Slash commands: `_kiro.dev/commands/available` arrives after session creation with the full command catalogue, but falls through to the debug log. The prompt box has no `/` dropdown, no live autocomplete, and no command execution path.
- Session management: `_kiro.dev/compaction/status` (context compaction progress/completion) and `_kiro.dev/clear/status` (session history cleared) arrive and are silently discarded. The user sees nothing when a compaction occurs mid-session.

**2. Groupby-date auto-poll lag** — when the ACP rail is in `date` or `status` mode, the 60-second automatic refresh only updates availability/status dots on existing rows (`railRefreshStates`). It does not re-fetch the list, so newly-active sessions don't appear at the top and recently-updated sessions don't reorder until the user manually presses Refresh. The code for the full refresh on date/status mode is missing from `railRefresh`.

### Success criteria

1. Typing `/` in the ACP prompt box opens a dropdown showing the available commands (from `_kiro.dev/commands/available`); typing further characters after `/` filters via `_kiro.dev/commands/options` live prefix suggestions; selecting a command executes it via `_kiro.dev/commands/execute` with the TuiCommand object payload.
2. When kiro-cli compacts the context window, the transcript pane shows a brief status indicator (started / completed / failed) sourced from `_kiro.dev/compaction/status`.
3. `_kiro.dev/clear/status` is received and silently consumed (no UI change needed — the TUI itself only logs a debug message for it).
4. In date or status grouping mode, the 60-second auto-poll refetches the full first page (`loadFlatPage(1)`) so newly-active sessions appear without a manual Refresh press.

### Scope boundaries & non-goals

**In scope:**
- `acp.py`: add handlers for `_kiro.dev/commands/available`, `_kiro.dev/compaction/status`, `_kiro.dev/clear/status`; new client→server type `"commands_options"` → `_handle_commands_options` → `_supervisor.commands_options()`; new client→server type `"commands_execute"` → `_handle_commands_execute` → `_supervisor.commands_execute()` using the TuiCommand payload shape
- `acp.html`: `/` key intercept in the prompt textarea, dropdown UI, live autocomplete via `commands_options` WS round-trip, command execution via the new `commands_execute` WS message; compaction status indicator in the transcript pane; `railRefresh` date/status path changed to call `loadFlatPage(1)`
- New `SERVER_TYPES` entries: `"commands"` (catalogue broadcast), `"commands_options_result"` (autocomplete response), `"commands_execute_result"` (execution response), `"compaction"` (status update)

**Out of scope:**
- `session/set_mode`, `session/set_model` — not part of this work
- Prompts / skills dropdown — `/` commands only; skill invocation as plain text is already supported
- `_kiro.dev/mcp/oauth_request`, `_kiro.dev/mcp/server_initialized` — separate MCP surface, not this work
- Bulk command execution / scripting
- Command history / recents

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

- **Isolation boundary** (`acp.py` module header): imports only `config.CONFIG_DIR` and `launcher._SESSION_ID_RE`. Any new per-session state must live in `_supervisor.sessions[sid]` dict (same pattern as `contextPercent` set via `_note_context`). No new imports from other `power_atlas` modules.
- **`_kiro.dev/commands/execute` payload**: TuiCommand object form required — `{"sessionId": sid, "command": {"command": name, "args": {}}}`. String form kills the agent (verified 2/2 on old builds; the object form is what KiroCrew and tui.js use successfully). `_kiro.dev/commands/execute` is a request (has `id`), not a notification.
- **`_kiro.dev/commands/available`** notification: `params = {commands: [{name, description, meta}], prompts, tools, mcpServers}`. No `sessionId` in params. Attribution via inflight session context (same approach as `_on_subagent_list` / SUBAGENT_LIST_METHOD).
- **`_kiro.dev/commands/options`** request: client sends `{sessionId, command (no leading "/"), partial}`, agent responds `{options: []}`. Method exists and works (confirmed in tui.js and KiroCrew). This is a new client→agent round-trip initiated by the browser.
- **`_kiro.dev/compaction/status`** notification: `params = {status: {type: "started"|"completed"|"failed", error?}, summary?}`. No `sessionId`. Attribution: inflight session. Broadcast-only (not in history — it's a level, like contextPercent). On `completed`, KiroCrew resets the context usage meter; PowerAtlas should show a transcript-pane system message.
- **`_kiro.dev/clear/status`** notification: no params consumed by tui.js or KiroCrew — both log/ignore. PowerAtlas silently consumes it (single `return` in `_on_notification`).
- **`SERVER_TYPES` + `envelope()` enforcement**: `envelope()` raises `ValueError` for unknown types — any new server→client frame type must be added to `SERVER_TYPES` before any emit call.
- **`steer`/`steer_ack` pattern** (`acp.py:159-163`, `_dispatch`, `_handle_steer`): canonical model for adding a new client→server type. Four steps: add to `CLIENT_TYPES`, add response type to `SERVER_TYPES`, add arm to `_dispatch`, write `async _handle_*` with guards.
- **`railRefresh` date/status path** (`acp.html:3158-3170`): currently calls `railFetch({mode:'recent'...})` then passes result to `railRefreshStates` — which only updates existing rows and does not reorder or add. Fix: replace this branch with a `loadFlatPage(1)` call (identical to what the manual Refresh button does).
- **`railRefreshStates` design intent** (`acp.html:3049`): "Timestamps and the workspace's own fields stay out… rewriting them would let a background poll reorder a row under a thumb already travelling towards it." This comment applies to the grouped mode — the date mode's whole purpose is recency ordering, so `loadFlatPage(1)` reordering is the correct behavior.
- **Per-session commands list storage**: stored in `_supervisor.sessions[sid]` dict (broadcast-only, not in history, same as `contextPercent`). Forwarded to newly-subscribing sockets on the existing `session` frame or as a follow-up `commands` frame (same pattern as `crew` subagents on subscribe in `_handle_subscribe`).

- `acp.py:159-163` — `CLIENT_TYPES`, `SERVER_TYPES`
- `acp.py:~3368` — `_dispatch()` routing table
- `acp.py:~4097` — `_handle_steer()` (pattern to follow)
- `acp.py:~2649` — `_on_notification()` (where new method branches go)
- `acp.py:~3350` — `_handle_subscribe()` (where per-session state is replayed)
- `acp.html:3140` — `railRefresh()`; `acp.html:3158` — date/status branch to fix
- `acp.html:2863` — `loadFlatPage()` (what the fix calls)
- `acp.html:4635` — `sendPrompt()` (reference for prompt-box input handling)
- `acp.html:5569` — prompt textarea `keydown` listener (where `/` intercept goes)

### 5. Risks & mitigations

- **`commands/execute` payload shape**: the object form `{command: {command: name, args: {}}}` is confirmed correct from tui.js and KiroCrew source. Sending a string form is what killed the agent on original measurements. Implementation must use the object form exclusively.
- **No `sessionId` on `commands/available` and `compaction/status`**: attribution via inflight session count (same as SUBAGENT_LIST_METHOD). If multiple sessions are inflight simultaneously, drop silently (same policy as subagent list). This is a known gap documented in `_on_subagent_list`.
- **`commands/options` latency**: a round-trip per keystroke after `/`. Acceptable based on the 1ms observed for `/tools` as a prompt (agent-side interception is fast), but if latency is noticeable in practice, fall back to client-side filtering of the already-received commands list. Decision deferred to implementation.
- **`railRefresh` reorder during interaction**: `loadFlatPage(1)` rebuilds the entire first page, which `renderRail()` then re-draws using `railRestoreFocus`. The existing focus-restore mechanism should handle this; tested by the existing `acp_page.test.mjs` focus tests.
- **History recording**: commands catalogue and compaction status should NOT be recorded in the replay buffer (they are current-state frames, not events). Compaction status on `completed` resets context usage which itself must not go into history. Same pattern as `_note_context` (broadcast-only via `_registry.broadcast`, not `_emit`).

### 6. Resolved decisions

- Q1: Sort fix cause? — A: Auto-poll lag (60s `railRefresh` in date/status mode uses `railRefreshStates` instead of a full re-fetch) — Decision: fix `railRefresh` to call `loadFlatPage(1)` in date/status mode.
- Q2: Slash command scope? — A: Option C (full: dropdown + live autocomplete + execute) — Decision: all three protocol methods implemented.
- Q3: `commands/execute` banned or usable? — A: Usable with TuiCommand object payload; original ban was from testing the wrong string payload — Decision: use `_kiro.dev/commands/execute` with `{command: {command: name, args: {}}}`.
- Q4: `clear/status` display? — A: No display — both tui.js and KiroCrew log/ignore — Decision: silently consume in `_on_notification`.
- Q5: `compaction/status` display? — A: Show a system message in the transcript pane for started/completed/failed — Decision: new `"compaction"` server frame broadcast to subscribers.
- Q6: Commands list storage? — A: Per-session in `sessions[sid]` dict, broadcast-only (not in history), replayed on subscribe like the `subagents` crew frame — Decision: follow the crew/contextPercent pattern.

### 7. Open items

- Exact shape of `commands/options` response `{options: []}`: the `options` array element shape is not confirmed from tui.js (the method returns the raw result — no destructuring visible). Resolvable with one live probe during implementation.
- Whether `commands/execute` streaming output arrives as `agent_message_chunk` kind or as a distinct `commands_execute` update type — KiroCrew's `_dispatch_events` handles it as an ordinary prompt turn. Assume same chunk path; verify during implementation.

### 8. Recommended approach

**Phase 1 — acp.py server-side** (three changes):
1. Add `_kiro.dev/commands/available` branch in `_on_notification`: extract `commands` list, attribute to inflight session, store in `sessions[sid]["commands"]`, broadcast a `"commands"` frame to subscribers (not `_emit` — not in history).
2. Add `_kiro.dev/compaction/status` branch in `_on_notification`: attribute to inflight session, broadcast a `"compaction"` frame `{status, error?, summary?}` to subscribers. On `completed`, optionally call `_note_context(sid, None)` to reset the meter.
3. Add `_kiro.dev/clear/status` branch in `_on_notification`: single `return` (silent consume).
4. Add `"commands"` and `"compaction"` to `SERVER_TYPES`.
5. Add `"commands_options"` to `CLIENT_TYPES`; add `SERVER_TYPES` entry `"commands_options_result"`; add arm to `_dispatch`; write `_handle_commands_options` (guards: session exists, subscribed, not subagent); add `_Supervisor.commands_options(sid, partial)` calling `_request("_kiro.dev/commands/options", {sessionId, command: "", partial})`.
6. Add `"commands_execute"` to `CLIENT_TYPES`; add `SERVER_TYPES` entry `"commands_execute_chunk"` and `"commands_execute_end"`; add arm to `_dispatch`; write `_handle_commands_execute` (guards: session exists, subscribed, no turn inflight, not subagent); add `_Supervisor.commands_execute(sid, name)` calling `_request("_kiro.dev/commands/execute", {sessionId, command: {command: name, args: {}}})` then streaming the result chunks.
7. Replay `commands` frame on subscribe (like `subagents`).

**Phase 2 — acp.html client-side** (three changes):
1. `railRefresh` fix: replace the date/status branch's `railFetch + railRefreshStates` with `loadFlatPage(1)`.
2. Slash command dropdown: intercept `/` keydown on the prompt textarea; show a floating dropdown populated from the received `commands` catalogue; filter on keystrokes using `commands_options` WS round-trip (debounced); keyboard-navigable; selecting a command sends `commands_execute` WS message and closes the dropdown.
3. Compaction status indicator: handle `"compaction"` frame in `onmessage`; render a non-bubbled system row in the transcript (similar to how turn start/end is shown) for `started`, `completed`, `failed`.

**Phase 3 — tests**
- `test_web.py`: new tests for `commands`, `compaction`, `clear` notification handling; `commands_options` and `commands_execute` client frame dispatch.
- `acp_page.test.mjs`: tests for the slash command dropdown, compaction indicator, and `railRefresh` date-mode fix.

### 9. QA environment

- PowerAtlas running: `.venv-PowerAtlas\Scripts\power-atlas` — opens at `http://127.0.0.1:<port>` (random port unless configured)
- `/acp` surface at `http://127.0.0.1:<port>/acp`
- Playwright via `node tests/acp_page.test.mjs` (JS harness, not pytest)
- Python suite: `.venv-PowerAtlas\Scripts\pytest tests/test_web.py` (or full suite)
- Live kiro-cli required to test slash command execution; available at `kiro-cli` on PATH
- Date-sort fix verifiable by switching to date grouping mode and waiting 60s (or mocking the timer in the JS test harness)
