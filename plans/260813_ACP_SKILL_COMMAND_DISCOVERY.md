# ACP Skill and Command Discovery Before First Turn

> **Date**: 2026-08-13
> **Status**: Draft
> **Scope**: Parse and display kiro-cli skills in the /acp slash-command palette before the first user prompt
> **Estimated effort**: 1 day

---

## Intent

### Problem statement & desired outcomes

The `/acp` slash-command palette (opened by pressing `/`) shows nothing until after the first turn completes. kiro-cli sends `_kiro.dev/commands/available` during session setup — before any prompt — carrying both slash commands (`params.commands`) and skills (`params.prompts`, distinguished by `serverName.startswith("skill:")`). PowerAtlas ignores the `prompts` field entirely, and the notification is also silently dropped when it arrives during the `session/new` round-trip (before `sessions[session_id]` is registered). A second delivery path via `session/update` with `sessionUpdate: "available_commands_update"` also goes unhandled. The result: the palette is empty on a fresh session and skills never appear at all.

Desired outcome: the palette shows skills (badged) and commands on first `/` keypress, even before the user sends a single prompt. Skills remain current after mid-session agent mode switches.

### Success criteria

- SC1: Pressing `/` on a freshly created session (no turns sent) shows both the 25 built-in commands and all 26 skills with a visible badge distinguishing skills from commands.
- SC2: Reconnecting to an existing session replays both commands and skills to the new subscriber (no re-prompt required).
- SC3: After a mid-session `/agentSelect` or agent mode switch, the palette reflects the updated skill/command list within the same session (when kiro-cli includes `sessionId` in the `session/update` notification; see known limitation in §6).
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

## 1) Current State

**`_kiro.dev/commands/available` handler** (acp.py:2865–2889): reads `params.get("commands")` only — a list of `{name, description, meta}` slash-command dicts. `params.get("prompts")` is never read. Skills in the `prompts` array are silently ignored.

**Attribution logic** (acp.py:2875–2889): when notification arrives: if `len(inflight)==1` → attribute to that session; if `len(inflight)==0 and len(sessions)==1` → attribute to that session; otherwise drop with debug log.

**The session/new race** (probe-verified 2026-08-13, kiro-cli 2.16.x): `_kiro.dev/commands/available` fires 5 times during `session/new` — 2 before the response, 3 after — all before any prompt. `asyncio.Future.set_result()` in `_on_response` schedules the waiting coroutine as a new queued callback; the notification callback runs first. When `_on_notification` processes `commands/available`, `self.sessions` is still empty (the session is registered at acp.py:2998 only after `_request("session/new", ...)` returns). Result: `inflight==0, sessions==0` → all five `commands/available` notifications are dropped. **No buffer mechanism exists** to hold them until the session registers.

**`load_session` does not have the race** (acp.py:3047): registers `sessions[session_id]` before `await self._request("session/load", ...)` at acp.py:3056. A `commands/available` arriving during a load finds the session populated.

**`available_commands_update`** (acp.py:2671–2923): a `session/update` notification with `kind == "available_commands_update"` matches no branch in `_on_notification`. Falls to the debug-log fall-through at acp.py:2915. Silently discarded.

**`_handle_subscribe` replay** (acp.py:3647–3651): already sends `meta.get("commands")` if not None on every new subscriber. The pattern for a `skills` replay is identical; the key does not yet exist.

**`SERVER_TYPES` frozenset** (acp.py:163–168): `envelope()` raises `ValueError` on any type not in the set. `"skills"` must be added before sending that frame type.

**`_reserved`** (acp.py:1681): incremented before `session/new` round-trip, decremented in `finally`. With `MAX_SESSIONS=8`, up to 8 concurrent `new_session` calls can hold `_reserved > 0` simultaneously. The single-slot `_pending_commands` buffer works correctly only for sequential creation; concurrent creation means only the last buffered notification is flushed (see §3 Design Decisions).

**Frontend `sessionCommands`** (acp.html:681): populated by the `'commands'` WS frame handler (acp.html:5372). Reset on every `'session'` frame (acp.html:5258) and in `releaseSession()` (acp.html:1150). No `sessionSkills` variable exists. `showCommandDropdown` (acp.html:1720) and `renderCommandDropdown` (acp.html:1729) render only `sessionCommands` with no per-type logic and no CSS badge mechanism.

**Tests**: `TestAcpCommandsAvailable` in tests/test_web.py (~line 16250) covers existing commands attribution, truncation, and filtering. No tests exist for `prompts` parsing, skill extraction, `_pending_commands` buffer, or `available_commands_update`. `acp_page.test.mjs` has `commandsFramePopulatesSessionCommands` and `slashKeyOpensDropdown` but no `'skills'` frame or badge tests.

## 2) Goal

Fix two root causes in `acp.py` — ignoring the `prompts` field and dropping notifications during `session/new` — and handle `available_commands_update`; then surface skills in the frontend palette with a badge. The result: the palette is populated before the first turn and stays current after mode switches.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Race fix approach | Single-slot `_pending_commands: tuple[list,list] \| None` buffer on `_Supervisor`; flushed immediately after `sessions[session_id]` is set in `new_session` | (A) Pre-register session before `_request`; (B) re-request commands after session registers; (C) no buffer, accept the drop | No new infrastructure; event loop is single-threaded so no locking needed; mirrors how `_registry.loading` defers sockets during `session/load`. Option B would require calling `_kiro.dev/commands/options` with an empty command (kills kiro-cli per project memory). |
| Single-slot buffer for concurrent new_session | Accepted limitation: single-slot means last-writer wins. With all 5 `commands/available` notifications carrying identical content (probe-verified: same catalogue on every delivery), dropping earlier notifications is safe today. A FIFO queue would be correct in principle but adds complexity without a practical benefit. | FIFO `deque[tuple[list,list]]` keyed to flush in order | The ProbeUI creates one session at a time; concurrent `new_session` (≥2 tabs racing) is possible but rare. Document the limitation; add a `log.debug` when the slot is replaced. |
| Skills storage | Separate `meta["skills"]` key alongside `meta["commands"]` | Merged list with `type` field on each entry | Frontend needs to badge skill entries differently; a merged list requires embedding `type` in every entry, changing the existing `commands` frame shape and breaking SC4. Separate key keeps existing `commands` path unchanged. |
| Shared skill-extraction helper | Extract `_parse_skills(entries: list[dict]) -> list[dict]` helper; call from both `commands/available` and `available_commands_update` handlers | Duplicate list-comprehension in each handler | Eliminates DRY violation; future skill-discriminant changes need one edit. Helper checks `_meta.kiro.type == "skill"` first (used by `available_commands_update`), falls back to `serverName.startswith("skill:")` (used by `commands/available`). |
| `available_commands_update` discriminant | Try `_meta.kiro.type == "skill"` first (nested at entry `_meta.kiro.type`); fall back to `serverName.startswith("skill:")` | `serverName` only | `available_commands_update` uses `_meta.kiro.type` (confirmed in tui.js); `commands/available` uses `serverName`. Single helper using both prevents a silent miss if either field is absent. |
| `available_commands_update` field path | **Execution-contingent (O1)**: verify during Phase 1 whether `availableCommands` lives at `params` level or nested under `params["update"]`. Probe a live mode-switch capture. | N/A | The tui.js source shows `e.availableCommands` on the update payload; `_on_notification`'s `update = params.get("update") or {}`, so the correct key is `update.get("availableCommands")` if `availableCommands` is inside the `update` object — or `params.get("availableCommands")` if not. Must verify before shipping. |
| Frontend sort | Flat server order, no promotion | Skills sorted first | User decision (Q2 in exploration). Skills are badged so they're identifiable without reordering. |
| Badge implementation | `<span class="acp-cmd-skill-badge">skill</span>` appended to `<li>` as a sibling after the name span | Inside `nameSpan`; color-only | `li.appendChild(badge)` after nameSpan keeps it as a flex sibling; text label is unambiguous even if CSS fails to load. |
| `_pending_commands` cleared on `new_session` failure | Yes — cleared in the `finally` block (load-bearing for the pre-flush-exception path) | Cleared only on success | An exception during `session/new` before the flush block is reached must not leave stale buffered data that would be applied to the next `new_session` call's session. The flush itself should catch and log broadcast failures rather than propagating them. |

## 4) External Dependencies & Costs

### Required external changes

None. Code-only change.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Backend — parse skills, fix race, replay on subscribe [QA]

**Goal**: Fix the two root causes in `acp.py`: (1) add the `_parse_skills` helper and parse `params.get("prompts")` to extract skills, (2) add the `_pending_commands` buffer to survive the `session/new` race, (3) add the `available_commands_update` handler, and (4) replay `meta["skills"]` in `_handle_subscribe`.

**Covers**: SC1, SC2, SC3, SC4, SC5

**File scope**: `src/power_atlas/acp.py`

**Changes:**

**1. Add `"skills"` to `SERVER_TYPES`** (acp.py:163–168):
```python
SERVER_TYPES = frozenset({
    "session", "chunk", "rendered", "tool_call", "tool_update", "meta", "error",
    "agent_died", "session_closed", "history_truncated", "history", "thought",
    "subagents", "steer_ack",
    "commands", "skills",  # <-- add "skills"
    "compaction", "commands_options_result", "commands_execute_result",
})
```

**2. Add `_parse_skills` helper** — add as a module-level function near `_as_text` and `MAX_COMMANDS_COUNT` (around acp.py:189). This is the single extraction point for both handlers:
```python
def _parse_skills(entries: list) -> list:
    """Extract skill entries from a prompts/availableCommands list.

    Accepts entries from either delivery path:
    - _kiro.dev/commands/available: uses entry.serverName.startswith("skill:")
    - session/update available_commands_update: uses entry._meta.kiro.type == "skill"

    Both discriminants are checked so neither path silently misses skills.
    """
    result = []
    for s in entries:
        if not isinstance(s, dict):
            continue
        name = _as_text(s.get("name")).lstrip("/")
        if not name:
            continue
        # _meta.kiro.type is the canonical discriminant on available_commands_update
        kiro_meta = s.get("_meta")
        if isinstance(kiro_meta, dict):
            kiro = kiro_meta.get("kiro")
            if isinstance(kiro, dict) and kiro.get("type") == "skill":
                result.append({"name": name, "description": _as_text(s.get("description"))})
                continue
        # serverName.startswith("skill:") is the discriminant on commands/available
        if _as_text(s.get("serverName")).startswith("skill:"):
            result.append({"name": name, "description": _as_text(s.get("description"))})
    return result[:MAX_COMMANDS_COUNT]
```

**3. Add `_pending_commands` to `_Supervisor.__init__`** — immediately after `self._reserved = 0` (acp.py:1681):
```python
self._reserved = 0
# Buffered (commands, skills) from _kiro.dev/commands/available notifications
# that arrived during a session/new round-trip before sessions[session_id] was
# set. Cleared in new_session()'s finally block on every exit path.
# Single-slot: last-writer wins. All 5 observed notifications carry identical
# content (probe-verified kiro-cli 2.16.x), so earlier discards are safe.
# A log.debug fires when the slot is replaced to make discards observable.
self._pending_commands: tuple[list, list] | None = None
```

**4. Update the `_kiro.dev/commands/available` handler** (acp.py:2865–2889) — replace the entire handler block:
```python
if method == "_kiro.dev/commands/available":
    commands = [
        {"name": _as_text(c.get("name")).lstrip("/"),
         "description": _as_text(c.get("description"))}
        for c in (params.get("commands") or [])[:MAX_COMMANDS_COUNT]
        if isinstance(c, dict) and _as_text(c.get("name"))
    ]
    skills = _parse_skills(params.get("prompts") or [])
    inflight = self.inflight
    sid = None
    if len(inflight) == 1:
        sid = next(iter(inflight))
    elif len(inflight) == 0 and len(self.sessions) == 1:
        sid = next(iter(self.sessions))
    if sid is not None:
        meta = self.sessions.get(sid)
        if meta is not None:
            meta["commands"] = commands
            meta["skills"] = skills
            _registry.broadcast(sid, envelope("commands", {"commands": commands}, sid))
            _registry.broadcast(sid, envelope("skills", {"skills": skills}, sid))
    elif len(inflight) == 0 and len(self.sessions) == 0 and self._reserved > 0:
        # session/new is in flight — buffer for flush in new_session().
        if self._pending_commands is not None:
            log.debug("ACP commands_available: replacing buffered pending commands "
                      "(last-writer wins, concurrent new_session window)")
        self._pending_commands = (commands, skills)
    else:
        log.debug("ACP commands_available: %d session(s) inflight, %d known — "
                  "cannot attribute; dropped", len(inflight), len(self.sessions))
    return
```

**5. Add `available_commands_update` handler** — add as a new `elif kind == "available_commands_update"` branch in `_on_notification`'s `kind`-based dispatch chain, immediately after the `kind == "agent_thought_chunk"` block (around acp.py:2844) and before `if method == "_kiro.dev/commands/available"` (acp.py:2865):

> **Pre-implementation check (O1)**: before writing this code, verify whether `availableCommands` lives at `params` level or nested inside `params["update"]`. In `_on_notification`, `update = params.get("update") or {}`. If kiro-cli sends `{"params": {"sessionId": "...", "update": {"sessionUpdate": "available_commands_update", "availableCommands": [...]}}}`, read from `update.get("availableCommands")`. If it sends `{"params": {"sessionId": "...", "availableCommands": [...], "update": {"sessionUpdate": "available_commands_update"}}}`, read from `params.get("availableCommands")`. Capture one live `available_commands_update` frame (e.g., via a `/agentSelect` during the probe script) and check which nesting is used. The code below assumes `update.get("availableCommands")` — correct the read path if the probe shows otherwise.

```python
if kind == "available_commands_update":
    # Mid-session agent mode switch: kiro-cli re-advertises the full catalogue.
    # NOTE: verify availableCommands nesting against a live probe before shipping
    # (see O1 in §3). Assuming update.get("availableCommands") here.
    available = update.get("availableCommands") or []
    # commands = non-skill entries (exclude skill, prompt, steering by _meta.kiro.type
    # or serverName; on this path _meta.kiro.type is the canonical discriminant)
    commands = [
        {"name": _as_text(c.get("name")).lstrip("/"),
         "description": _as_text(c.get("description"))}
        for c in available
        if isinstance(c, dict)
        and _as_text(c.get("name"))
        and not (
            (isinstance(c.get("_meta"), dict)
             and isinstance(c["_meta"].get("kiro"), dict)
             and c["_meta"]["kiro"].get("type") in ("skill", "steering", "prompt"))
            or _as_text(c.get("serverName")).startswith("skill:")
        )
    ][:MAX_COMMANDS_COUNT]
    skills = _parse_skills(available)
    # Attribution: sessionId direct lookup first (works for multi-session);
    # fall back to inflight/sessions count (single-session heuristic).
    sid = session_id if (session_id and session_id in self.sessions) else None
    if sid is None:
        inflight = self.inflight
        if len(inflight) == 1:
            sid = next(iter(inflight))
        elif len(inflight) == 0 and len(self.sessions) == 1:
            sid = next(iter(self.sessions))
    if sid is not None:
        meta = self.sessions.get(sid)
        if meta is not None:
            meta["commands"] = commands
            meta["skills"] = skills
            _registry.broadcast(sid, envelope("commands", {"commands": commands}, sid))
            _registry.broadcast(sid, envelope("skills", {"skills": skills}, sid))
    else:
        log.debug("ACP available_commands_update: cannot attribute; dropped "
                  "(multi-session without sessionId in params, or no sessions)")
    return
```

**6. Flush `_pending_commands` in `new_session`** (acp.py:2998–3003) — after `self.sessions[session_id] = _new_session_record(cwd)`, before `_publish_live()`:
```python
self.sessions[session_id] = _new_session_record(cwd)
# Flush any commands/available notifications buffered during this session/new
# round-trip. Broadcast failures are caught so they don't abort registration.
if self._pending_commands is not None:
    pending_commands, pending_skills = self._pending_commands
    self._pending_commands = None  # consume before broadcast
    meta = self.sessions.get(session_id)
    if meta is not None:
        meta["commands"] = pending_commands
        meta["skills"] = pending_skills
        try:
            _registry.broadcast(session_id,
                                envelope("commands", {"commands": pending_commands},
                                         session_id))
            _registry.broadcast(session_id,
                                envelope("skills", {"skills": pending_skills},
                                         session_id))
            log.debug("ACP new_session: flushed %d pending commands, %d pending skills",
                      len(pending_commands), len(pending_skills))
        except Exception:
            log.warning("ACP new_session: flush broadcast failed for session %s",
                        session_id, exc_info=True)
self._publish_live()
self.history[session_id] = _History()
```

Also clear `_pending_commands` in the `finally` block of `new_session` (load-bearing for the pre-flush-exception path — if an exception occurs before the flush runs, this prevents the buffer from leaking to the next `new_session` call):
```python
finally:
    self._reserved -= 1
    # Load-bearing: clears the buffer if an exception prevented the flush above.
    # On the success path the flush already set this to None; this is a no-op then.
    self._pending_commands = None
```

**7. Replay `meta["skills"]` in `_handle_subscribe`** (acp.py:3647–3651) — after the existing `commands` replay block:
```python
commands = meta.get("commands")
if commands is not None:
    conn.send(envelope("commands", {"commands": commands}, session_id))
skills = meta.get("skills")
if skills is not None:
    conn.send(envelope("skills", {"skills": skills}, session_id))
```

**Exit criteria**:
- [ ] `"skills"` added to `SERVER_TYPES` frozenset at acp.py:163
- [ ] `_parse_skills(entries)` helper added as a module-level function near `_as_text`
- [ ] `self._pending_commands` initialized to `None` in `_Supervisor.__init__` adjacent to `self._reserved = 0` (acp.py:1681)
- [ ] `_kiro.dev/commands/available` handler uses `_parse_skills(params.get("prompts") or [])`, stores `meta["skills"]`, broadcasts `"skills"` frame
- [ ] O1 resolved: `available_commands_update` handler reads `availableCommands` from the correct nesting (verified by probe); comment updated to confirm
- [ ] `available_commands_update` `elif kind == ...` branch added in `_on_notification`'s kind-dispatch chain
- [ ] `_pending_commands` buffer flushed in `new_session` after session registration, cleared in `finally` block; broadcast failure caught and logged
- [ ] `_handle_subscribe` sends `meta["skills"]` frame alongside `meta["commands"]` frame
- [ ] `.venv-PowerAtlas\Scripts\pytest tests/test_web.py -k "TestAcpCommandsAvailable"` passes (no regression)

### Phase 2: Backend — tests [QA]

**Goal**: Extend `TestAcpCommandsAvailable` to cover all new paths added in Phase 1.

**Covers**: SC4, SC5

**File scope**: `tests/test_web.py`

**Why horizontal**: Phase 2 is test-only for Phase 1's backend changes. The JS tests in Phase 4 are separate because `acp_page.test.mjs` is a JS harness, not pytest, and its test logic depends on Phase 3's frontend changes.

**Note on async tests**: `new_session` is `async`. Tests 4 and 5 must either use `@pytest.mark.asyncio` + `async def`, or extract a sync helper `_flush_pending_commands(session_id)` from `new_session` and test that helper directly (simpler: avoids needing to mock `ensure_started`, `_request`, `_publish_live`). **Preferred approach**: extract the flush into a sync `_flush_pending_commands(self, session_id: str)` method and call it in `new_session`; tests call the sync helper directly. Check existing test suite for whether `pytest-asyncio` is already a dev dependency before deciding.

**New test cases to add to `TestAcpCommandsAvailable`:**

1. `test_prompts_field_extracts_skills` — send `commands/available` with `prompts` containing two skill entries (`serverName="skill:config"`) and one non-skill prompt (`serverName="local"`); assert `meta["skills"]` contains exactly the two skills and `meta["commands"]` is unchanged.

2. `test_prompts_field_extracts_skills_by_meta_type` — same as above but use `_meta={"kiro": {"type": "skill"}}` instead of `serverName`; assert same outcome.

3. `test_skills_broadcast_frame_sent` — send `commands/available` with skills; assert the WS subscriber receives a `"skills"` frame with the extracted skills list.

4. `test_pending_commands_buffer_flushed_on_session_register` — call `_flush_pending_commands(session_id)` on a supervisor with `_pending_commands = (commands, skills)` set; assert `meta["commands"]` and `meta["skills"]` are populated, both WS frames broadcast, and `_pending_commands` is `None` after the call.

5. `test_pending_commands_buffer_cleared_when_broadcast_raises` — set `_pending_commands`, make `_registry.broadcast` raise; assert `_pending_commands` is `None` and `meta["commands"]` holds the pending value (meta was written before the broadcast failed).

6. `test_pending_commands_slot_replaced_on_second_notification` — set `_pending_commands = (old_cmds, old_skills)`; send a second `commands/available` notification with `sessions={}` and `_reserved=1`; assert `_pending_commands` holds the new values and a debug log was emitted.

7. `test_available_commands_update_updates_skills` — send `session/update` with `sessionUpdate="available_commands_update"` and an `availableCommands` array containing one slash command and two skills (via `_meta.kiro.type`); assert `meta["commands"]` and `meta["skills"]` are both updated and both WS frames are broadcast.

8. `test_available_commands_update_attribution_by_session_id` — send `available_commands_update` with a `sessionId` matching a known session and `len(sessions)=2`; assert it is attributed to the correct session, not dropped.

9. `test_available_commands_update_max_count_applied_per_type` — send `availableCommands` with 201 skills and 201 commands; assert both `meta["skills"]` and `meta["commands"]` are capped at `MAX_COMMANDS_COUNT` independently.

10. `test_subscribe_replays_skills_frame` — after `meta["skills"]` is set, call `_handle_subscribe` and assert the subscriber receives both a `"commands"` and a `"skills"` frame.

**Exit criteria**:
- [ ] All 10 new test cases added and passing
- [ ] `.venv-PowerAtlas\Scripts\pytest tests/test_web.py -k "TestAcpCommandsAvailable"` — full class passes (including pre-existing tests)

### Phase 3: Frontend — skills in palette, badge [QA]

**Goal**: Add `sessionSkills`, the `'skills'` WS frame handler, merge skills into the dropdown, and add the badge CSS.

**Covers**: SC1, SC2, SC3

**File scope**: `src/power_atlas/templates/acp.html`

**Changes:**

**1. Declare `sessionSkills`** — add alongside `sessionCommands` at acp.html:681:
```javascript
var sessionCommands = [];
var sessionSkills = [];  // [{name, description}] — kiro-cli skills, badged in the palette
```

**2. Reset `sessionSkills` on session frame** (acp.html:5258 area) — the complete reset block (show full replacement to avoid ambiguity):
```javascript
// A new session frame means any prior command catalogue is gone — reset both lists.
sessionCommands = [];
sessionSkills = [];
hideCommandDropdown();
```

**3. Reset `sessionSkills` in `releaseSession()`** (acp.html:1150 area) — add beside `sessionCommands = []`:
```javascript
sessionCommands = [];
sessionSkills = [];
```

**4. Add `'skills'` WS frame handler** — add immediately after the `'commands'` handler (acp.html:5372):
```javascript
if (type === 'skills') {
  sessionSkills = (payload && payload.skills) || [];
  return;
}
```

**5. Update `showCommandDropdown(partial)`** (acp.html:1720) — replace the function body to merge commands and skills into one flat list, each tagged with `isSkill`:
```javascript
function showCommandDropdown(partial) {
  var lc = partial ? partial.toLowerCase() : '';
  // Merge commands and skills into one flat list; preserve server order within each.
  var allItems = sessionCommands.map(function(c) {
    return {name: c.name, description: c.description, isSkill: false};
  }).concat(sessionSkills.map(function(s) {
    return {name: s.name, description: s.description, isSkill: true};
  }));
  var items = allItems.filter(function(c) {
    return !lc || c.name.toLowerCase().indexOf(lc) !== -1;
  });
  renderCommandDropdown(items, sessionCommands.length === 0 && sessionSkills.length === 0);
}
```

**6. Update `renderCommandDropdown(items, catalogueEmpty)`** (acp.html:1729) — add `catalogueEmpty` parameter for placeholder logic; add badge for skill entries. The `catalogueEmpty` boolean distinguishes "no data loaded yet" from "data loaded but nothing matched the filter":
```javascript
function renderCommandDropdown(items, catalogueEmpty) {
  // ... existing list-clearing code ...
  if (!items.length) {
    var placeholder = document.createElement('li');
    placeholder.className = 'acp-cmd-placeholder';
    placeholder.textContent = catalogueEmpty
      ? 'Loading catalogue\u2026'    // before first commands/skills frame
      : 'No matching commands or skills';
    ul.appendChild(placeholder);
    cmdDropdownEl.hidden = false;
    _cmdSelectedIndex = -1;
    return;
  }
  items.forEach(function(item, idx) {
    var li = document.createElement('li');
    li.setAttribute('role', 'option');
    li.dataset.idx = idx;
    var nameSpan = document.createElement('span');
    nameSpan.className = 'acp-cmd-name';
    nameSpan.textContent = '/' + item.name;
    li.appendChild(nameSpan);
    if (item.isSkill === true) {
      // Badge appended as a sibling after nameSpan inside <li>
      var badge = document.createElement('span');
      badge.className = 'acp-cmd-skill-badge';
      badge.textContent = 'skill';
      li.appendChild(badge);
    }
    var descSpan = document.createElement('span');
    descSpan.className = 'acp-cmd-desc';
    descSpan.textContent = item.description || '';
    li.appendChild(descSpan);
    // ... existing click handler wiring ...
    ul.appendChild(li);
  });
  _cmdSelectedIndex = 0;
  // ... existing scroll/highlight logic ...
}
```

> **Note**: the snippets above show the structural changes. Read the actual `renderCommandDropdown` body (acp.html:1729) before implementing to wire the click handlers and scroll logic correctly — preserve all existing behavior, only add the badge insertion and the `catalogueEmpty` parameter.

**7. Add CSS for `.acp-cmd-skill-badge`** — in the slash command palette CSS block near acp.html:339:
```css
/* skill badge — displayed inline after the command name */
.acp-cmd-skill-badge {
  display: inline-block;
  margin-left: 0.35em;
  padding: 0 0.3em;
  font-size: 0.65em;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-radius: 3px;
  background: var(--badge-bg, rgba(148, 163, 184, 0.18));
  color: var(--badge-fg, #94a3b8);
  vertical-align: middle;
  line-height: 1.6;
}
```

**Exit criteria**:
- [ ] `var sessionSkills = []` declared at acp.html:681 area
- [ ] `sessionSkills = []` reset in both the `'session'` frame handler and `releaseSession()`; full reset block shown — not just the added line
- [ ] `'skills'` WS frame handler sets `sessionSkills`
- [ ] `showCommandDropdown` merges commands and skills into a flat list, passes `catalogueEmpty` flag
- [ ] `renderCommandDropdown` accepts `catalogueEmpty`, renders "Loading catalogue…" when both lists empty, "No matching commands or skills" when data is loaded but nothing matched
- [ ] `renderCommandDropdown` adds `.acp-cmd-skill-badge` span (as `<li>` child sibling after nameSpan) on skill entries only; command entries have no badge
- [ ] CSS for `.acp-cmd-skill-badge` added
- [ ] Hard-reload (`Ctrl+Shift+R`) on running PowerAtlas — press `/` on a fresh session — skills appear with badge before any prompt (SC1 manual check)

### Phase 4: Frontend — tests [QA]

**Goal**: Extend `acp_page.test.mjs` to cover the new frontend skill behaviour.

**Covers**: SC1, SC2, SC3, SC4

**File scope**: `tests/acp_page.test.mjs`

**New test cases:**

1. `skillsFramePopulatesSessionSkills` — deliver a `'skills'` frame; assert `sessionSkills` is populated correctly.

2. `skillsFrameOnSessionChangeResetsSessionSkills` — set `sessionSkills`, then send a new `'session'` frame; assert `sessionSkills` is reset to `[]`.

3. `releaseSessionClearsSessionSkills` — set `sessionSkills`, call `releaseSession()`; assert `sessionSkills === []`.

4. `slashKeyShowsSkillsInDropdown` — seed both `sessionCommands` and `sessionSkills`; fire `/` key; assert both command entries and skill entries appear in the rendered dropdown.

5. `skillEntriesShowBadge` — seed `sessionSkills` with one skill; fire `/`; assert the rendered `<li>` for the skill contains an element with class `acp-cmd-skill-badge`.

6. `commandEntriesDoNotShowBadge` — seed `sessionCommands` with one command; fire `/`; assert no `.acp-cmd-skill-badge` element is rendered.

7. `slashFilterMatchesSkillsByName` — seed both lists; type `/qex`; assert only skill entries whose name contains `"qex"` appear (plus any matching commands). Also test the inverse: type a string that matches no commands but no skills — assert "No matching" placeholder, not "Loading" placeholder.

8. `emptyBothListsShowsLoadingPlaceholder` — with both lists empty, open the dropdown; assert placeholder text contains "Loading" (not "No matching").

9. `loadedButNoMatchShowsNoMatchingPlaceholder` — seed at least one command and one skill; type `/zzznotexists`; assert placeholder text contains "No matching" (not "Loading").

**Exit criteria**:
- [ ] All 9 new test cases added and passing
- [ ] `node tests/acp_page.test.mjs` — full suite passes (including pre-existing tests)

### Phase 5: Documentation

**Goal**: Update `docs/KNOWLEDGE.md` and `plans/ROADMAP.md` to reflect that skills are now discovered and displayed in the palette.

**Covers**: (no SC — doc update)

**File scope**: `docs/KNOWLEDGE.md`, `plans/ROADMAP.md`

**Changes:**

- **`docs/KNOWLEDGE.md` (line 49)**: Update the `_kiro.dev/commands/available` bullet to state: the handler now also extracts skills from `params.prompts` (entries where `serverName.startswith("skill:")` or `_meta.kiro.type == "skill"`), stores them in `meta["skills"]`, broadcasts a `"skills"` WS frame alongside `"commands"`, and replays both on subscribe. Update the palette description to say the dropdown shows both commands and skills (skills identified by a badge).
- **`docs/KNOWLEDGE.md` (line 46)**: Reconcile the command/prompt counts to the probe-verified values: 25 commands + 26 prompts (not "24 built-ins, 25 skills").
- **`plans/ROADMAP.md` (line 134)** (Skills support item): Mark the "skills in palette" and "skills discoverable before first turn" sub-gaps as resolved by this plan. Narrow the remaining scope to: `$ARGUMENTS` verification (unverified over ACP) and Claude Code skill invocation (out of scope here).

**Exit criteria**:
- [ ] `docs/KNOWLEDGE.md` updated to describe skills extraction, `"skills"` frame, and badged palette; counts reconciled
- [ ] `plans/ROADMAP.md` Skills support item updated to reflect resolved sub-gaps

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `_pending_commands` buffer not cleared on `new_session` failure | Stale skills/commands applied to a subsequent session | Cleared in `finally` block unconditionally; flush also clears slot before broadcasting |
| Concurrent `new_session` calls (≥2 tabs racing) | Second session never gets commands flushed; last-writer-wins means first session may get latest notification | Accepted limitation (documented in §3). All probe-observed notifications carry identical content; a log.debug fires on slot replacement |
| `available_commands_update` arrives without `sessionId` | Attribution falls back to inflight/sessions count; drops in multi-session scenarios | SC3 notes this limitation; `sessionId` is almost certainly present in practice (kiro-cli's own `session/update` handler always receives it via params) |
| `available_commands_update` `availableCommands` field path wrong | Handler silently extracts `[]`; SC3 never fires | O1 requires probe verification before shipping; comment in code names this as the exit criterion |
| `_registry.broadcast` raises inside the flush | meta["commands"]/["skills"] written but not broadcast | Caught and logged; session is not left in inconsistent state; next `commands/available` (post-turn) will re-deliver |
| `renderCommandDropdown` called without `catalogueEmpty` param | `catalogueEmpty` is `undefined` → falsy → "No matching" message shown even when catalogue not yet loaded | All calls go through `showCommandDropdown`; Phase 4 tests verify both placeholder branches |
| `"skills"` frame type missing from `SERVER_TYPES` | `envelope()` raises `ValueError` at runtime | Added in Phase 1 step 1 before any send path; Phase 2 test `test_skills_broadcast_frame_sent` catches it |

## 7) Verification

**Automated:**
```
.venv-PowerAtlas\Scripts\pytest tests/test_web.py -k "TestAcpCommandsAvailable" -v
node tests/acp_page.test.mjs
```

**Manual (SC1 — pre-turn palette)**:
1. Start PowerAtlas (or hard-reload if already running)
2. Open `/acp` and create a new session on any workspace
3. Press `/` immediately — before typing any prompt
4. Verify: dropdown shows ~25 commands + ~26 skills; skill entries have a visible `skill` badge; commands do not have the badge

**Manual (SC2 — reconnect replay)**:
1. With skills loaded in a session, close and reopen the `/acp` tab
2. Press `/` — verify skills appear immediately without sending a prompt

**Manual (SC3 — mode switch)**:
1. In an active `/acp` session, send `/agentSelect` and switch to a different agent
2. Press `/` — verify the palette reflects the new agent's skills

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `docs/KNOWLEDGE.md` | Update `commands/available` bullet to describe skills extraction, `"skills"` frame, badged palette; reconcile command/prompt counts to probe values | 5 |
| `plans/ROADMAP.md` | Mark palette/skills-discovery sub-gap resolved; narrow remaining scope to `$ARGUMENTS` and Claude Code | 5 |

## 9) Implementation Divergences from Plan

*Reserved — filled during implementation.*

## Review Log

### 2026-08-13 — Plan creation (via /qplan)

4 findings High, 5 Medium, 4 Low. All 13 auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | Skill-extraction list-comprehension duplicated in both notification handlers (DRY violation). | Fixed — extracted `_parse_skills()` module-level helper called by both handlers. |
| 2 | High | Single-slot buffer unsafe for concurrent `new_session` (`_reserved > 1`): second session never gets a flush. | Fixed — documented in §3 as accepted limitation with `log.debug` on slot replacement; SC5 wording clarified. |
| 3 | High | `available_commands_update` handler may read `availableCommands` from wrong nesting level, silently returning `[]`. | Fixed — Phase 1 requires probe verification (O1) before shipping; comment and exit criterion added. |
| 4 | Medium | `available_commands_update` sliced combined list before partitioning — skills after position 200 dropped. | Fixed — `_parse_skills` helper + commands filter both iterate the full list and cap after filtering. |
| 5 | Medium | acp.html line references off by ~39 lines (plan written against older snapshot). | Fixed — corrected to acp.html:681 (sessionCommands), 1720 (showCommandDropdown), 1729 (renderCommandDropdown), 5372 ('commands' handler). |
| 6 | Medium | `test_pending_commands_buffer_flushed` calls async `new_session` without specifying `asyncio.run()` or `pytest-asyncio`. | Fixed — Phase 2 note specifies extracting sync `_flush_pending_commands` helper; tests call helper directly. |
| 7 | Medium | Placeholder message text "start a turn to load the catalogue" factually wrong after pre-turn delivery lands. | Fixed — Phase 3 change 6 uses "Loading catalogue…" when both lists empty, "No matching commands or skills" otherwise. |
| 8 | Medium | Badge insertion point ambiguous ("nameSpan.appendChild or li.appendChild"). | Fixed — committed to `li.appendChild(badge)` after nameSpan (sibling in `<li>`). |
| 9 | Medium | `_registry.broadcast` failure inside flush propagates, leaving session in inconsistent state. | Fixed — flush wraps broadcasts in `try/except`, logs warning; meta["commands"]/["skills"] written before broadcast. |
| 10 | Low | SC3 multi-session limitation (attribution fallback drops without sessionId) undocumented. | Fixed — SC3 updated with limitation note; §6 risk table entry added. |
| 11 | Low | `_pending_commands` slot replacement silent (no log). | Fixed — `log.debug` added when non-None slot is replaced. |
| 12 | Low | Python 3.10+ union syntax concern. | N/A — project requires Python 3.11+ (`pyproject.toml:8`); syntax is valid. |
| 13 | Low | No observability for flush. | Fixed — `log.debug` added after successful flush reporting counts. |

## Harness Improvement Opportunities

- The probe step (running a live kiro-cli subprocess to settle the commands/available timing) was not covered by the trio's "decidable-by-probe" list in the way it should have been — the mutation-finder labeled it as open ("requires live observation") but it was settable with a 15-line script. The `/qexplore` decidable-by-probe gate could benefit from a note clarifying that "launch a local subprocess and read its stdout" counts as a read-only probe the orchestrator can and should run before the interview. Cost: one extra Q&A round that could have been replaced by the probe result.
