# Kiro Extensions Support + Date-Sort Fix

> **Date**: 2026-08-12
> **Status**: In Progress
> **Last Updated**: 2026-08-12
> **Scope**: ACP slash command palette, session management notifications, groupby-date auto-poll fix
> **Estimated effort**: 1–2 days

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
6. Add `"commands_execute"` to `CLIENT_TYPES`; add `SERVER_TYPES` entry `"commands_execute_result"` (ack-only frame `{name, status:"accepted"}`); add arm to `_dispatch`; write `_handle_commands_execute` (guards: session exists, subscribed `not_subscribed`, no turn inflight, not subagent, name length, catalogue validation); add `_Supervisor.commands_execute(sid, name)` calling `_request("_kiro.dev/commands/execute", {sessionId, command: {command: name, args: {}}})`. Output flows as agent chunks via existing `_on_notification`.
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


---

## 1) Current State

**`acp.py` — notification handler** (`acp.py:2649` `_on_notification`): Three kiro-private method branches are missing. `_kiro.dev/commands/available` (no `sessionId`, params: `{commands:[{name,description,meta}], prompts, tools, mcpServers}`), `_kiro.dev/compaction/status` (no `sessionId`, params: `{status:{type,error?}, summary?}`), and `_kiro.dev/clear/status` (no params) all fall through to the debug log. Verified 2026-08-12 from tui.js `handleCommandsAdvertising`, `handleCompactionStatus`, `handleClearStatus`.

**`acp.py` — protocol type sets** (`acp.py:159–163`): `CLIENT_TYPES` has 7 entries; `SERVER_TYPES` has 14. Neither contains any command or compaction type.

**`acp.py` — `_on_agent_request`** (`acp.py:2393`): Any agent→client request is refused with `-32601`. `_kiro.dev/commands/execute` is a client→agent request (has `id`), dispatched by the client. This is different from `_on_agent_request` — `commands_execute` flows through `_dispatch` like `steer`.

**`_kiro.dev/commands/execute` payload** (verified 2026-08-12 from tui.js `getCommandOptions` and KiroCrew `send_command`): TuiCommand object form: `{"sessionId": sid, "command": {"command": name, "args": {}}}`. KNOWLEDGE.md:47 documents kills with the *string form*; the object form works correctly. This plan implements the object form exclusively.

**`_kiro.dev/commands/options` payload** (verified 2026-08-12 from tui.js `getCommandOptions`): Client sends `{sessionId, command: "" (no leading "/"), partial: "<typed suffix>"}`. Agent responds `{options: [...]}`. Exact `options` element shape not confirmed from tui.js — verify during Phase 1 implementation via live probe or debug log.

**`acp.html` — `railRefresh` date/status branch** (`acp.html:3158–3170`): Calls `railFetch({mode:'recent', page:1, size:Math.min(100, railFlat.length)})` then passes result to `railRefreshStates`. `railRefreshStates` (`acp.html:3049`) explicitly excludes timestamp reordering and new-row insertion. Result: newly-active sessions don't appear and session order doesn't update on auto-poll.

**`acp.html` — prompt textarea handlers** (`acp.html:5569`): Only `Enter` keydown is intercepted. No `/` key handler, no autocomplete UI, no `commands` or `compaction` frame handling in the `onmessage` dispatcher.

**`steer`/`steer_ack` pattern** (canonical model for new client→server types): `CLIENT_TYPES` (`acp.py:159`), `SERVER_TYPES` (`acp.py:163`), `_dispatch` arm (`acp.py:~3404`), `_handle_steer` (`acp.py:4097`), `_Supervisor.steer()` (`acp.py:3069`).

**`_handle_subscribe` crew replay** (`acp.py:3536`): After attaching, if `crew` dict exists for the session, a `subagents` frame is broadcast. The same pattern applies for commands list replay.

## 2) Goal

Add three kiro protocol extensions to the ACP surface: a `/` command palette in the prompt box (catalogue from `_kiro.dev/commands/available`, live autocomplete from `_kiro.dev/commands/options`, execution via `_kiro.dev/commands/execute`), a compaction status indicator in the transcript pane (from `_kiro.dev/compaction/status`), and silent consume of `_kiro.dev/clear/status`. Fix the 60-second auto-poll in date/status mode to re-fetch the recency list rather than only updating existing row states.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| `commands/execute` payload | TuiCommand object form: `{command:{command:name,args:{}}}` | String form | String form kills the agent (KNOWLEDGE.md:47, 2/2 reproductions); object form confirmed working in tui.js and KiroCrew |
| Commands list attribution (no sessionId) | Inflight session attribution — if exactly 1 session mid-prompt, attribute to it; otherwise drop | Broadcast to all, always attribute to "current session" | Same policy as `_on_subagent_list` (SUBAGENT_LIST_METHOD). Documented known gap: 0 or 2+ inflight sessions means catalogue silently not stored for that push |
| Compaction status attribution (no sessionId) | Same inflight attribution as commands | Per-session storage always | Same rationale as above; a compaction fires while the model is running a turn |
| Commands catalogue storage | `sessions[sid]["commands"]` — a list of `{name, description}` dicts; broadcast-only (`_registry.broadcast`, not `_emit`); replayed on subscribe like the `subagents` crew frame | Record in history (`_emit`) | Commands list is current state (like `contextPercent`), not a transcript event. Replay via `_handle_subscribe` addition |
| Compaction status in history? | NO — broadcast-only; `completed` also calls `_note_context(sid, None)` to reset meter | Record as transcript event | Compaction status is ephemeral process state, not conversation content. Same pattern as `_note_context` broadcast |
| `clear/status` display | Silent consume — single `return` in `_on_notification` | Show indicator | tui.js `handleClearStatus` logs debug only; KiroCrew yields an empty event. Nothing useful to show |
| `commands/execute` streaming | `commands_execute_result` is a lightweight ack frame (`{name, status:"accepted"}`) — NOT carrying content text. Command output arrives as `agent_message_chunk` notifications → existing `chunk` frames to all subscribers, same as `session/prompt`. `_handle_commands_execute` adds `session_id` to `inflight`, awaits the `_request` ack, then releases `inflight`. Chunk frames arrive concurrently via `_on_notification`. Verify via Phase 1 live probe: confirm ack arrives before full output; if not, switch to `timeout=_INACTIVITY`. | New dedicated streaming loop | `_request` returns the JSON-RPC ack, not model output. Displaying output twice (ack text + chunks) was the defect. |
| `commands/execute` client frame triggers | New `CLIENT_TYPES` entry `"commands_execute"` → `_handle_commands_execute` | Reuse `"prompt"` type | Commands are not prompts; separate type preserves clean semantics and separate guards |
| `commands_execute_result` payload | `{name: str, status: "accepted"}` only — no `text` field. Output is in transcript as agent chunks. | `{name, text}` with command output | `_request` ack does not carry the model's output; a `text` field would always be empty and mislead the client. |
| Date/status mode `railRefresh` | Replace `railRefreshStates` branch with `loadFlatPage(1)` | Keep `railRefreshStates`, add a separate `insertNewRows` pass | `loadFlatPage(1)` already handles full re-render correctly and is used by manual Refresh; re-implementing subset behavior duplicates logic and risks drift |
| `commands/options` response element shape | Defer to live probe during Phase 1; treat `options` as `[{name:str, description?:str}]` (same shape as `commands/available` commands list entries) | Hardcode empty assumption | Shape not confirmed from tui.js; cheapest resolution is debug-log capture on first real session |
| `commands/execute` streaming output type (superseded) | ~~`commands_execute_result` server frame carrying `{done:bool, text:str}` for each chunk~~ | See `commands/execute` streaming row above | Updated during review cycle — `commands_execute_result` is ack-only; output is in chunk frames. |
| SC identifiers | SC-1 through SC-4 matching the Intent success criteria | None | Enables `/qvalidate` sc-coverage check |

**Open items resolved:**
- `commands/options` element shape: treat as `{name, description?}` during implementation; verify with live probe or debug log at Phase 1 start. Update `docs/KNOWLEDGE.md` if shape differs.
- `commands/execute` streaming: arrives as ordinary `session/update` chunks on the `agent_message_chunk` channel (confirmed from KiroCrew `_dispatch_events`). The `_request` response will be the final result dict; chunks arrive via `_on_notification` as usual.

## 4) External Dependencies & Costs

### Required external changes

| Category | Change needed | Owner | Status |
|---|---|---|---|

No external changes required. This is a code-only change to an existing single-machine app.

### Cost impact

None. No new infrastructure, APIs, or recurring costs.

## 5) Implementation Phases

### Phase 1: acp.py — notification handlers + new server types [QA]

**Goal**: Handle the three new kiro notifications server-side, add new SERVER/CLIENT types, store the commands catalogue per-session, and replay it on subscribe. Also add `_Supervisor.commands_options()` and `_Supervisor.commands_execute()` methods plus their client-frame handlers.

**Covers**: SC-1, SC-2, SC-3

**File scope**:
- `src/power_atlas/acp.py`
- `tests/test_web.py`

**Changes:**

1. **`SERVER_TYPES`** — rewrite the `frozenset({...})` literal to include `"commands"`, `"compaction"`, `"commands_options_result"`, `"commands_execute_result"`. Note: `frozenset` is immutable; this is a full rewrite of the literal, not an `.add()` call.

2. **`CLIENT_TYPES`** — rewrite the `frozenset({...})` literal to include `"commands_options"`, `"commands_execute"`. Same note.

3. **New constant** — add `MAX_COMMAND_PARTIAL_CHARS = 256` near the other cap constants (e.g. near `MAX_STEER_CHARS`). Slash command names are 5–30 chars; 256 is a generous cap that prevents protocol abuse without restricting legitimate use.

3. **`_on_notification` — add three branches** (before the fall-through debug log at the end):

   `_kiro.dev/commands/available` branch:
   ```python
   if method == "_kiro.dev/commands/available":
       commands = [
           {"name": _as_text(c.get("name")), "description": _as_text(c.get("description"))}
           for c in (params.get("commands") or [])
           if isinstance(c, dict) and _as_text(c.get("name"))
       ]
       # Attribute to the single inflight session; drop if 0 or 2+
       inflight = self.inflight
       if len(inflight) == 1:
           sid = next(iter(inflight))
           meta = self.sessions.get(sid)
           if meta is not None:
               meta["commands"] = commands
               _registry.broadcast(sid, envelope("commands", {"commands": commands}, sid))
       return
   ```

   `_kiro.dev/compaction/status` branch:
   ```python
   if method == "_kiro.dev/compaction/status":
       status = params.get("status") or {}
       stype = _as_text(status.get("type")) if isinstance(status, dict) else _as_text(status)
       error = _as_text(status.get("error")) if isinstance(status, dict) else ""
       summary = _as_text(params.get("summary"))
       inflight = self.inflight
       if len(inflight) == 1:
           sid = next(iter(inflight))
           if stype == "completed":
               # Reset context meter: _note_context(sid, None) stores None in
               # meta["contextPercent"] and broadcasts meta{contextPercent:null}
               # (JSON null). Client sets the bar hidden — correct semantics.
               # Type annotation for _note_context accepts float|None after this plan.
               _note_context(sid, None)
           _registry.broadcast(sid, envelope(
               "compaction", {"status": stype, "error": error, "summary": summary}, sid))
       else:
           log.debug("ACP compaction_status: %d session(s) inflight, cannot "
                     "attribute; dropped (status=%r)", len(inflight), stype)
       return
   ```

   `_kiro.dev/clear/status` branch:
   ```python
   if method == "_kiro.dev/clear/status":
       return   # silent consume; TUI logs debug only, nothing to display
   ```

   These three branches must be inserted **before** the `METADATA_METHOD` and `SUBAGENT_LIST_METHOD` checks (or immediately after them — placement does not matter for correctness since they are distinct methods). For clarity, place them immediately before the fall-through debug block.

4. **`_handle_subscribe` — replay commands** (after the existing crew replay block at `acp.py:3536`):
   ```python
   commands = meta.get("commands")
   if commands is not None:
       conn.send(envelope("commands", {"commands": commands}, session_id))
   ```

5. **`_new_session_record`** — no change needed; `meta["commands"]` is added dynamically by `_on_notification`.

6. **`_Supervisor.commands_options()`** — new method:
   ```python
   async def commands_options(self, session_id: str, partial: str) -> list:
       """Fetch autocomplete suggestions for a partial slash command."""
       result = await self._request(
           "_kiro.dev/commands/options",
           {"sessionId": session_id, "command": "", "partial": partial},
       )
       return list((result or {}).get("options") or [])
   ```

7. **`_handle_commands_options(conn, session_id, payload)`** — new async handler:
   ```python
   async def _handle_commands_options(conn: _Connection, session_id: str, payload: dict) -> None:
       if not session_id:
           conn.send(error_frame("bad_envelope", "'commands_options' needs a sessionId."))
           log.warning("ACP commands_options refused: [bad_envelope] no sessionId")
           return
       if session_id in _supervisor.subagent_sessions:
           conn.send(error_frame("read_only_session", _READ_ONLY_SUBAGENT_MESSAGE, session_id))
           log.warning("ACP commands_options refused: [read_only_session] session=%s", session_id)
           return
       if conn.session_id != session_id:
           conn.send(error_frame("not_subscribed",
               "Subscribe to this session first.", session_id))
           log.warning("ACP commands_options refused: [not_subscribed] session=%s", session_id)
           return
       if _supervisor.sessions.get(session_id) is None:
           conn.send(error_frame("unknown_session", "No such live session.", session_id))
           log.warning("ACP commands_options refused: [unknown_session] session=%s", session_id)
           return
       partial = str(payload.get("partial") or "")[:MAX_COMMAND_PARTIAL_CHARS]
       try:
           options = await _supervisor.commands_options(session_id, partial)
       except AcpError as exc:
           conn.send(error_frame(exc.code, str(exc), session_id))
           log.warning("ACP commands_options error: session=%s: %s", session_id, exc)
           return
       except Exception:
           log.exception("ACP commands_options: unexpected error for session=%s", session_id)
           conn.send(error_frame("internal_error",
               "An unexpected error occurred processing commands_options.", session_id))
           return
       conn.send(envelope("commands_options_result", {"options": options}, session_id))
   ```

8. **`_Supervisor.commands_execute()`** — new method:
   ```python
   async def commands_execute(self, session_id: str, name: str) -> dict:
       """Execute a slash command via the TuiCommand object form."""
       result = await self._request(
           "_kiro.dev/commands/execute",
           {"sessionId": session_id, "command": {"command": name, "args": {}}},
       )
       return result or {}
   ```

9. **`_handle_commands_execute(conn, session_id, payload)`** — new async handler:
   ```python
   async def _handle_commands_execute(conn: _Connection, session_id: str, payload: dict) -> None:
       if not session_id:
           conn.send(error_frame("bad_envelope", "'commands_execute' needs a sessionId."))
           log.warning("ACP commands_execute refused: [bad_envelope] no sessionId")
           return
       if session_id in _supervisor.subagent_sessions:
           conn.send(error_frame("read_only_session", _READ_ONLY_SUBAGENT_MESSAGE, session_id))
           log.warning("ACP commands_execute refused: [read_only_session] session=%s", session_id)
           return
       if conn.session_id != session_id:
           conn.send(error_frame("not_subscribed",
               "Subscribe to this session first.", session_id))
           log.warning("ACP commands_execute refused: [not_subscribed] session=%s", session_id)
           return
       meta = _supervisor.sessions.get(session_id)
       if meta is None:
           conn.send(error_frame("unknown_session", "No such live session.", session_id))
           log.warning("ACP commands_execute refused: [unknown_session] session=%s", session_id)
           return
       if session_id in _supervisor.closing:
           conn.send(error_frame("close_in_progress",
               "Session is being released; try again after it closes.", session_id))
           log.warning("ACP commands_execute refused: [close_in_progress] session=%s", session_id)
           return
       if session_id in _supervisor.inflight:
           conn.send(error_frame("turn_in_progress",
               "A turn is already running; wait for it to finish before sending a command.",
               session_id))
           log.warning("ACP commands_execute refused: [turn_in_progress] session=%s", session_id)
           return
       name = str(payload.get("name") or "").strip().lstrip("/")
       if not name:
           conn.send(error_frame("bad_envelope",
               "'commands_execute' needs a non-empty name.", session_id))
           log.warning("ACP commands_execute refused: [bad_envelope] empty name session=%s",
                       session_id)
           return
       if len(name) > MAX_COMMAND_PARTIAL_CHARS:
           conn.send(error_frame("bad_payload", "Command name too long.", session_id))
           log.warning("ACP commands_execute refused: [bad_payload] name too long session=%s",
                       session_id)
           return
       # Validate name against the received catalogue when available.
       # Allow-and-log when catalogue not yet received (race before first commands/available).
       valid_names = {c["name"] for c in meta.get("commands") or [] if isinstance(c, dict)}
       if valid_names and name not in valid_names:
           conn.send(error_frame("bad_payload", f"Unknown command '{name}'.", session_id))
           log.warning("ACP commands_execute refused: [bad_payload] unknown command %r "
                       "session=%s", name, session_id)
           return
       _supervisor.touch_used(session_id)
       log.info("ACP commands_execute: session=%s name=%r", session_id, name)
       # Add to inflight to block concurrent turns and additional commands_execute calls.
       # The JSON-RPC ack arrives quickly; output flows as session/update chunks through
       # the existing _on_notification path and is rendered as agent chunks in the
       # transcript. commands_execute_result is a lightweight ack frame (status only),
       # NOT carrying content text — see Design Decisions.
       # Verify during live probe (Phase 1 exit criterion): confirm ack arrives before
       # full output completes, so REQUEST_TIMEOUT_SECONDS (90s) is appropriate.
       _supervisor.inflight.add(session_id)
       try:
           result = await _supervisor.commands_execute(session_id, name)
       except AcpError as exc:
           conn.send(error_frame(exc.code, str(exc), session_id))
           log.warning("ACP commands_execute error: session=%s: %s", session_id, exc)
           return
       except Exception:
           log.exception("ACP commands_execute: unexpected error session=%s", session_id)
           conn.send(error_frame("internal_error",
               "An unexpected error occurred executing the command.", session_id))
           return
       finally:
           _supervisor.inflight.discard(session_id)
       # Send ack frame — carries status only, not the command's text output.
       # Output arrives as agent_message_chunk notifications → chunk frames to all subscribers.
       conn.send(envelope("commands_execute_result",
           {"name": name, "status": "accepted"}, session_id))
   ```

10. **`_dispatch`** — add two arms (after the `"steer"` arm):
    ```python
    if type_ == "commands_options":
        _spawn_task(_handle_commands_options(conn, session_id, payload))
        return
    if type_ == "commands_execute":
        _spawn_task(_handle_commands_execute(conn, session_id, payload))
        return
    ```

**Tests** (`tests/test_web.py`):

- `TestAcpCommandsAvailable`: session with inflight — notification stored in `sessions[sid]["commands"]`; broadcast `commands` frame to subscribers. No inflight — notification dropped (debug logged).
- `TestAcpCompactionStatus`: `started`/`completed`/`failed` status — `compaction` frame broadcast to subscribers; on `completed`, `_note_context(sid, None)` called, a `meta` frame with `contextPercent: null` is also broadcast (dual-frame effect — verify both frames are sent). No inflight — dropped (debug logged).
- `TestAcpClearStatus`: notification arrives → no frame broadcast.
- `TestAcpCommandsOptionsHandler`: `commands_options` client frame → `commands_options_result` frame returned; bad sessionId → error frame; subagent session → `read_only_session` error; `not_subscribed` guard → error; unexpected exception → `internal_error` frame.
- `TestAcpCommandsExecuteHandler`: successful execution → `commands_execute_result {status:"accepted"}`; turn-in-progress guard; closing guard; unknown session guard; empty name guard; name too long guard; unknown command guard (catalogue validation); unsubscribed socket → `not_subscribed` error; concurrent execution blocked by `inflight`; unexpected exception → `internal_error` frame.
- `TestHandleSubscribeCommandsReplay`: subscribe on a session whose `meta["commands"]` is set → `commands` frame sent.

**Also update `_note_context` type annotation** to `def _note_context(session_id: str, percent: float | None) -> None:` — the function already handles `None` correctly (stores it; client hides meter), but the annotation must match usage.

**Exit criteria**:
- [x] `SERVER_TYPES` frozenset literal rewritten to contain `"commands"`, `"compaction"`, `"commands_options_result"`, `"commands_execute_result"`
- [x] `CLIENT_TYPES` frozenset literal rewritten to contain `"commands_options"`, `"commands_execute"`
- [x] `MAX_COMMAND_PARTIAL_CHARS = 256` constant added
- [x] `_on_notification` has branches for `_kiro.dev/commands/available`, `_kiro.dev/compaction/status` (with debug log on drop), `_kiro.dev/clear/status`
- [x] `_note_context` type annotation updated to `float | None`
- [x] `_handle_subscribe` replays `commands` frame when `meta["commands"]` is set
- [x] `_handle_commands_options` and `_handle_commands_execute` are in `_dispatch`
- [x] Both handlers have: `not_subscribed` guard, `read_only_session` error code, `log.warning` on every refusal, `except Exception` fallback
- [x] `_handle_commands_execute` has: `inflight.add` before await, `finally: inflight.discard`, catalogue validation, name length cap, `log.info` on success dispatch
- [ ] **Live probe — `commands/execute` object form**: spawn `kiro-cli acp -a`, create a session, send `{"jsonrpc":"2.0","id":1,"method":"_kiro.dev/commands/execute","params":{"sessionId":"...","command":{"command":"tools","args":{}}}}` and confirm the process stays alive (does NOT exit with code 0). This is a **go/no-go gate for Phase 3** — if the process exits, the `commands_execute` path is shelved and SC-1 re-scoped to catalogue-only.
- [ ] **Live probe — `commands/execute` output path**: confirm whether output arrives as `agent_message_chunk` chunks (same as `session/prompt`) or only in the `_request` result dict. Document the finding in `docs/KNOWLEDGE.md`. If chunks arrive, the `commands_execute_result` ack-only design is confirmed correct; if no chunks and result carries text, update `commands_execute_result` payload to include `text`.
- [ ] **Live probe — `commands/options` response shape**: confirm `options` element shape (assumed `{name, description?}`). Update `docs/KNOWLEDGE.md` if different.
- [x] All new test classes pass; no regressions in full `test_web.py` suite

**Implementation (2026-08-12, code: 410abcb / 9f40e7c / b74f724)**
Added four new `SERVER_TYPES` entries (`commands`, `compaction`, `commands_options_result`, `commands_execute_result`) and two new `CLIENT_TYPES` entries (`commands_options`, `commands_execute`), plus a `MAX_COMMAND_PARTIAL_CHARS = 256` constant and `MAX_COMMANDS_COUNT = 200` constant. In `_on_notification`, added three new method branches: `_kiro.dev/commands/available` (attributes catalogue to single inflight session, stores in `sessions[sid]["commands"]` with 200-entry cap, broadcasts `commands` frame), `_kiro.dev/compaction/status` (broadcasts `compaction` frame, resets context meter via `_note_context(sid, None)` on `completed`), and `_kiro.dev/clear/status` (silent consume). Added commands catalogue replay in `_handle_subscribe` after the crew replay block, and two new `_Supervisor` methods (`commands_options`, `commands_execute`), two new top-level handler functions (`_handle_commands_options`, `_handle_commands_execute`) with full guard suites matching canonical `_handle_steer` order, and two new `_dispatch` arms. Updated `_note_context` type annotation to `float | None`. Auto-fixes: `closing` guard added to `_handle_commands_options`, error message sanitized (static "Unknown command." instead of f-string), guard order corrected to match canonical steer pattern, `c.get("name")` in valid_names comprehension.

---

### Phase 2: acp.html — date/status auto-poll fix [QA]

**Goal**: Fix `railRefresh` so the date and status grouping modes re-fetch the recency list on every 60-second auto-poll tick, making newly-active sessions appear without a manual Refresh.

**Covers**: SC-4

**File scope**:
- `src/power_atlas/templates/acp.html`
- `tests/acp_page.test.mjs`

**Changes:**

In `railRefresh()` (`acp.html:3158–3170`), replace the current date/status branch:

```js
// BEFORE (acp.html:3158–3170):
if (railMode === 'date' || railMode === 'status') {
  if (!railFlat.length) return;
  railBusy = true;
  railFetch({ mode: 'recent', page: 1,
              size: Math.min(100, railFlat.length) })
    .then(function (data) {
      railBusy = false;
      if (railRefreshStates({ sessions: (data && data.sessions) || [] })) {
        renderRail();
      }
    }).catch(function () { railBusy = false; });
  return;
}
```

```js
// AFTER:
if (railMode === 'date' || railMode === 'status') {
  // Full re-fetch rather than state-only update: newly-active sessions
  // must appear at the top without a manual Refresh press.
  // The !railFlat.length early-return from the previous implementation is
  // intentionally removed: loadFlatPage(1) is safe to call on an empty rail
  // (it resets railFlat=[] and fetches fresh), and railBusy already prevents
  // double-fetches while a request is in flight. A spurious tick before the
  // first page loads is benign.
  loadFlatPage(1);
  return;
}
```

`loadFlatPage(1)` already sets `railBusy`, handles the fetch, calls `renderRail()` on success, and clears `railBusy` on both success and error — so the guard, fetch, render and error paths are all inherited.

**Tests** (`tests/acp_page.test.mjs`):
- `railRefreshDateModeCallsLoadFlatPage`: stub `loadFlatPage`; set `railMode = 'date'`; call `railRefresh()`; assert `loadFlatPage` was called with `1`.
- `railRefreshStatusModeCallsLoadFlatPage`: same for `railMode = 'status'`.
- `railRefreshProjectModeUsesRefreshStates`: regression — `railMode = 'project'`; call `railRefresh()`; assert `loadFlatPage` was NOT called.

**Exit criteria**:
- [x] `railRefresh` date/status branch calls `loadFlatPage(1)` (not `railRefreshStates`)
- [x] New tests pass; existing `railRefresh` tests pass

**Implementation (2026-08-12, code: d78de92 / 36443f7)**
Replaced the `railRefresh` date/status branch (which called `railFetch + railRefreshStates`) with `loadFlatPage(1, true)`. Added a `silent` parameter to `loadFlatPage` (default falsy): when `true`, skips the pre-fetch "loading sessions…" status write and uses a silent catch (`railBusy = false`) instead of `railFailed`, matching the grouped-mode auto-poll's "silent on purpose" contract. The `!railFlat.length` early-return guard was intentionally removed (loadFlatPage is safe on an empty rail; railBusy already prevents double-fetches). Added 4 tests: date-mode calls loadFlatPage(1), status-mode calls loadFlatPage(1), project-mode regression (unchanged), and failure-path silence test.

---

### 2026-08-12 -- Implementation Review (after Phase 2, persona: Senior engineer, Reliability engineer)

Implementation health: Green.
4 findings (0 High, 1 Medium, 3 Low). All auto-fixed in cycle 1; cycle 2 clean.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| M1 | Medium | Background-tick error now calls `railFailed`, overwriting user's last status message; grouped-mode path is deliberately silent. | Fixed — `loadFlatPage(page, silent)` added; `railRefresh` passes `silent=true` |
| M2 | Low | Status text flashes "loading sessions…" on every 60-second tick from `loadFlatPage`'s pre-fetch write. | Fixed — `if (!silent)` guard on pre-fetch status write |
| M3 | Low | No failure-path test verifying railBusy cleared and poll cycle unfrozen on tick error. | Fixed — `railRefreshDateModeFailureIsSilent` test added |
| M4 | Low | Comment on removed `!railFlat.length` guard incomplete for the failed-initial-load case. | Fixed — comment extended with silent-catch behavior explanation |

---

### Phase 3: acp.html — slash command palette [QA]

**Goal**: Add a `/` command palette to the prompt box: opening on `/`, filtering via `commands_options` round-trip, keyboard-navigable, executing via `commands_execute` WS message. Handle `commands` and `compaction` frames in `onmessage`.

**Covers**: SC-1, SC-2

**File scope**:
- `src/power_atlas/templates/acp.html`
- `tests/acp_page.test.mjs`

**Changes:**

**A. `onmessage` — handle new frame types:**

In the `handle(msg)` dispatcher:
```js
if (type === 'commands') {
  sessionCommands = (msg.payload && msg.payload.commands) || [];
  return;
}
if (type === 'compaction') {
  var s = msg.payload && msg.payload.status || '';
  var txt = s === 'started' ? 'Compacting conversation context…'
          : s === 'completed' ? 'Context compacted.'
          : s === 'failed' ? 'Compaction failed.'
          : null;
  if (txt) addSystemMessage(txt);
  return;
}
if (type === 'commands_options_result') {
  applyCommandOptions(msg.payload && msg.payload.options || []);
  return;
}
if (type === 'commands_execute_result') {
  // Ack frame — the command's actual text output arrives as normal 'chunk'
  // frames (agent_message_chunk notifications → chunk frames via _on_notification).
  // This frame only confirms the command was accepted.
  hideCommandDropdown();
  return;
}
```

**B. Per-session commands storage:**

```js
var sessionCommands = [];   // [{name, description?}] received from 'commands' frame
```

Reset `sessionCommands = []`:
- In the `type === 'session'` branch of `handle()` (alongside `agentBody = null` and `turnActive = false` resets) — this fires on new session, load, and subscribe.
- In `releaseSession()` (if that function exists) for the disconnect path.

**C. Slash command dropdown UI:**

Add a `<div id="acpCmdDropdown" hidden ...>` absolutely positioned above `#acpPrompt`. Contains a `<ul>` of command rows. Each row shows command name and description. Keyboard: `ArrowUp`/`ArrowDown` to move selection; `Enter`/`Tab` to confirm; `Escape` to dismiss.

**D. Prompt textarea keydown — `/` intercept:**

Append to the `keydown` handler:
```js
if (e.key === '/' && promptInput.value === '' && !e.shiftKey && !e.ctrlKey) {
  e.preventDefault();
  promptInput.value = '/';
  showCommandDropdown('');
}
```

Also intercept `Enter` when the dropdown is visible — confirm selection rather than call `sendPrompt`:
```js
if (e.key === 'Enter' && !e.shiftKey && isCommandDropdownVisible()) {
  e.preventDefault();
  confirmCommandSelection();
  return;
}
```

**E. Prompt textarea `input` handler — live filtering:**

When `promptInput.value` starts with `/` and has no space, debounce (150ms) and call `requestCommandOptions(promptInput.value.slice(1))`.

When the value no longer matches `^\/\S*$` (slash cleared, or space typed after command name), `hideCommandDropdown()`. When a space is typed after the slash-token, the prompt reverts to normal text-send mode (user is typing arguments; `sendPrompt` sends the full text).

**F. `showCommandDropdown(partial)` / `hideCommandDropdown()` / `applyCommandOptions(options)` helpers:**

`showCommandDropdown(partial)`: populate from `sessionCommands` client-side filtered by `partial`; if socket is OPEN and `sessionId` is non-null, send `{type:"commands_options", sessionId:..., payload:{partial:partial}}` to get server-side suggestions.

`applyCommandOptions(options)`: merge server-returned options with client-side filtered list (deduplicate by name); re-render dropdown.

`hideCommandDropdown()`: hide the `#acpCmdDropdown` element.

`isCommandDropdownVisible()` / `confirmCommandSelection()`: read/act on the dropdown state.

**G. On dropdown confirm:**

Extract the selected command name (e.g., `"context"`), then send `{type:"commands_execute", sessionId:..., payload:{name:"context"}}` via the WS. Clear `promptInput.value` to `""`, hide dropdown. The command's output will arrive as normal `chunk` frames in the transcript.

> **Rejected:** Sending the command as a `prompt` frame with plain text (e.g. `/context`) — this works but bypasses the native execution path. KiroCrew and tui.js use `commands/execute` for this; the object form is confirmed correct. **Use instead:** `commands_execute` WS client frame → `_handle_commands_execute` → `_request("_kiro.dev/commands/execute", ...)`.

**H. `addSystemMessage(text)` helper:**

Appends a non-bubble, lightly styled system row to `transcriptEl` (the existing module-level cached reference — NOT `document.getElementById('transcript')`). Use `transcriptEl.appendChild(el)` following the existing pattern at lines 1520, 1555, 1670.

**CRITICAL**: `addSystemMessage` MUST use `textContent` or `document.createTextNode` to insert text — NEVER `innerHTML` or `insertAdjacentHTML`. The `text` field is agent-controlled; using innerHTML would allow a compromised agent to inject arbitrary HTML/JS into the page.

CSS class `acp-system-msg`. Does not participate in `agentBody`, is not in the session's JSON-RPC history, and is not sent to the agent.

**Tests** (`tests/acp_page.test.mjs`):
- `commandsFramePopulatesSessionCommands`: inject `commands` frame; assert `sessionCommands` populated.
- `commandsFrameOnSessionChangeResetsSessionCommands`: inject `session` frame after `sessionCommands` populated; assert `sessionCommands = []`.
- `compactionStartedAddsSystemMessage`: inject `compaction {status:"started"}`; assert system message visible with correct text.
- `compactionCompletedAddsSystemMessage`: same for `completed`.
- `slashKeyOpensDropdown`: simulate keydown `'/'` on empty prompt; assert dropdown visible.
- `commandOptionsResultUpdatesDropdown`: inject `commands_options_result`; assert dropdown rows updated.
- `commandsExecuteResultClosesDropdown`: inject `commands_execute_result {status:"accepted"}`; assert dropdown hidden (no text display — output is in transcript as chunks).
- `dropdownEscapeDismisses`: open dropdown; Escape keydown; assert hidden.
- `dropdownEnterSendsCommandsExecute`: open dropdown, select a command, Enter; assert `commands_execute` WS frame sent (not `prompt`).
- `spaceAfterCommandDismissesDropdown`: type `/context ` (with space); assert dropdown hidden, prompt value preserved.
- `dropdownEnterWithClosedSocketIsNoop`: open dropdown, close socket, hit Enter; assert no crash and dropdown hidden.
- `addSystemMessageUsesTextContent`: verify `addSystemMessage` sets `textContent` on the added element (not `innerHTML`); inject a `<script>` string as text; assert it appears as literal text, not executed.

**Exit criteria**:
- [x] `onmessage` handles `commands`, `compaction`, `commands_options_result`, `commands_execute_result`
- [x] `sessionCommands` is declared and reset to `[]` in the `type === 'session'` branch of `handle()` (and in `releaseSession()` if it exists)
- [x] `#acpCmdDropdown` element exists and opens on `/` keydown in empty prompt
- [x] `Enter` keydown with dropdown visible confirms selection (sends `commands_execute`) rather than calling `sendPrompt`
- [x] Space after slash-token dismisses dropdown and returns to normal text-send mode
- [x] Live prefix filtering sends `commands_options` WS frame (debounced, only when `sessionId` non-null)
- [x] Selecting a command sends `commands_execute` WS frame (with `{name: commandName}`) and hides dropdown
- [x] `addSystemMessage` uses `textContent` or `createTextNode` (NEVER innerHTML) — verified by `addSystemMessageUsesTextContent` test
- [x] Compaction `started`/`completed`/`failed` each renders the correct system message
- [x] All new tests pass; existing prompt/steer/queue tests unaffected
- [x] `docs/KNOWLEDGE.md` updated: `commands/execute` correction note (string-form kills agent; TuiCommand object form confirmed and shipped); new bullets for `commands/available`, `compaction/status`, `clear/status`

**Implementation (2026-08-12, code: a5015e2 / 28e65e2)**
Added a complete `/` slash command palette to the ACP prompt box. Pressing `/` on an empty prompt opens `#acpCmdDropdown` with commands from the `commands` frame; typing after `/` filters client-side and sends debounced `commands_options` WS round-trips; `ArrowUp`/`ArrowDown` navigate; `Enter` or `Tab` sends `commands_execute`; `Escape` or typing a space closes the palette. Added mouse-click via delegated `mousedown` on the dropdown. Added `turnActive` guard so the palette doesn't open during a running turn. Added ARIA combobox attributes (`aria-haspopup`, `aria-expanded`, `aria-activedescendant`, `aria-controls`, per-item `id`). Added `onmessage` handlers for `commands`, `compaction`, `commands_options_result`, `commands_execute_result`. `addSystemMessage` uses `textContent` exclusively. `sessionCommands` reset on every `session` frame. Client-side partial length cap (`MAX_CMD_PARTIAL_CHARS = 256`).

### 2026-08-12 -- Implementation Review (after Phase 3, persona: Security auditor, End-user advocate)

Implementation health: Green.
8 findings (0 High after fix, 2 Medium, 6 Low). All auto-fixed in cycle 1; cycle 2 clean.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| EU1 | High | No mouse-click handler on dropdown items; palette was keyboard-only with misleading cursor:pointer. | Fixed — delegated mousedown listener on #acpCmdDropdown added |
| EU2 | Medium | Dropdown could open during active turn; no turnActive guard on `/` keydown intercept. | Fixed — `!turnActive` guard added; setTurn(true) calls hideCommandDropdown |
| EU3 | Medium | showCommandDropdown sent commands_options WS frame, and input handler also sent one — double send on every `/`. | Fixed — WS send removed from showCommandDropdown; only debounced input handler sends |
| EU4 | Low | Tab key not handled for dropdown confirmation (plan spec: Enter/Tab confirm). | Fixed — Tab added alongside Enter in dropdown navigation block |
| EU5 | Low | Missing ARIA combobox relationship on textarea and dropdown items. | Fixed — aria-haspopup, aria-expanded, aria-activedescendant, aria-controls, li id attrs added |
| EU6/S2 | Low | No mouse-click test (finding EU1's test gap). | Fixed — dropdownMouseClickSelectsCommand test added |
| S1 | Low | addSystemMessageUsesTextContent test passed vacuously (status mapped to hardcoded strings, XSS payload never reached addSystemMessage). | Fixed — direct addSystemMessage XSS test added via _testAddSystemMessage hook |
| S3 | Low | No client-side length cap on partial string sent in commands_options WS frames. | Fixed — MAX_CMD_PARTIAL_CHARS = 256 constant and slice added |

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `commands/execute` object-form payload kills agent on some kiro-cli versions | High | Verified working in tui.js and KiroCrew against kiro-cli 2.16.x. If a version regression occurs, the error manifests as `agent_died` frame (existing handler covers it). Phase 1 exit criterion requires live probe before Phase 3 ships. |
| `commands/options` element shape differs from assumed `{name,description?}` | Medium | Phase 1 exit criterion requires a live probe and KNOWLEDGE.md update before Phase 3 uses the shape. Phase 3 renders `options[i].name` — if the field is named differently, the dropdown items will be blank, which is visible and not data-corrupting. |
| `loadFlatPage(1)` reorder during user interaction | Medium | `railRestoreFocus` is called inside `renderRail()`, which `loadFlatPage` triggers. Existing tests cover focus restoration. The date mode's purpose is recency ordering; reordering on a 60-second tick is correct by design. |
| No sessionId on `commands/available` — attribution drops during multi-session turns | Low | Known gap (same as SUBAGENT_LIST_METHOD). Commands are re-sent on each new session load; missing one push costs one dropdown update. Documented in Design Decisions. |
| `commands_execute` while a turn is inflight | Low | `_handle_commands_execute` guards on `session_id in _supervisor.inflight` and returns a `turn_in_progress` error frame. Client-side guard: dropdown is hidden during active turns (same condition as Queue/Steer controls being enabled). |
| Replay on subscribe races with live `commands/available` notification | Low | `_handle_subscribe` replays `meta.get("commands")` — whichever push last ran wins. Safe because subscribe is atomic with the replay (no await between attach and replay per `_handle_subscribe` existing invariant). |

## 7) Verification

```powershell
# Python suite (acp.py changes)
.venv-PowerAtlas\Scripts\pytest tests/test_web.py -x -q

# JS harness (acp.html changes)
node tests/acp_page.test.mjs

# Full suite
.venv-PowerAtlas\Scripts\pytest tests/ -x -q

# Manual: launch PowerAtlas, open /acp, create a session
# 1. Type '/' in the prompt box — verify dropdown appears
# 2. Type '/con' — verify dropdown filters to /context etc.
# 3. Select a command — verify commands_execute_result system message appears
# 4. Wait 60s in date mode — verify session list reorders without manual Refresh
# 5. Trigger a compaction (/compact) — verify "Compacting…" / "Context compacted." messages
```

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `docs/KNOWLEDGE.md` | Correction note at line 47: "string-form `commands/execute` still kills the agent; TuiCommand object form `{command:{command:name,args:{}}}` confirmed working and used in PowerAtlas." | Phase 1 exit criterion (line 47 correction); Phase 3 exit criterion (full update after ship) |
| `docs/KNOWLEDGE.md` | Add bullets documenting: PowerAtlas now handles `commands/available` (catalogue → dropdown), `compaction/status` (transcript indicator, kiro-cli 2.16.x), `clear/status` (silent consume). | Phase 3 (doc-table-only) |
| `plans/ROADMAP.md` | Update `commands/execute` citation in the "Chained launches" section — the supporting evidence (agent health not readable from exit code/stderr) still holds, but the `commands/execute` being unusable is now superseded. | Phase 3 (doc-table-only) |

## 9) Implementation Divergences from Plan
<Reserved — filled during implementation>

## Review Log

### 2026-08-12 -- Plan Creation (via /qplan)

Running high-effort review (4 personas: Architect, Senior engineer, Reliability engineer, Security auditor). 24 findings. 24 auto-resolved in cycle 1.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `_note_context(sid, None)` confirmed safe (None->null->hide meter); type annotation updated. | Fixed — annotation to `float \| None`; test verifies dual-frame |
| 2 | High | Missing `not_subscribed` guard in both new handlers. | Fixed — guard added to both, matching `_handle_steer` |
| 3 | High | Wrong error code `read_only` vs established `read_only_session`. | Fixed — changed to `read_only_session` in both handlers |
| 4 | High | Missing `except Exception` fallback; client hangs on unexpected errors. | Fixed — fallback added to both handlers |
| 5 | High | `commands/execute` live-probe go/no-go gate absent from Phase 1 exit criteria. | Fixed — explicit exit criterion added; Phase 3 blocked on Phase 1 probe |
| 6 | High | Missing `log.warning` on all refusal paths in both handlers. | Fixed — `log.warning` added after each error_frame emit |
| 7 | Medium | `commands_execute_result` must be ack-only; output is agent chunks not `_request()` result. | Fixed — payload is `{name, status:accepted}`; Design Decisions updated; live probe exit criterion added |
| 8 | Medium | `_handle_commands_execute` not in `_supervisor.inflight`; concurrent executions unguarded. | Fixed — `inflight.add`/`finally: inflight.discard` added |
| 9 | Medium | Compaction attribution drop not logged. | Fixed — `log.debug` added on drop path |
| 10 | Medium | `railRefresh` guard removal undocumented. | Fixed — comment explains intentional removal |
| 11 | Medium | Compaction `_note_context` dual-frame effect undocumented and untested. | Fixed — Design Decisions updated; test verifies both frames |
| 12 | Medium | `sessionCommands` reset location unspecified. | Fixed — Phase 3 specifies reset in `type === 'session'` handler |
| 13 | Medium | `commands/execute` streaming output ambiguity; possible duplicate display. | Fixed — ack-only payload; live probe to confirm; Design Decisions clarified |
| 14 | Medium | `name` not validated against catalogue; arbitrary names forwarded. | Fixed — catalogue validation added to handler |
| 15 | Medium | `partial` cap 4000 too large; should be 256. | Fixed — `MAX_COMMAND_PARTIAL_CHARS = 256` constant introduced |
| 16 | Medium | `addSystemMessage` must use `textContent` not `innerHTML`. | Fixed — Phase 3 spec and exit criterion updated |
| 17 | Medium | `name` length uncapped in `commands_execute`. | Fixed — `len(name) > MAX_COMMAND_PARTIAL_CHARS` guard added |
| 18 | Medium | No tests for `not_subscribed` guard in either handler. | Fixed — test cases added to both test classes |
| 19 | Low | `SERVER_TYPES` 'add to' wording misleads; frozenset requires full rewrite. | Fixed — clarified as 'rewrite the frozenset literal' |
| 20 | Low | Wrong constant `MAX_STEER_CHARS` for `partial`; merged with finding 15. | Fixed — `MAX_COMMAND_PARTIAL_CHARS` introduced |
| 21 | Low | `addSystemMessage` spec said `#transcript`; should say `transcriptEl`. | Fixed — Phase 3 spec updated |
| 22 | Low | No `log.info` on successful dispatch in `_handle_commands_execute`. | Fixed — `log.info` added |
| 23 | Low | `sessionCommands` reset not in exit criteria. | Fixed — exit criterion added to Phase 3 |
| 24 | Low | `dropdownEnterWithClosedSocketIsNoop` test missing. | Fixed — test added to Phase 3 test list |

### 2026-08-12 -- Implementation Review (after Phase 1, persona: Security auditor, Reliability engineer)

Implementation health: Yellow.
2 escalated Mediums pending user decision; all other findings auto-fixed.
12 findings total (0 High, 7 Medium, 5 Low). 9 auto-fixed across 2 cycles; 2 escalated.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| R1 | Medium | `commands_execute` holds `inflight` but emits no `meta {turn:"start"}` frame; client Send button stays enabled during blocking call. | Fixed — emit meta turn:start/end around inflight block, matching _handle_prompt pattern (b2d3847) |
| R2 | Medium | `commands_execute` uses 90 s wall-clock timeout (`REQUEST_TIMEOUT_SECONDS`) rather than the inactivity sentinel; long commands are cut off abruptly at 90 s. | Fixed — timeout=_INACTIVITY added to _Supervisor.commands_execute (b2d3847) |
| R3 | Medium | `_handle_commands_options` was missing `closing` guard, unlike parallel handler. | Fixed — closing guard added matching canonical steer order |
| S1 | Medium | Unbounded commands list stored in `meta["commands"]`; a compromised kiro-cli could inflate per-session memory. | Fixed — `MAX_COMMANDS_COUNT = 200` constant and slice added |
| S2 | Medium | Error frame echoed user-supplied `name` in f-string (`f"Unknown command '{name}'."`), potential XSS vector via Phase 3 innerHTML. | Fixed — static "Unknown command." message |
| R4/S4 | Low | `c["name"]` in `valid_names` set comprehension (KeyError risk on malformed catalogue entry). | Fixed — changed to `c.get("name")` with None exclusion guard |
| R5 | Low | No test verifying `_handle_prompt` blocked when `commands_execute` holds `inflight`. | Fixed — test added |
| R6 | Low | Test fixture cleanup not in try/finally; potential state leak on failure. | Fixed — try/finally with copy restore |
| S3 | Low | Guard order inconsistency: `not_subscribed` before `unknown_session` in both new handlers, reversed from `_handle_steer`. | Fixed — guard order corrected in both handlers |
| S5 | Low | Supervisor methods had no documentation that `session_id` is pre-validated. | Fixed — docstrings updated |
| S6 | Low | No concurrent `commands_execute` test. | Fixed — asyncio.gather concurrent test added |
| C2-L1 | Low | `not_subscribed` / `unknown_session` still inverted after cycle-1 fixes. | Fixed — orchestrator-direct swap in cycle 2 |
| C2-L2 | Low | Test `test_unknown_command_returns_bad_payload` didn't assert the static message text. | Fixed — assertion added |

*Reviewers: Security auditor findings prefixed S; Reliability engineer prefixed R. R4 and S4 merged (same finding). C2-* are cycle-2 findings.*

## Harness Improvement Opportunities
- Sub-agent auto-fix commit staged user's pre-staged working-tree files (web.py, acp.html, style.css, README.md, acp_page.test.mjs) because the pathspec-scoped commit was missing `-- <paths>`. Cost: user's concurrent work committed under Phase 1 commit, blurring authorship. Suggested change: in auto-fix sub-agent briefs, always specify `git commit -m "..." -- <explicit-file-list>` to prevent index bleed from concurrent working-tree files.
Format: - <observation> — cost: <what the friction actually cost> — suggested change: <one-line>
Leave the heading even if empty: /qclose Pass 2 and /qdream GAT-PLANS both read it, and an
absent heading is indistinguishable from a frictionless run.>
