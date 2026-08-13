# ACP Skill and Command Discovery Before First Turn

> **Date**: 2026-08-13
> **Status**: Exploring
> **Scope**: Parse and display kiro-cli skills in the /acp slash-command palette before the first user prompt

---

## Intent

### Problem statement & desired outcomes

The `/acp` slash-command palette (opened by pressing `/`) shows nothing until after the first turn completes. kiro-cli sends `_kiro.dev/commands/available` during session setup — before any prompt — carrying both slash commands (`params.commands`) and skills (`params.prompts`, distinguished by `serverName.startswith("skill:")`). PowerAtlas ignores the `prompts` field entirely, and the notification is also silently dropped when it arrives during the `session/new` round-trip (before `sessions[session_id]` is registered). A second delivery path via `session/update` with `sessionUpdate: "available_commands_update"` also goes unhandled. The result: the palette is empty on a fresh session and skills never appear at all.

Desired outcome: the palette shows skills (badged) and commands on first `/` keypress, even before the user sends a single prompt. Skills remain current after mid-session agent mode switches.

### Success criteria

- SC1: Pressing `/` on a freshly created session (no turns sent) shows both the 25 built-in commands and all 26 skills with a visible badge distinguishing skills from commands.
- SC2: Reconnecting to an existing session replays both commands and skills to the new subscriber (no re-prompt required).
- SC3: After a mid-session `/agentSelect` or agent mode switch, the palette reflects the updated skill/command list within the same session.
- SC4: No regression to existing commands behaviour — existing `TestAcpCommandsAvailable` tests continue to pass; command names, descriptions, and MAX_COMMANDS_COUNT truncation are unchanged.
- SC5: The `_pending_commands` buffer does not cause a session-creation race (commands/available notifications arriving for an in-flight `session/new` are correctly attributed once the session registers).

### Scope boundaries & non-goals

**In scope:**
- Parsing `params.get("prompts")` in the `_kiro.dev/commands/available` handler to extract skills.
- Adding a `_pending_commands` single-slot buffer to `_Supervisor` to handle the `session/new` attribution race.
- Adding a handler for `session/update` with `sessionUpdate: "available_commands_update"`.
- Storing `meta["skills"]` separately from `meta["commands"]` in session meta.
- Sending a `"skills"` WS frame alongside `"commands"` (broadcast + subscribe replay).
- Adding `"skills"` to `SERVER_TYPES` frozenset (acp.py:163).
- Frontend: handle `'skills'` WS frame, display skill entries with a badge, flat server-order sort.
- Extending `TestAcpCommandsAvailable` and `acp_page.test.mjs` for all new paths.

**Out of scope:**
- Argument completion for skills (the `_kiro.dev/commands/options` path is separate and already guarded against `command: ""`).
- Steering document display (also in `params.prompts` but with `_meta.kiro.type === "steering"`).
- v3 ACP session support (v3 uses a different notification shape; this targets v2 `kiro-cli acp -a`).
- Prompt entries (non-skill entries in `params.prompts`).

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

- **`_kiro.dev/commands/available` wire shape** (probe-verified, kiro-cli 2.16.x): `{commands: [{name, description, meta}], prompts: [{name, description, serverName, ...}], tools: [...]}`. Fires 5 times during `session/new` — 2 before the response, 3 after — all before any prompt. 25 commands + 26 prompts (all skills, `serverName='skill:config'`) in each delivery. (acp.py:2865, tui.js `handleCommandsAdvertising`)
- **Skills discriminant**: `entry.serverName.startswith("skill:")` in the prompts array. Confirmed by probe: `serverName='skill:config'` for all 26 skills. Regular prompts would have `serverName` of `"local"`, `"global"`, or an MCP server name.
- **`_new_session_record`** (acp.py:1616): returns `{cwd, created, last_used, last_activity}`. `commands` is not pre-populated — it is added dynamically by the `commands/available` handler (acp.py:2885). `skills` does not exist yet.
- **Attribution race** (structurally confirmed by code + probe): During `session/new`, `self.sessions` is empty when `_on_notification` runs for `commands/available`. `asyncio.Future.set_result()` schedules the waiting coroutine as a new callback at the tail of the ready queue; the notification callback runs first. Result: `inflight==0, sessions==0` → drop at acp.py:2888. No buffer mechanism exists.
- **`load_session` does not have the race**: registers `sessions[session_id]` at acp.py:3047 *before* `await self._request("session/load", ...)` at acp.py:3056. A `commands/available` during a load finds the session in `sessions`.
- **`_handle_subscribe` already replays `meta["commands"]`** (acp.py:3647–3651): sends `envelope("commands", {"commands": commands}, sid)` if `meta.get("commands") is not None`. Same pattern needed for `meta["skills"]`.
- **`available_commands_update` falls through** (acp.py:2671–2923): `method == "session/update"`, `kind == "available_commands_update"` — no branch matches it. Debug-logged and discarded. The `availableCommands` array in its payload uses `_meta.kiro.type` (`"skill"` / `"prompt"` / `"steering"`) to distinguish entries.
- **`SERVER_TYPES` frozenset** (acp.py:163–168): `envelope()` raises `ValueError` on any unlisted type. Must add `"skills"` before sending the new frame.
- **Frontend `sessionCommands`** (acp.html:642): array of `{name, description}` objects. Reset on every `'session'` frame (acp.html:5258) and in `releaseSession()` (acp.html:1150). Populated only by `'commands'` WS frame (acp.html:5336). No `sessionSkills` variable exists.
- **`showCommandDropdown` / `renderCommandDropdown`** (acp.html:1681–1750): filters `sessionCommands` by partial text, renders flat `<li>` elements with `.acp-cmd-name` and `.acp-cmd-desc` spans. No per-type logic. CSS badge will require a new span and a small style block.
- **`MAX_COMMANDS_COUNT = 200`** (acp.py:189): applied to commands slice. Apply same cap to skills.
- **`TestAcpCommandsAvailable`** class in `tests/test_web.py` (~line 16250): tests cover inflight attribution, zero-sessions drop, name filtering, eviction, truncation — all using `params.get("commands")` only. No tests for `prompts`, `skills`, `available_commands_update`, or the `session/new` race.
- **`acp_page.test.mjs`**: `commandsFramePopulatesSessionCommands`, `slashKeyOpensDropdown`, etc. — no `'skills'` frame test or badge rendering test.

### 5. Risks & mitigations

- **R1: `_pending_commands` buffer concurrency** — `_Supervisor` runs on the event loop (single-threaded for state mutations); the buffer is a plain `dict | None` attribute, no locking needed. Risk: low.
- **R2: `available_commands_update` sessionId absent** — the wire shape is [unverified] from tui.js source; if sessionId is absent, the new handler must fall back to the inflight/sessions count attribution rather than crashing. Mitigation: defensive `params.get("sessionId")` with fallback.
- **R3: Skills and commands diverging in size** — probe shows 26 skills today; future MCP-served skills could grow. `MAX_COMMANDS_COUNT` cap applied to both independently prevents unbounded growth.
- **R4: `SERVER_TYPES` omission** — `envelope()` raises `ValueError` at runtime, not import time. Mitigation: add `"skills"` to the frozenset as part of the same edit that sends the frame; test coverage catches it.
- **R5: Frontend `session` frame resets `sessionCommands` but not `sessionSkills`** — the reset at acp.html:5258 only resets `sessionCommands`. A new `sessionSkills = []` reset must be added in the same place, plus `releaseSession()`.

### 6. Resolved decisions

- Q1: How to fix the session/new attribution race for commands/available? — A: single-slot `_pending_commands` buffer — Decision: add `self._pending_commands: list | None = None` to `_Supervisor`; when `commands/available` fires with `sessions==0, inflight==0, _reserved>0`, store `(commands, skills)` in the buffer; flush it immediately after `sessions[session_id] = _new_session_record(cwd)` in `new_session`.
- Q2: Visual treatment for skills in the palette? — A: flat sort + badge — Decision: keep server order (no sort), add a muted inline `skill` badge (a `<span class="acp-cmd-skill-badge">` with a small CSS label) on skill entries only. Commands render as before.
- Q3: Handle available_commands_update for mid-session mode switches? — A: yes — Decision: add an `elif kind == "available_commands_update"` branch in `_on_notification` that updates `meta["commands"]` and `meta["skills"]`, broadcasts both frames. Attribution via `params.get("sessionId")` direct lookup; fallback to inflight/sessions count if absent.

### 7. Open items

- **O1 (execution-contingent)**: Whether `available_commands_update` payload uses `availableCommands[].serverName.startswith("skill:")` or `availableCommands[]._meta.kiro.type == "skill"` for the skill discriminant. tui.js uses `_meta.kiro.type`; the `commands/available` path uses `serverName`. Implementation should try `_meta.kiro.type` first (present on `available_commands_update`), fall back to `serverName` for `commands/available`. Verify against a live mode-switch capture during `/qdev`.
- **O2 (deterministic)**: Confirm `_pending_commands` is correctly cleared when `new_session` fails (exception path). The `finally` block in `new_session` must clear it to avoid stale buffered data leaking to a subsequent `new_session` call.

### 8. Recommended approach

**Phase 1 — Backend (acp.py)**

1. Add `"skills"` to `SERVER_TYPES` frozenset (acp.py:163).
2. Add `self._pending_commands: tuple[list, list] | None = None` to `_Supervisor.__init__`.
3. In `_on_notification` `_kiro.dev/commands/available` handler (acp.py:2865):
   - Parse `params.get("prompts")` to extract skills: `[{name, description}]` where `serverName.startswith("skill:")`, capped at `MAX_COMMANDS_COUNT`.
   - When `sid` is resolved: store `meta["commands"] = commands` and `meta["skills"] = skills`; broadcast both `envelope("commands", ...)` and `envelope("skills", ...)`.
   - When `sid` is None but `_reserved > 0` (session/new in flight): store `(commands, skills)` in `self._pending_commands`.
4. In `_Supervisor.new_session` (acp.py:2998), after `self.sessions[session_id] = _new_session_record(cwd)`: flush `_pending_commands` — apply to the new session meta and broadcast.
5. Add `elif kind == "available_commands_update"` branch in `_on_notification`: parse `availableCommands` by `_meta.kiro.type`; update `meta["commands"]` and `meta["skills"]`; broadcast both frames. Attribute via sessionId; fallback to inflight/sessions count.
6. In `_handle_subscribe` (acp.py:3647): send `meta.get("skills")` frame alongside the existing `meta.get("commands")` frame.

**Phase 2 — Frontend (acp.html)**

1. Add `var sessionSkills = [];` alongside `sessionCommands`.
2. Reset `sessionSkills = []` in the `'session'` frame handler and `releaseSession()`.
3. Add `'skills'` WS frame handler: `sessionSkills = (payload && payload.skills) || [];`.
4. Update `showCommandDropdown(partial)`: merge `sessionCommands` (type `"command"`) and `sessionSkills` (type `"skill"`) into one flat list, filter by partial, preserve order (skills entries keep their server position — no promotion).
5. Update `renderCommandDropdown`: if item has `type === "skill"`, add `<span class="acp-cmd-skill-badge">skill</span>` after the name span.
6. Add CSS for `.acp-cmd-skill-badge`: small muted label, inline, similar treatment to existing meta chips.

**Phase 3 — Tests**

1. Extend `TestAcpCommandsAvailable` (test_web.py): add tests for `prompts` parsing, skill extraction by `serverName`, `_pending_commands` flush on session register, `available_commands_update` handler.
2. Extend `acp_page.test.mjs`: add tests for `'skills'` frame populating `sessionSkills`, badge rendering, merged dropdown filtering.

### 9. QA environment

- Start PowerAtlas: `.venv-PowerAtlas\Scripts\power-atlas` (or via tray). Open `/acp` in browser at `http://127.0.0.1:<port>/acp`.
- Create a new session from any kiro-cli workspace. Press `/` immediately — before sending any prompt. Verify palette shows skills with badge.
- Python tests: `.venv-PowerAtlas\Scripts\pytest tests/test_web.py -k "Commands"` for fast targeted run.
- JS tests: `node tests/acp_page.test.mjs`.
- Hard-reload (`Ctrl+Shift+R`) required after acp.html edits — no restart needed per AGENTS.md.

## Harness Improvement Opportunities

- The probe step (running a live kiro-cli subprocess to settle the commands/available timing) was not covered by the trio's "decidable-by-probe" list in the way it should have been — the mutation-finder labeled it as open ("requires live observation") but it was settable with a 15-line script. The `/qexplore` decidable-by-probe gate could benefit from a note clarifying that "launch a local subprocess and read its stdout" counts as a read-only probe the orchestrator can and should run before the interview. Cost: one extra Q&A round that could have been replaced by the probe result.
