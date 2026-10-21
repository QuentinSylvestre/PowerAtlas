# ACP Per-Session kiro-cli Process

> **Date**: 2026-10-20
> **Status**: Complete
> **Last Updated**: 2026-10-21
> **Scope**: Migrate PowerAtlas ACP from one shared kiro-cli process to one process per session
> **Memory relied on**: Project memory (PowerAtlas), kiro-guidelines steering

---

## Intent

### Problem statement & desired outcomes

PowerAtlas's ACP surface runs all sessions through a single shared `kiro-cli acp` process. When two
sessions are active simultaneously, kiro-cli serializes their turns â€” the second session's prompt
waits until the first session's turn finishes. This surfaces as "Waiting for another session's turn
to finishâ€¦" in the steer handler and invisible blocking everywhere else. The TUI never hits this
because it runs one process per session by nature.

Desired outcome: concurrent ACP sessions run in full parallel, no turn serialization, each session
gets its own kiro-cli process and lifecycle.

### Success criteria

- Two ACP sessions can run simultaneous turns without either blocking the other.
- The `queued_behind` / `queue_status` / `_set_queued_behind` code is removed (it becomes unreachable).
- `_Supervisor` is instantiated once per ACP session, not once globally.
- Session creation from the picker shows a brief spinner (~2-4 s) while the new process spawns and
  the `initialize` handshake completes.
- All existing ACP test scenarios still pass after the test fixture refactor.
- Memory cost per session is measured and documented before any cap change.

### Scope boundaries & non-goals

In scope: `acp.py`, `presence.py`, `web.py` (session routing helpers), `tests/test_web.py` ACP
fixture refactor.

Out of scope: Kiro IDE sessions, Claude Code sessions, Codex sessions, the dashboard rail, the
Overview, the remote-access path. No warm pool (follow-up item). No per-process cwd change (keep
`_neutral_cwd()` unchanged).

---

## Exploration Discovery

### 1. Problem statement

One `_Supervisor` singleton at `acp.py:8590` owns one `kiro-cli acp` process (kiro-cli.exe â†’
bun.exe â†’ node.exe). kiro-cli's own notification at line 7265
(`_info_kind == "queued"`, `activeSessionId`) confirms the serialization is inside kiro-cli's
dispatch loop â€” not PA-invented. The comment explicitly notes: "No first-party client renders this
(the TUI drives one session; KiroCrew has no handler)." PA is the first client to surface it.

### 2. Recommended approach

Replace the module-level `_supervisor = _Supervisor()` singleton with a module-level routing dict
`_supervisors: dict[str, _Supervisor]` keyed by session id. Each ACP session gets its own
`_Supervisor` instance and its own `kiro-cli acp` subprocess. The `_Supervisor` class itself is
unchanged in contract; it is now instantiated N times instead of once.

Key structural changes:
- `_supervisors: dict[str, _Supervisor]` â€” primary routing table (regular sessions).
- `_reserved: int` at module level (replaces per-supervisor `_reserved`).
- `_find_supervisor(session_id)` â€” checks `_supervisors` first; falls back to scanning
  `_workflow_child_meta` / `subagent_sessions` across all supervisors for workflow step sessions.
- `at_capacity()` â†’ `len(_supervisors) + _reserved >= MAX_SESSIONS`.
- Per-supervisor asyncio sweep task (started in `new_session`/`load_session`); module-level
  `start_sweeper()` and `start_watchdog()` calls removed from `web.py` lifespan.
- `_maybe_recycle_idle` eliminated â€” with one session per supervisor, closing the session kills
  the process. No separate idle-agent recycle needed.
- `_set_queued_behind`, `_set_queued_behind`, `queue_status` frame, `queued_behind` meta field
  removed as dead code.
- `_publish_live` interface changes: emits one `{session_id: pid}` pair per call; `presence.py`
  `_acp_live` changes from `tuple[frozenset, int|None]` to `dict[str, int]`; D32 guard
  generalizes to check all ACP pids.
- `_secrets: _SecretStore` stays module-level singleton; existing `threading.Lock` handles
  concurrent per-supervisor access safely.
- `_registry: _Registry` stays module-level; WebSocket routing is already per-session-id.

### 3. Files and components affected

| File | Change |
|---|---|
| `src/power_atlas/acp.py` | Core: `_supervisor` singleton â†’ `_supervisors` dict; new `_find_supervisor()` helper; remove `_maybe_recycle_idle`, `_set_queued_behind`, `queue_status`; add per-supervisor sweep task; update `at_capacity()`, `shutdown()`, `_detach()`, lifespan wiring |
| `src/power_atlas/presence.py` | `_acp_live: tuple â†’ dict[str, int]`; `publish_acp_sessions` signature change; D32 guard generalization |
| `src/power_atlas/web.py` | Remove `start_sweeper()` / `start_watchdog()` from lifespan; update `_build_held_set()` to union across all supervisors; update `_inflight_ids`, `_awaiting_person_ids`, `_workflow_states` helpers |
| `tests/test_web.py` | Full `acp_store` fixture refactor: factory model replacing singleton mutation; 1,109 `acp_mod._supervisor` references to migrate |
| `docs/KNOWLEDGE.md` | Update "one process tree" and "~161 MB/session" notes; add per-session process facts once measured |
| `plans/ROADMAP.md` | Close the spike item; link this plan |

### 4. Existing patterns & constraints

- **v3 process tree**: kiro-cli.exe â†’ bun.exe â†’ node.exe. One Windows Job Object
  (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) per supervisor ensures teardown on handle close (`acp.py:4200`).
  Each per-session supervisor needs its own Job Object. `win32job` required; unavailable â†’ `AgentUnavailable`.
- **`_neutral_cwd()`**: returns `CONFIG_DIR / "acp-cwd"` regardless of session workspace. Stays
  unchanged â€” presence.py's scan reads process cwd, and the config dir must not register as a
  "live workspace" cwd.
- **`_secrets`**: DPAPI-encrypted per-user binary. All supervisors share one file; `threading.Lock`
  inside makes concurrent access safe without additional synchronization.
- **`close_session()` has one await** (line 8446: `_answer_pending_cancelled`) â€” the AGENTS.md
  memory note claiming "zero awaits" is stale; verified 2026-10-20.
- **`load_session` sibling-copy optimization** (`acp.py:8309â€“8313`): copies `commands`/`skills`
  from an arbitrary sibling session. Disappears in per-session model (no siblings); the loaded
  session gets its catalogue from kiro-cli's own `_kiro.dev/commands/available` notification.
- **Test coupling**: 1,109 `acp_mod._supervisor` references in `test_web.py`. The `acp_store`
  fixture directly mutates 20+ named attributes on the singleton. Full factory refactor required.
- **`_on_subagent_list` attribution** (`acp.py:4878`): uses `len(self.inflight)` to attribute a
  sessionId-less subagent notification. With one session per supervisor, `len(inflight) â‰¤ 1`
  always â€” the multi-session ambiguity path becomes unreachable and can be simplified.

### 5. Risks & mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Unknown v3 per-session memory cost â€” each session now owns one kiro-cli process tree instead of a fraction of one | High | Add a measurement step as Phase 1 exit criterion; do not change `MAX_SESSIONS` default until measured |
| Spawn latency (~2-4 s per session) is user-visible for the first time | Medium | Session picker shows spinner; acceptable UX for an explicit action |
| 1,109 test references means test refactor likely equals or exceeds production code effort | High | Dedicated test infrastructure phase; use a factory pattern that enables multi-session test scenarios not possible today |
| `_publish_live` interface change ripples into `presence.py` D32 guard â€” orphan lock suppression could break if any pid is missed | Medium | Explicit per-supervisor `_publish_live` calls aggregated at module level; add a test that verifies D32 guard fires for all pids |
| Workflow step sessions route through parent supervisor's kiro-cli â€” if `_find_supervisor` scan is wrong, step prompts go to the wrong process | Medium | Unit test covering `_find_supervisor` with a live workflow child; scan is O(â‰¤8) supervisors |
| `acp_store` fixture refactor touches every ACP test â€” high risk of accidental coverage gaps | High | Run full test suite after each fixture migration batch; mutation-test the factory |

### 6. Resolved decisions

- Q1: Routing table shape â€” A: flat module-level `_supervisors: dict[str, _Supervisor]` + module-level `_reserved` counter â€” Decision: module-level dict; no pool class.
- Q2: Test infrastructure â€” A: full fixture refactor â€” Decision: `acp_store` becomes a per-session factory; no compatibility shim.
- Q3: Spawn latency strategy â€” A: on-demand spawn â€” Decision: each session creation spawns a fresh process; no warm pool.
- Q4: Sweeper architecture â€” A: per-supervisor asyncio tasks â€” Decision: each supervisor starts its own task; `start_sweeper()` / `start_watchdog()` removed from `web.py` lifespan.

### 7. Open items

- **Memory cost of v3 per-session kiro-cli process**: unknown. The v2 measurement (~161 MB,
  ~3 processes per session) does not apply to v3's shared-tree model. Must measure before deciding
  new `MAX_SESSIONS` default. Command: `Get-Process kiro-cli,bun,node | Measure-Object WorkingSet64 -Sum`
  with N sessions live.
- **kiro-cli `session_info_update` kind `"queued"` semantics**: confirmed to be cross-session
  within one process. With per-session processes, this notification should never fire. Verify
  during live QA: run two parallel sessions and confirm neither shows `queued_behind` in the
  steer handler.
- **`_find_supervisor` performance**: O(N supervisors) scan for workflow children. With
  `MAX_SESSIONS = 8`, N â‰¤ 8 and the scan is negligible. If cap ever increases significantly,
  a supplementary `_child_to_supervisor: dict[str, _Supervisor]` cache can be added.

### 8. Out of scope

- Warm pool (pre-spawned initialized processes to reduce session creation latency). Follow-up
  item in ROADMAP.
- Changing `_neutral_cwd()` to use session workspace cwd.
- Changing `MAX_SESSIONS` default before memory is measured.
- Any provider other than kiro-cli ACP.
- Dashboard Overview, remote access, or non-ACP web routes.

### 9. Completion signals

- Two ACP sessions run concurrent turns with no "waiting" message.
- `grep "queued_behind\|_set_queued_behind\|queue_status" src/power_atlas/acp.py` returns no hits.
- Full pytest suite passes after fixture refactor.
- Memory cost documented in `docs/KNOWLEDGE.md`.
- ROADMAP spike item closed.

---

## Notes

- **PA restart grant (2026-10-20, user)**: PowerAtlas may be restarted autonomously during
  implementation for live QA and verification. Grant covers this plan only.

---

## Assumptions (unconfirmed)

- Migration is restart-required: PA restarts to deploy; in-flight sessions resume from disk via `session/load`.
- `_neutral_cwd()` unchanged.
- `_secrets` concurrent access safe with existing `threading.Lock`; no additional synchronization needed.
- `_publish_live` publishes `{session_id: pid}` per call; `presence.py` aggregates.
- `close_session` one-await fact is a stale-memory correction only, not a design blocker.

---

## 1) Current State

`_supervisor = _Supervisor()` at `acp.py:8590` is the sole kiro-cli ACP process manager.
Every `_handle_*` function and module-level helper calls this singleton directly. On v3,
all sessions share one process tree (kiro-cli.exe â†’ bun.exe â†’ node.exe); kiro-cli serializes
turns across sessions and reports queuing via `session_info_update` kind `"queued"` with an
`activeSessionId` field (`acp.py:7265`).

Key shared-process state (all must be split per supervisor or moved to module level):
- `self._proc / self._job / self._ready / self._reader / self._write_lock` â€” process handle and I/O
- `self.sessions / self.history / self.inflight / self.closing / self._reserved` â€” session tracking
- `self.crews / self.subagent_sessions / self.subagent_history / self._workflow_children` â€” fan-out
- `self._pending / self._next_id` â€” JSON-RPC in-flight futures

Module-level singletons that stay global:
- `_secrets = _SecretStore(ACP_SECRETS_PATH)` at `acp.py:1004` â€” MCP OAuth tokens; thread-safe
- `_registry: _Registry` at `acp.py:3065` â€” WebSocket routing; already per-session-id internally
- `_bubbles: dict` at `acp.py:8724` â€” moved to module level in the migration

The test suite has 1,109 references to `acp_mod._supervisor` in `tests/test_web.py`
(confirmed 2026-10-20 via grep). The `acp_store` fixture mutates 20+ named attributes on
the singleton directly.

Memory note: the v2 ~161 MB / 3 processes per session figure does NOT apply to v3 shared-tree.
The true per-session v3 per-process cost is unknown and must be measured post-migration.

## 2) Goal

Replace the `_supervisor` singleton with `_supervisors: dict[str, _Supervisor]`. Each ACP
session creation (`_handle_new` / `_handle_load`) instantiates and spawns a dedicated
`_Supervisor`. Concurrent sessions run independent kiro-cli processes and never queue behind
each other.

## 3) Design Decisions

| Decision | Choice | Alternatives | Rationale |
|---|---|---|---|
| Routing table shape | Module-level `_supervisors: dict[str, _Supervisor]` + module-level `_reserved: int` | Pool class wrapper | Consistent with existing module-level singletons (`_registry`, `_tasks`); simpler test surface |
| Test infrastructure | Full `acp_store` factory refactor (1,109 refs) | Compatibility shim keeping `acp_mod._supervisor` | Shim would test a fiction; multi-session scenarios need real per-session fixtures |
| Spawn strategy | On-demand â€” spawn per session creation | Warm pool | Warm pool adds lifecycle complexity; spinner latency (~2-4 s) is acceptable for an explicit user action |
| Sweeper architecture | Per-supervisor asyncio task (started in `new_session`/`load_session`) | One global sweep task iterating all supervisors | Self-contained supervisors; `start_sweeper()`/`start_watchdog()` calls removed from lifespan |
| `_publish_live` interface | Emits one `{session_id: pid}` entry per call; `presence._acp_live` becomes `dict[str, int]` | Keep tuple, aggregate at hook level | Dict is queryable and correct; D32 guard can check all pids without a linear scan per check |
| Migration deployment | Restart-required | Rolling (new sessions per-process, old stay shared) | Clean restart; sessions resume from disk via `session/load` |
| `_neutral_cwd()` | Unchanged (`CONFIG_DIR/acp-cwd`) | Use session workspace cwd per process | Presence scan maps process cwd; config dir must not register as a live workspace |
| `_secrets` concurrency | Shared module-level singleton; existing `threading.Lock` sufficient | Per-supervisor copy | MCP tokens are per-server, not per-session; sharing is correct |
| `queued_behind` / `queue_status` | Remove as dead code | Keep for future single-process harnesses | Per-session processes make this unreachable; dead code removal is mandatory |
| Workflow step routing | `_find_supervisor(session_id)` scans `_workflow_child_meta` / `subagent_sessions` across all supervisors (O(â‰¤MAX_SESSIONS)) | Supplementary `_child_to_supervisor` lookup dict | Cache adds write-time synchronization; O(8) scan negligible; add cache only if cap grows substantially |
| `close_session` await | One await at `acp.py:8446` (`_answer_pending_cancelled`); AGENTS.md memory note claiming "zero awaits" is stale | â€” | Correction only; no design impact |
| Probe: `queued` serialization | Confirmed from kiro-cli source (`acp.py:7261` comment) that it only fires within one shared process | â€” | Per-session processes eliminate it; no probe needed |
| Memory cap during migration | Keep `MAX_SESSIONS = 8`, `_clamped` ceiling at 16 | Reduce cap conservatively | Measure first (Phase 4); change only after data |
| Review effort | Full (user override 2026-10-20; default for Major: standard) | â€” | User preference; recorded per `shared/AGENTS.md Â§ Continuous Improvement` |

## 4) External Dependencies & Costs

| Category | Change needed | Owner | Status |
|---|---|---|---|
| CI/CD | None | â€” | N/A |
| IAM / Permissions | None | â€” | N/A |
| Cloud resources | None | â€” | N/A |
| Data migration / backfill | None â€” sessions persist on disk in kiro-cli's store, unchanged | â€” | N/A |
| Rollout / cutover | Restart PA to deploy | User | Restart-required |
| Cleanup after rollback window | None | â€” | N/A |
| Secrets / Env vars | None â€” `acp-secrets.bin` shared across per-session supervisors, unchanged | â€” | N/A |
| DNS / Networking | None | â€” | N/A |
| Third-party services | None | â€” | N/A |

### Cost impact

None. Compute cost per session is higher (one full kiro-cli process tree vs. a fraction of one),
but this is local machine overhead only. The per-session v3 memory cost is unknown and must be
measured (Phase 4). No cloud or API cost change.

## 5) Implementation Phases

### Phase 1: Core migration â€” routing, spawn, handlers [QA]

**Goal**: Replace the `_supervisor` singleton with per-session `_Supervisor` instances. PA
restarts with the new model; two sessions can run concurrent turns with no serialization.

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/presence.py`, `src/power_atlas/web.py`

**Covers**: SC-1

#### `acp.py` changes

**Add module-level routing infrastructure:**
```python
# Module-level supervisor pool (replaces _supervisor singleton)
_supervisors: dict[str, _Supervisor] = {}  # session_id â†’ supervisor
_reserved: int = 0  # sessions in-flight between reserve and registration

def _find_supervisor(session_id: str) -> "_Supervisor | None":
    """Route session_id to its supervisor. Checks regular sessions first,
    then scans workflow children across all supervisors (O(MAX_SESSIONS))."""
    sup = _supervisors.get(session_id)
    if sup is not None:
        return sup
    for s in _supervisors.values():
        if (session_id in s.subagent_sessions
                or session_id in s._workflow_child_meta):
            return s
    return None

def _deregister_supervisor(session_id: str) -> None:
    """Remove a supervisor from the routing dict and publish updated presence."""
    sup = _supervisors.pop(session_id, None)
    if sup is not None:
        _publish_live_all()

def _publish_live_all() -> None:
    """Publish the current {session_id: pid} mapping across all supervisors."""
    hook = sessions_changed_hook  # not _sessions_changed_hook; see acp.py:1399
    if hook is None:
        return
    _live = _supervisors  # local snapshot for iteration safety
    mapping: dict[str, int] = {}
    for sid, s in _live.items():
        p = s.agent_pid()
        # Guard stale pid: if process has exited but _detach hasn't cleared _proc yet
        if p is not None and s._proc is not None and s._proc.poll() is not None:
            p = None
        if p is not None:
            mapping[sid] = p
    hook(mapping)
```

**Remove `_supervisor = _Supervisor()` singleton line** (`acp.py:8590`).

**Remove per-supervisor `_reserved`** from `_Supervisor.__init__`; it is now the module-level
counter above. All `self._reserved` reads/writes in `_Supervisor` become `global _reserved;
_reserved += 1 / -= 1` at the module level. (The module-level counter is loop-thread-only,
consistent with existing threading model.)

**Update `at_capacity()`** (currently `_Supervisor.at_capacity`, `acp.py:6591`):
```python
def at_capacity() -> bool:
    """Whether starting another session would exceed MAX_SESSIONS."""
    return len(_supervisors) + _reserved >= MAX_SESSIONS
```
Make it a module-level function (not a method). Update all call sites.

**Update `_handle_new`** (`acp.py:9529`): create a new `_Supervisor()`, start it, register it in
`_supervisors[session_id]` **before** the `conn not in _registry.connections` early-return
check. Registration must precede any early-return path after `new_session()` returns, so that
the supervisor is always in `_supervisors` if the session was created in kiro-cli and the
sweep task is running. On early return, call `_deregister_supervisor(session_id)` to clean up.
Guard with `at_capacity()` check using module-level function.

**Update `_handle_load`** (`acp.py:9448`): same registration-before-early-return pattern.

**Remove `load_session` sibling-copy** (`acp.py:8309â€“8313`): the lines that copy `commands`/
`skills` from an existing sibling's meta no longer apply (no siblings exist per supervisor).
Remove them. The loaded session receives its catalogue from kiro-cli's `_kiro.dev/commands/available`
notification.

**Update all `_handle_*` functions** to route through `_find_supervisor(session_id)` instead of
the global `_supervisor`. The call pattern `_supervisor.X(session_id, ...)` becomes:
```python
sup = _find_supervisor(session_id)
if sup is None:
    conn.send(error_frame("unknown_session", "...", session_id))
    return
sup.X(session_id, ...)
```
Functions to update: `_handle_subscribe`, `_handle_prompt`, `_handle_steer`,
`_handle_mcp_signin`, `_handle_permission_response`, `_handle_question_response`,
`_handle_cancel`, `_handle_close`, `_handle_commands_options`, `_handle_commands_execute`,
`_handle_subagent_subscribe`.

**Update `_emit`** (module-level, `acp.py:8598`):
```python
def _emit(session_id: str, frame: dict) -> None:
    sup = _find_supervisor(session_id)
    if sup is not None:
        sup.record(session_id, frame)
    _registry.broadcast(session_id, frame)
```

**Update `shutdown()`** (module-level, `acp.py:8821`): iterate all supervisors:
```python
def shutdown() -> None:
    for sup in list(_supervisors.values()):
        sup.shutdown()
    _supervisors.clear()
```

**Update `_publish_live`** (per-supervisor method, `acp.py:8501`): delegate to
`_publish_live_all()` defined above. The per-supervisor method is now a thin wrapper:
```python
def _publish_live(self) -> None:
    _publish_live_all()
```
This replaces the old single-pid emission with the full-mapping emission defined above.
`sessions_changed_hook` (not `_sessions_changed_hook` â€” the actual module variable name;
verify against `acp.py:1399`) is now called with `dict[str, int]`.

**Update `set_sessions_changed_hook()`** (`acp.py:1428`): the function currently ends with
`_supervisor._publish_live()` to emit an initial snapshot. Replace with `_publish_live_all()`.

**Update `_detach()`** (`acp.py:4261`): two changes required:
1. Replace `_bubbles.clear()` (`acp.py:4309`) with a session-scoped clear using `self._session_id`
   (a new attribute, see below): `_bubbles.pop(self._session_id, None)`. Do NOT iterate
   `self.sessions` â€” `close_session()` clears `self.sessions` before calling `_detach()`, so
   iteration would be a no-op in the normal-close path. Using `self._session_id` is correct in
   both paths: normal close (sessions already cleared, `_session_id` still set) and crash
   (`_on_agent_death` â†’ `_detach` directly, sessions still populated).
2. Add `_deregister_supervisor(self._session_id)` call after clearing internal state (after
   the `self.sessions.clear()` call). Uses the same `_session_id` attribute for reliability.

**Add `_session_id` attribute to `_Supervisor`**: set `self._session_id: str | None = None`
in `__init__`. Write it when the supervisor is registered:
`_supervisors[session_id] = sup; sup._session_id = session_id`. This ensures `_detach()` can
always identify its session without iterating `self.sessions`.

**Update `close_session()`** (`acp.py:8417`): the normal close path â€” after local state cleanup
(pop session from `self.sessions`, clear `self.history[sid]`, etc.) â€” calls
`_deregister_supervisor(self._session_id)` (which also calls `_publish_live_all()`), then calls
`self._detach()` + `_dispose()` to kill the kiro-cli process. The process lifecycle is:
`close_session()` â†’ local cleanup â†’ `_deregister_supervisor()` â†’ `_detach()` â†’ `_dispose()`.

> **Rejected:** Rely on `_maybe_recycle_idle` to kill the process â€” that method is being removed
> in Phase 2. **Use instead:** `close_session()` initiates the process kill directly as above.

#### `presence.py` changes

**`_acp_live`**: change type from `tuple[frozenset, int | None]` to `dict[str, int]`:
```python
_acp_live: dict[str, int] = {}
```

**`publish_acp_sessions`** (`presence.py:~212`): accept `mapping: dict[str, int]`:
```python
def publish_acp_sessions(mapping: dict[str, int]) -> None:
    global _acp_live
    _acp_live = dict(mapping)
```

**`acp_holds`** (`presence.py:~231`): unchanged â€” `session_id in _acp_live` still works.

**Remove existing tuple-unpack** (`presence.py:~747`): the line
`acp_sids, acp_pid = _acp_live` (which destructures the old 2-tuple) must be deleted. With a
dict, this would raise `ValueError` on any session count other than exactly 2.

**Replace D32 guard in `_scan`** (approx. `presence.py:800`): generalize from one pid to a
frozenset of all ACP pids. Use a local snapshot of `_acp_live` to keep both reads consistent:
```python
_live = _acp_live  # local snapshot
acp_sids = frozenset(_live)          # session ids
acp_pids = frozenset(_live.values()) # all ACP pids
# Replace: if provider in _KIRO_PROVIDERS and acp_pid and pid == acp_pid and sid not in acp_sids
# With:
if provider in _KIRO_PROVIDERS and pid in acp_pids and sid not in acp_sids:
    continue
```

#### `web.py` changes

**`web.py` lifespan** (`web.py:1365`): update `set_sessions_changed_hook` wiring.
`presence.publish_acp_sessions` now accepts `dict[str, int]`; no other wiring change needed.

**`_build_held_set()` helper** (implicit in `api_acp_sessions` / `api_dashboard_sessions`,
`web.py:4358â€“4360`): change from `frozenset(supervisor.sessions)` to the union across all
supervisors:
```python
def _build_held_set() -> frozenset[str]:
    sups = getattr(acp, "_supervisors", {})
    held: frozenset[str] = frozenset()
    for s in sups.values():
        held |= frozenset(s.sessions)
    return held
```

**`_inflight_ids`, `_awaiting_person_ids`, `_workflow_states` helpers**
(`web.py:3360â€“3430`): each currently takes `supervisor` arg; refactor to accept the full
`_supervisors` dict and aggregate across all values. The module-level
`getattr(acp, "_supervisor", None)` pattern becomes `getattr(acp, "_supervisors", {})`.

**`attention_snapshot()` aggregation** (`web.py:~4856`): this call currently does
`sup = getattr(acp, "_supervisor", None); sup.attention_snapshot()`. Refactor to iterate all
supervisors and union their `attention_snapshot()` results.

**`_reserved` reads in `web.py`** (`web.py:4361, 4430, 4947` â€” three sites): change each from
`supervisor._reserved` to `acp._reserved`. All three must be updated; the plan previously
noted only one.

**Exit criteria**:
- [x] PA restarts cleanly; `/acp` page loads; existing sessions resume via `session/load`
- [x] Two new sessions created; simultaneous prompts complete without either blocking the other
- [x] `_supervisors` dict has one entry per open ACP session; `_supervisor` attribute no longer
  exists on `acp` module; entry is removed after close (`len(_supervisors) == 0` after closing
  both sessions)
- [x] `presence._acp_live` is a `dict`; `acp_holds(sid)` returns `True` for held sessions,
  `False` after close
- [x] SC-1: Two parallel turns confirmed; `orchestrator.log` grepped for `kind.*queued`
  during the parallel test returns no hits
- [x] Create-close-create cycle succeeds: create 8 sessions, close all 8, create 1 more â€” no
  `at_capacity` refusal (verifies deregistration works)
- [x] **Note**: `pytest tests/test_web.py` will fail on Phase 1 and Phase 2 entry (1,109
  singleton references); automated tests are deferred to Phase 3. Phase 1 and Phase 2
  use live QA only.

---

### Phase 2: Dead code removal and per-supervisor sweep [QA]

**Goal**: Remove all code that only existed for the shared-process model. Add per-supervisor
asyncio sweep tasks. `start_sweeper()` / `start_watchdog()` removed from `web.py` lifespan.

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/web.py`

**Covers**: SC-2

#### `acp.py` removals

**Remove `_maybe_recycle_idle`** (`acp.py:4217`): with one session per supervisor, the session
lifecycle ends with `close_session()` calling `_detach()` + `_dispose()` (Phase 1). No orphan
agent recycle concept exists.

**Remove `_set_queued_behind`** (`acp.py:4828`), `queued_behind` meta field writes, and the
`_info_kind == "queued"` notification branch (`acp.py:7261`).

**Remove `queue_status` frame emissions** â€” all `_registry.broadcast(session_id, envelope("queue_status", ...))` calls.

**Update `_sweep_loop` and `_sweep_once` for multi-supervisor model** (`acp.py:10980â€“11011`):

`_sweep_once` currently iterates `_supervisor.sessions` to reclaim idle sessions. After Phase 1,
idle-TTL reclaim is handled by per-supervisor sweep tasks (started in `new_session`/`load_session`,
cancelled in `close_session`/`_detach`). The global `_sweep_loop` is slimmed to run only:
1. SC-1 orphan-buffer cleanup: iterate `_supervisors.values()` for `_pending_early_frames` entries
   idle past 1800 s for sessions that never registered.
2. Backstop `_publish_live_all()` call.

```python
async def _sweep_loop() -> None:
    """Minimal global sweep: SC-1 orphan-buffer cleanup and presence backstop."""
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        now = time.monotonic()
        # SC-1 orphan cleanup
        for sup in list(_supervisors.values()):
            stale = [
                sid for sid, at in list(sup._pending_early_frames_at.items())
                if now - at > ACP_IDLE_TTL_SECONDS
                   and sid not in sup.sessions
            ]
            for sid in stale:
                sup._pending_early_frames.pop(sid, None)
                sup._pending_early_frames_at.pop(sid, None)
        # Presence backstop
        _publish_live_all()
```

`start_sweeper()` in `web.py` lifespan **stays** â€” it starts this slimmed global sweep.
`start_watchdog()` moves to a per-supervisor method called from `new_session`/`load_session`:
```python
# In _Supervisor:
def _start_lifecycle_tasks(self) -> None:
    """Start per-supervisor sweep and watchdog tasks. Called after session is registered."""
    self._sweep_task_handle = asyncio.create_task(self._sweep_once_local())
    self._watchdog_task_handle = asyncio.create_task(self._watchdog_loop())

def cancel_lifecycle_tasks(self) -> None:
    """Cancel per-supervisor tasks on close. Called from close_session()/_detach()."""
    for handle in (self._sweep_task_handle, self._watchdog_task_handle):
        if handle is not None:
            handle.cancel()
    self._sweep_task_handle = None
    self._watchdog_task_handle = None
```
Add `_sweep_task_handle` and `_watchdog_task_handle` attributes to `_Supervisor.__init__`.

**`_sweep_once_local()` per-supervisor idle-TTL**:
```python
async def _sweep_once_local(self) -> None:
    """Periodic idle-TTL sweep for this supervisor's single session.
    Runs every SWEEP_INTERVAL_SECONDS; cancels itself when session is closed."""
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        now = time.monotonic()
        if not self.sessions:
            return  # session already closed; task will be cancelled shortly
        session_id = next(iter(self.sessions))
        meta = self.sessions[session_id]
        if _sweepable(session_id, meta, now, ...):
            await self.close_session(session_id)
            return
```

**Remove `_sweep_failures` / `_sweep_held_logged` module-level dicts** (`acp.py:10759`):
only after confirming the per-supervisor sweep does not reference them. The SC-1 cleanup above
does not use these dicts; they can be safely removed in Phase 2.

**`start_watchdog()` (global)**: remove from `web.py` lifespan (`web.py:1387`). Per-supervisor
watchdog tasks are started by `_start_lifecycle_tasks()` when sessions are created.

#### `web.py` changes

**Lifespan startup**: remove only `watchdog = acp.start_watchdog()` (`web.py:1387`). Keep
`sweeper = acp.start_sweeper()` â€” the slimmed global sweep still runs. Remove watchdog
cancellation from lifespan shutdown.

**`at_capacity()` error message** (the docstring at `acp.py:3180`): update from
"~3 processes and ~161 MB" to "one kiro-cli process tree" (exact cost documented after
Phase 4 measurement).

**Exit criteria**:
- [x] `grep -rn "queued_behind\|_set_queued_behind\|queue_status" src/power_atlas/acp.py` returns no hits
- [x] `grep -n "_maybe_recycle_idle\|AGENT_IDLE_RECYCLE_SECONDS" src/power_atlas/acp.py` returns no hits
- [x] SC-2: `queued_behind` dead code absent confirmed
- [x] PA restarts; session creation, prompting, and close work normally
- [x] Create-close-create verifies process death: create a session, close it, run
  `Get-Process kiro-cli,bun,node` and confirm the processes from that session are gone
- [x] A new session can be created immediately after closing the previous one (capacity slot freed)
- [x] `start_watchdog()` is no longer called from `web.py` lifespan
- [x] **Note**: `pytest tests/test_web.py` will fail on Phase 2 entry (same as Phase 1).
  Phase 2 is validated by live QA only.

---

### Phase 3: Test fixture refactor

**Goal**: Refactor `acp_store` to add per-session supervisor support while preserving backward
compatibility with the existing `(acp_mod, tmp_path)` yield contract; migrate 1,109
`acp_mod._supervisor` references; add multi-session test coverage not possible in the old model.

**File scope**: `tests/test_web.py`

**Covers**: SC-3

#### Fixture migration strategy

**`acp_store` fixture** (`test_web.py:5811`): **keep the existing yield value** â€” the fixture
currently yields `(acp_mod, tmp_path)` and 76 tests unpack it this way. Adding per-session
supervisor support requires adding two new cleanup operations to the existing fixture body, NOT
changing the yield:
```python
# In acp_store fixture body, ADD alongside existing cleanup:
monkeypatch.setattr(acp_mod, "_supervisors", {})
monkeypatch.setattr(acp_mod, "_reserved", 0)
# Keep all existing cleanup (sessions, history, inflight, crews, _bubbles,
# _registry.connections, _registry.subscribers, _registry.loading, etc.)
```

**`_live_session()` helper** (`test_web.py:11428`): **keep returning `sid` (string)** â€” 81 tests
store the return value as `sid` and pass it to API calls. The helper is extended to also create
a supervisor and register it:
```python
def _live_session(acp_mod, sid=None, *, cwd=r"C:\scratch") -> str:
    if sid is None:
        sid = f"sess_{uuid.uuid4().hex[:8]}"
    # Create and register a per-session supervisor
    sup = acp_mod._Supervisor()
    sup.sessions[sid] = acp_mod._new_session_record(cwd, "kiro_default")
    sup.history[sid] = acp_mod._History()
    acp_mod._supervisors[sid] = sup
    return sid  # unchanged return type
```
When a test needs the supervisor itself, it retrieves it via `acp_mod._supervisors[sid]`.

**`_notify()` helper** (`test_web.py:11435`): currently calls
`acp_mod._supervisor._on_notification(session_id, method, params)`. Update to route through
the session's supervisor:
```python
def _notify(acp_mod, session_id, method, params):
    sup = acp_mod._supervisors.get(session_id)
    if sup is not None:
        sup._on_notification(session_id, method, params)
```

**`acp_session` fixture** (`test_web.py:~3062`): this fixture registers a session directly on
`acp_mod._supervisor.sessions`. Refactor to use `_live_session()` for registration:
```python
@pytest.fixture
def acp_session(acp_store, ...):
    acp_mod, store = acp_store
    sid = _live_session(acp_mod, sid=FIXED_SID, cwd=str(store))
    yield acp_mod, sid
    acp_mod._supervisors.pop(sid, None)
```
Any direct `acp_mod._supervisor.X` accesses in this fixture should route to
`acp_mod._supervisors[sid].X`.

**`wf` fixture** (`test_web.py:13077`): currently yields `acp_mod, acp_mod._supervisor`.
Update to yield `acp_mod` and the parent session's supervisor:
```python
@pytest.fixture
def wf(acp_store, ...):
    acp_mod, store = acp_store
    parent_sid = _live_session(acp_mod, ...)
    sup = acp_mod._supervisors[parent_sid]
    # ... existing setup using sup instead of acp_mod._supervisor ...
    yield acp_mod, sup
    # ... teardown on sup._pending_permission, sup._workflow_step_questions, etc.
```

**`at_capacity()` call sites in tests**: update from
`acp_mod._supervisor.at_capacity()` to `acp_mod.at_capacity()`; update `_reserved` patches
from `acp_mod._supervisor._reserved = N` to `acp_mod._reserved = N`.

**`TestAcpSubagentListAttribution`** (`test_web.py:11663`): update 5 tests that put two session
ids into `inflight` on the same supervisor (structurally impossible in per-session model):
- Tests covering the "multi-inflight attribution ambiguity" path verify dead code â€” remove them.
- Replace with tests that verify the per-supervisor single-inflight attribution: one session in
  `inflight` â†’ subagent notification attributed correctly.

**`TestAcpIdleSweeper`** (`test_web.py:~17949`): `_sweep()` helper calls
`acp_mod._sweep_once()` (global). After Phase 2, the global sweep does not reclaim idle sessions.
Update the helper to call the per-supervisor `_sweep_once_local()` directly:
```python
async def _sweep(acp_mod, sid):
    sup = acp_mod._supervisors[sid]
    await sup._sweep_once_local()
```
Update `_idle()` helper: `acp_mod._supervisor.sessions[sid]["last_used"]` â†’
`acp_mod._supervisors[sid].sessions[sid]["last_used"]`.

**`TestAcpLifespanWiring`** (`test_web.py:~18538`): update to verify:
- `start_sweeper()` IS called from lifespan (starts the slimmed global SC-1 sweep)
- `start_watchdog()` is NOT called from lifespan (per-supervisor watchdog)
- Per-supervisor lifecycle tasks start when a session is created

**New multi-session test coverage:**
1. Two sessions, simultaneous prompts: verify neither `sessions[sid]["queued_behind"]` is set
   (key doesn't exist) and both complete without blocking.
2. `_find_supervisor` routes workflow child: create a parent supervisor with a workflow child
   in `_workflow_child_meta`; verify `_find_supervisor(child_sid)` returns the parent's supervisor.
3. D32 guard with multiple pids: set `_acp_live = {"sess_a": 100, "sess_b": 200}`;
   inject a kiro-cli sidecar with pid 100 and session_id "sess_orphan"; verify the D32 guard
   skips it (it's ACP-owned but not a registered session) without affecting pid 200's sessions.

**Exit criteria**:
- [x] `pytest tests/test_web.py -x -q --timeout=300` passes with no failures or errors
- [x] SC-3: full test suite pass confirmed
- [x] `grep -c "acp_mod\._supervisor\b" tests/test_web.py` returns 0
  (word-boundary anchor catches end-of-line references; `acp_mod._supervisors` remains valid)
- [x] At least one new test verifies two parallel sessions complete without blocking
- [x] At least one new test verifies `_find_supervisor` workflow child routing
- [x] D32 guard multi-pid test added and passing (addresses Risk Assessment R2)
- [x] `TestAcpSubagentListAttribution` updated: multi-inflight tests removed, single-supervisor
  inflight attribution tests added
- [x] `TestAcpIdleSweeper` updated to drive `_sweep_once_local()` per-supervisor path

---

### Phase 4: Documentation ripple and memory measurement [QA]

**Goal**: Fix all stale documentation references found by the doc-impact analysis;
measure per-session process memory cost; update `MAX_SESSIONS` default if warranted.

**File scope**: `src/power_atlas/acp.py` (potential cap update), `docs/KNOWLEDGE.md`,
`AGENTS.md`, `memory/MEMORY.md` (proposals â€” user approval required per memory governance),
`plans/ROADMAP.md`, `README.md`

**Covers**: SC-4, SC-5

#### Memory measurement (do first, before doc updates)

With PA running and 1, 2, 4, and 8 sessions open (one active prompt each):
```powershell
# Measure per-session kiro-cli process tree cost
Get-Process kiro-cli, bun, node | Measure-Object WorkingSet64 -Sum
# Also measure per-supervisor baseline (one session, idle):
# (Subtract base PA overhead from each measurement)
```
Record: baseline (0 sessions), per-session increment, total at cap. If per-session cost is
substantially higher than the v3 shared-tree cost (which was roughly proportional to
`MAX_SESSIONS`), propose a cap reduction; otherwise, keep at 8 or raise to 16.

#### `docs/KNOWLEDGE.md` updates

| Location | Update |
|---|---|
| Line ~52 (`queued_behind`, `queue_status` paragraph) | Remove â€” feature no longer exists |
| Line ~62 ("one process holds multiple concurrent sessionsâ€¦ viable") | Update: shared-process architecture was superseded; per-session processes now used |
| Line ~114 (`_supervisor.commands_execute`) | Update to `_find_supervisor(sid).commands_execute(...)` |
| Line ~380 (v3 process tree, `_maybe_recycle_idle`, `AGENT_IDLE_RECYCLE_SECONDS`, `~161 MB`) | Full rewrite with measured per-session costs |

#### `README.md` updates

| Location | Update |
|---|---|
| Line 365 (`acp_max_sessions = 8 # ~3 processes and ~161 MB`) | Update with measured v3 per-session cost |
| Line 441 ("one supervised `kiro-cli acp --agent-engine v3` process holds every session") | Rewrite: "one `kiro-cli acp` process per session" |

#### `AGENTS.md` update

| Location | Update |
|---|---|
| Line 83 ("drive `power_atlas.acp._supervisor`") | Update to `power_atlas.acp._find_supervisor(sid)` or `power_atlas.acp._supervisors[sid]` for protocol questions probe |

#### `plans/ROADMAP.md` updates

| Location | Update |
|---|---|
| "One kiro-cli process per ACP session" spike item (already partially updated) | Confirm closed; link plan; remove "done when" language |
| Line ~322 (`AGENT_IDLE_RECYCLE_SECONDS` in Clean Close item) | Update rationale â€” idle recycle removed; close is local-only, process killed on session close |
| Lines ~93â€“94 (Kiro Crew positioning note) | Note PowerAtlas now diverges from Kiro Crew's shared-process shape |
| Line ~180 (Scheduled tasks: "all sessions share ONE process tree") | Update to reflect per-session processes |
| Line ~326 (session cap rationale) | Update with measured v3 per-session cost figure |

#### `memory/MEMORY.md` proposals (user approval required before writing)

These are memory entries owned by the user. Propose each via `Save / Skip / Edit?`:

1. **Line ~342** ("Per-session server state that must survive reconnect/reload"): The
   `queued_behind` / `queue_status` motivating example is dead code. Either remove the
   example (the general principle about `_handle_subscribe` session frame is still valid)
   or replace with a live example from the new per-session model.

2. **Line ~471** (D9 boundary reasoning, `_supervisor.sessions`): Update to note that
   `_supervisor.sessions` became `_supervisors[sid].sessions` in `261020_ACP_PER_SESSION_PROCESS`.
   The D9 boundary (no `presence â†’ acp` import) remains; the note about cross-thread iteration
   still applies to each supervisor's `sessions` dict.

3. **Lines ~596, 598â€“599** ("Workflow step sessions: use `_supervisor._request(...)` directly"):
   Update to `_find_supervisor(sid)._request(...)` and `_find_supervisor(sid).prompt()`.

**Exit criteria**:
- [x] SC-4: Per-session memory cost measured and recorded in `docs/KNOWLEDGE.md` under
  a "Per-session process cost (v3, measured)" heading with date, kiro-cli version, and RSS figures
  for 1, 2, 4, and 8 sessions
- [x] SC-5: All doc-impact hits addressed â€” README.md, AGENTS.md, ROADMAP.md, KNOWLEDGE.md
  updated; memory/MEMORY.md proposals surfaced for user approval and disposition recorded
- [x] `grep -n "queued_behind\|queue_status\|_set_queued_behind" docs/KNOWLEDGE.md plans/ROADMAP.md README.md memory/MEMORY.md` returns no un-addressed hits
- [x] `grep -n "all sessions share ONE process\|one supervised.*kiro-cli.*process holds every" docs/ README.md plans/ROADMAP.md` returns no hits
- [x] `src/power_atlas/acp.py`: `MAX_SESSIONS` updated to measured-safe default if measurement
  shows current default is unsafe; `_clamped` ceiling updated accordingly
- [x] PA restarts cleanly after any `acp.py` cap change

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `_find_supervisor` O(N) scan is wrong for some workflow child â€” step prompt goes to wrong process | High â€” session stall | Dedicated unit test in Phase 3 verifying correct parent supervisor routing for a live workflow child |
| D32 guard generalization misses a pid â€” orphan lock files leak into live-dot presence | Medium â€” ghost "live" dots | Explicit test: inject one orphan pid and N live pids into `_acp_live`; verify D32 skips exactly the orphan |
| Per-session process memory is much higher than expected â€” 8 sessions exhausts RAM | High â€” OOM on cap | Phase 4 measurement is a prerequisite for any cap change; keep default at 8 until measured |
| Test fixture refactor introduces subtle behavioral differences â€” test passes but tests wrong behavior | High | Mutation testing after Phase 3; each refactored test group runs in isolation (no cross-test supervisor bleed) |
| `close_session` one-await (`_answer_pending_cancelled`) â€” Phase 1 refactoring assumes zero-await path; may need updated invariant reasoning | Low | Document the await in the per-supervisor sweep task's invariant comment; close path already tested |
| `_publish_live` called from multiple supervisors between event-loop ticks â€” race on `_acp_live` dict update | Low â€” GIL rebind atomicity on CPython | `publish_acp_sessions` does a single `dict(mapping)` rebind; GIL atomicity holds; document assumption |

| Risk | Disposition |
|---|---|
| Memory higher than expected â†’ cap change needed | Accepted: measured in Phase 4; cap change is an exit criterion |
| Concurrent `_secrets.store` during session startup burst | Accepted: existing `threading.Lock` sufficient |

## 7) Verification

**Phase 1 live test** (requires PA restart permission â€” granted 2026-10-20):
1. Restart PA
2. Open two `/acp` sessions in different workspaces
3. Send a long-running prompt to Session A (e.g., "count from 1 to 100 slowly")
4. Immediately send a prompt to Session B
5. Verify Session B starts responding without waiting for Session A to finish
6. Verify the steer handler in Session B never shows `queued_behind` error

**Phase 2 dead code check**:
```powershell
# Should return no hits
Select-String -Path src/power_atlas/acp.py -Pattern "queued_behind|_set_queued_behind|queue_status|_maybe_recycle_idle|AGENT_IDLE_RECYCLE_SECONDS"
```

**Phase 3 test suite**:
```powershell
.venv-PowerAtlas\Scripts\python -m pytest tests/test_web.py -x -q --timeout=300
```

**Phase 4 memory measurement**:
```powershell
# With 0 sessions open (baseline):
Get-Process kiro-cli, bun, node -ErrorAction SilentlyContinue | Measure-Object WorkingSet64 -Sum

# With 1, 2, 4, 8 sessions open (repeat for each):
# (Create sessions from /acp picker, let each reach "ready" state, then measure)
Get-Process kiro-cli, bun, node | Measure-Object WorkingSet64 -Sum
```

## Dependency Graph

```
Phase 1 (core migration)
  â””â”€â†’ Phase 2 (dead code removal)
        â””â”€â†’ Phase 3 (test fixture)
              â””â”€â†’ Phase 4 (docs + measurement)
```

## Backwards Compatibility

| Item | Strategy | Safety effect |
|---|---|---|
| `/ws/acp` WebSocket protocol | Unchanged â€” clients see identical JSON-RPC frames | None |
| `/api/acp/sessions` HTTP API | `held` set now unioned across all supervisors; semantics identical | None |
| `acp-secrets.bin` | Shared module-level singleton; MCP OAuth tokens unaffected | None |
| `presence.publish_acp_sessions` signature | Internal only; changed from `(frozenset, int)` to `dict[str, int]`; all callers updated in same phase | Internal breaking change, contained |
| `kiro-cli` ACP protocol | Unchanged â€” same `initialize` / `session/new` / `session/load` sequence; one process per session uses identical wire format | None |

## File Change Summary

### Modified
- `src/power_atlas/acp.py` (Phases 1, 2, 4)
- `src/power_atlas/presence.py` (Phase 1)
- `src/power_atlas/web.py` (Phases 1, 2)
- `tests/test_web.py` (Phase 3)
- `docs/KNOWLEDGE.md` (Phase 4)
- `AGENTS.md` (Phase 4)
- `memory/MEMORY.md` (Phase 4 â€” proposals only; user approval required)
- `plans/ROADMAP.md` (Phase 4)
- `README.md` (Phase 4)

### Created
None.

### Deleted
None.

### Unchanged
- `src/power_atlas/data.py`, `data_claude.py`, `data_codex.py`, `data_kiro_v3.py`,
  `agent_profile.py`, `launcher.py`, `overview.py`, `config.py`, `resume.py`, `quota.py`
- All templates, static assets
- All tests outside `tests/test_web.py`

## Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Core migration â€” routing, spawn, handlers [QA] | Complete | commits 88efd8a, d760bac |
| 2 | Dead code removal and per-supervisor sweep [QA] | Complete | commits ff417f6, 65a2f96 |
| 3 | Test fixture refactor | Complete | commits 87433dcâ€“5a90da4 |
| 4 | Documentation ripple and memory measurement [QA] | Complete | commit ed147be |

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `src/power_atlas/acp.py` | `at_capacity()` error message: remove "~3 processes and ~161 MB" | 2 |
| `docs/KNOWLEDGE.md` | Remove `queued_behind`/`queue_status` paragraph (~line 52) | 4 |
| `docs/KNOWLEDGE.md` | Update "one process holds multiple concurrent sessions" (~line 62) | 4 |
| `docs/KNOWLEDGE.md` | Update `_supervisor.commands_execute` reference (~line 114) | 4 |
| `docs/KNOWLEDGE.md` | Full rewrite of v3 process model paragraph (~line 380) with measured costs | 4 |
| `AGENTS.md` | Update `power_atlas.acp._supervisor` protocol probe instruction (~line 83) | 4 |
| `plans/ROADMAP.md` | Close "one kiro-cli process per ACP session" spike item | 4 |
| `plans/ROADMAP.md` | Update Kiro Crew positioning note (~lines 93â€“94) | 4 |
| `plans/ROADMAP.md` | Update Scheduled Tasks process-tree note (~line 180) | 4 |
| `plans/ROADMAP.md` | Update `AGENT_IDLE_RECYCLE_SECONDS` in Clean Close item (~line 322) | 4 |
| `plans/ROADMAP.md` | Update session cap rationale (~line 326) with measured cost | 4 |
| `README.md` | Update `acp_max_sessions` comment (~line 365) | 4 |
| `README.md` | Update "one process holds every session" sentence (~line 441) | 4 |
| `memory/MEMORY.md` | Propose update: `queued_behind`/`queue_status` example in reconnect-state entry (~line 342) â€” user approval required | 4 |
| `memory/MEMORY.md` | Propose update: D9 boundary note `_supervisor.sessions` (~line 471) â€” user approval required | 4 |
| `memory/MEMORY.md` | Propose update: workflow step `_supervisor._request` â†’ `_find_supervisor` (~lines 596â€“599) â€” user approval required | 4 |

### Phase 2 â€” Implementation (2026-10-20, code: ff417f6, fix: 65a2f96)

**Scope**: `src/power_atlas/acp.py`, `src/power_atlas/web.py`

**Delivered per plan**: `_maybe_recycle_idle`, `AGENT_IDLE_RECYCLE_SECONDS`, `_idle_since` removed; `_set_queued_behind`, `queued_behind` meta writes (6 sites), `_info_kind == "queued"` branch, `queue_status` frame and `SERVER_TYPES` entry, `turn_queued` steer guard all removed; `_sweep_failures`/`_sweep_held_logged`/`_sweep_awaiting_failures` module-level dicts removed; `_sweep_once()` slimmed to workflow-reap + SC-1 orphan + presence backstop; `_start_lifecycle_tasks()`, `cancel_lifecycle_tasks()`, `_sweep_once_local()` added to `_Supervisor`; wired into `_handle_new`/`_handle_load` and `_detach()`; `start_watchdog()` removed from `web.py` lifespan.

**Review findings** (full effort â€” Green):
- L1: `start_watchdog()` dead code remained in `acp.py`. **Fixed** (65a2f96).
- L2: Per-supervisor sweep has no close-failure retry. Accepted as design â€” watchdog covers process death; retry not warranted.

**Live QA**: PASS (run by coder inside workflow session):
- EC-1 âœ… PA started cleanly
- EC-2 âœ… Session created, prompt responded
- EC-3 âœ… Session closed cleanly ("0 live")
- EC-4 âœ… Process tree killed ("12 process(es) killed")
- EC-5 âœ… Capacity freed (held:0 after close)
- EC-6 âœ… All 5 dead-code greps return zero hits

### Phase 3 â€” Implementation (2026-10-20, commits 58acca7â€¦5a90da4)

**Scope**: `tests/test_web.py`

**Delivered per plan**: `acp_store` fixture updated with `_supervisors`/`_reserved` resets; `_live_session()` returns `str`, creates per-session supervisor; `_notify()` routes through `_supervisors`; `acp_session` and `wf` fixtures migrated; all 1,109 `acp_mod._supervisor` references migrated; `TestAcpSubagentListAttribution` multi-inflight tests removed; `TestAcpIdleSweeper._sweep()` replaced with inline per-supervisor tick logic (direct `_sweep_once_local()` call would hang â€” correctly avoided); `TestAcpLifespanWiring` updated; 3 new multi-session tests added.

**Review findings** (2 build-loop iterations + 1 extension; final pass: APPROVED):
- Build loop produced 3 revision passes total. Main issues resolved across passes: 145â†’22â†’0 test failures; D32 guard test made non-vacuous (calls actual guard logic); `start_watchdog` tracking added.
- Final pass: all 3,546 tests pass, 0 singleton refs, 7/7 focus areas pass.



**Scope**: `src/power_atlas/acp.py`, `src/power_atlas/presence.py`, `src/power_atlas/web.py`

**Delivered per plan**: `_supervisor` singleton removed; `_supervisors: dict[str, _Supervisor]` + `_reserved: int` added; `_find_supervisor()`, `_deregister_supervisor()`, `_publish_live_all()`, module-level `at_capacity()` added; all `_handle_*` functions route through `_find_supervisor()`; `_handle_new`/`_handle_load` create per-session supervisors with registration-before-early-return; `presence.py` D32 guard generalised to `frozenset` of all ACP pids; `web.py` helpers iterate `_supervisors` dict; sibling-copy optimization removed.

**Divergences**:
- `load_session()` sibling-copy lines removed as planned; confirmed no equivalent in `new_session()` path.
- `at_capacity()` moved to module level; `_find_permission_entry()` (a related helper) also moved to module level as a natural consequence.
- The `_sweep_once()` inner `for session_id, meta` loop used 2-space relative indentation (6 vs. 8 expected) â€” fixed in d760bac.

**Review findings** (Cycle 1 â€” full effort â€” Green):
- M1: `_handle_new`/`_handle_load` missing `sup._discard()` on `new_session()`/`load_session()` failure â€” orphan process until kiro-cli idle timeout. **Fixed** (d760bac): added `sup._discard("session creation/load failed")` to all four exception handlers.
- L1: Redundant `_publish_live_all()` calls on close (4Ã—, harmless). Deferred.
- L2: Stale docstring refs to `_supervisor.*` at acp.py lines 8683, 8685, 8808. Deferred to Phase 4 doc ripple.
- L3: 2-space indentation in `_sweep_once()` inner loop. **Fixed** (d760bac).

**Live QA**: PASS (2026-10-20, PA restarted per pre-approved grant):
- EC-1 âœ… PA loads; `/api/acp/sessions` responds
- EC-2 âœ… Session B ("session B reply test") responded immediately without waiting for Session A's turn to finish; no serialization observed
- EC-3 âœ… `_supervisors` dict confirmed; `_supervisor` singleton removed; `_reserved` int = 0
- EC-4 âœ… `presence._acp_live` is a dict
- SC-1 âœ… 0 "queued" messages in orchestrator.log (last 200 lines) during the parallel test
- EC-6 âœ… Create-close-create cycle: both sessions closed (0/8), new session (`sess_bba968ed`) created without capacity refusal
- Process kill âœ… kiro-cli count: 8 with 2 sessions â†’ 6 with 0 sessions (delta of 2, matches sessions created and closed)

## Follow-up Work (Deferred)

1. **Warm pool for reduced session creation latency.** On-demand spawn adds ~2-4 s spinner
   to session creation. A warm pool (1-2 pre-initialized supervisors) could hide this.
   Source: Q3 (on-demand chosen over warm pool), Phase 4 measurement will confirm actual latency.

2. **Session cap increase beyond 16.** Current `_clamped` ceiling is 16. If per-session memory
   is low enough, a higher cap may be warranted. Gated on Phase 4 measurement.
   Source: Open item 2 in Exploration Discovery.

3. **`_find_supervisor` lookup cache.** An `O(N)` scan over `_supervisors.values()` for workflow
   children is negligible at `MAX_SESSIONS = 8â€“16`. If the cap grows substantially, add a
   supplementary `_child_to_supervisor: dict[str, _Supervisor]` cache.
   Source: Design Decisions (workflow step routing).

## Review Log

### Cycle 1 â€” 2026-10-20 â€” Full effort (user override; default for Major: standard)

**Reviewers**: Senior engineer, Concurrency/reliability engineer, Test engineer, Plan quality/leanness reviewer

**Health after cycle 1 + fixes**: Green

| # | Finding | Sev | Resolution |
|---|---|---|---|
| 1 | `_bubbles.clear()` in `_detach()` destroys all sessions' state on any supervisor crash | High | Fixed: Phase 1 mandates `self._session_id` attribute; `_detach()` uses `_bubbles.pop(self._session_id)` |
| 2 | Supervisor deregistration (`_supervisors.pop`) never specified | High | Fixed: Phase 1 adds `_deregister_supervisor()` called from both `close_session()` and `_detach()` |
| 3 | Session close does not kill process â€” `_maybe_recycle_idle` removed with no replacement | High | Fixed: Phase 1 specifies `close_session()` â†’ `_deregister_supervisor()` â†’ `_detach()` â†’ `_dispose()` |
| 4 | `_sessions_changed_hook` NameError in `_publish_live_all` snippet | High | Fixed: correct name `sessions_changed_hook` with inline comment citing `acp.py:1399` |
| 5 | `set_sessions_changed_hook()` still calls `_supervisor._publish_live()` after migration | High | Fixed: Phase 1 specifies updating this call to `_publish_live_all()` |
| 6 | Disconnected-socket early return in `_handle_new` orphans a supervisor | High | Fixed: Phase 1 mandates registration before early-return check + `_deregister_supervisor()` on early exit |
| 7 | `acp_store` proposed yield change broke 76 tests | High | Fixed: Phase 3 keeps `(acp_mod, tmp_path)` yield; adds `_supervisors`/`_reserved` resets only |
| 8 | `acp_store` missing 7 module-level cleanup items | High | Fixed: Phase 3 explicitly preserves all existing cleanup |
| 9 | `acp_session` fixture not in Phase 3 scope | High | Fixed: Phase 3 adds full `acp_session` migration specification |
| 10 | `_live_session` proposed return-type change broke 81 call sites | High | Fixed: Phase 3 mandates `_live_session()` keeps returning `sid` (string) |
| 11 | `TestAcpSubagentListAttribution` multi-inflight tests need deletion not simplification | High | Fixed: Phase 3 specifies removing multi-inflight tests + adding single-supervisor attribution tests |
| 12 | Phase 2 idle-reclaim exit criterion unverifiable | High | Fixed: Phase 2 adds `Get-Process` check confirming processes die on session close |
| 13 | `_detach()` scoped bubble clear is no-op in normal-close path | Medium | Fixed: `self._session_id` mandated; `_detach()` uses it rather than iterating `self.sessions` |
| 14 | Tuple-unpack `acp_sids, acp_pid = _acp_live` not removed | Medium | Fixed: Phase 1 explicitly specifies removing the tuple-unpack |
| 15 | Phase 2 sweeper contradiction | Medium | Fixed: Phase 2 clarifies `start_sweeper()` stays; only `start_watchdog()` removed |
| 16 | SC-1 orphan-buffer sweep references removed singleton | Medium | Fixed: Phase 2 adds explicit SC-1 loop iterating `_supervisors.values()` |
| 17 | `attention_snapshot()` aggregation not in `web.py` changes | Medium | Fixed: Phase 1 adds aggregation across all supervisors |
| 18 | `_reserved` at 3 `web.py` sites, plan noted only 1 | Medium | Fixed: Phase 1 lists all 3 sites (`web.py:4361, 4430, 4947`) |
| 19 | Phases 1/2 no automated test coverage â€” unstated | Medium | Fixed: exit criteria explicitly note live-QA-only during these phases |
| 20 | `_bubbles` migration and sibling-copy removal unassigned | Medium | Fixed: Phase 1 covers both |
| 21 | Dead `pid` variable in `_publish_live_all` snippet | Medium | Fixed: replaced with `_publish_live_all()` module function; dead variable removed |
| 22 | Phase 2 live QA criterion too vague | Medium | Fixed: concrete create-close-create cycle with process inspection |
| 23 | D32 guard multi-pid test had no phase ownership | Medium | Fixed: Phase 3 assigns and exit-criteria-tracks |
| 24 | Exit-criterion grep missed end-of-line references | Medium | Fixed: Phase 3 grep uses `\b` word boundary |
| 25 | `TestAcpIdleSweeper` not addressed | Medium | Fixed: Phase 3 specifies `_sweep_once_local()` routing |
| 26 | `_notify` helper routes through removed singleton | Medium | Fixed: Phase 3 specifies `_notify()` refactor |
| 27 | Mutation testing a note only, not a criterion | Medium | Fixed: D32 guard + workflow child routing added as exit criteria |
| 28 | `at_capacity()` test call sites not addressed | Medium | Fixed: Phase 3 covers test-side `at_capacity()` and `_reserved` patches |
| 29 | `wf` fixture yields `acp_mod._supervisor` directly | Medium | Fixed: Phase 3 specifies `wf` fixture migration |
| 30 | `_find_supervisor` loop variable shadowed `sup` | Low | Fixed: renamed loop variable to `s` |

### Step 9 Holistic Review â€” 2026-10-21 â€” Full effort (4 personas, all applicable)

**Reviewers**: Senior engineer, Concurrency/reliability, Test engineer, Security

**Health after Step 9 + fixes**: Green (commit c1a550a â€” 4,276 tests pass)

| # | Finding | Sev | Resolution |
|---|---|---|---|
| S1 | Double `_reserved` increment: `_handle_new` + `new_session()` both increment, halving effective cap to 4/8 | High | Fixed (c1a550a): removed `_reserved` pair from `_handle_new`; `new_session()` is sole manager |
| S2 | `CancelledError` leaks `_reserved` permanently â€” no `BaseException`/`finally` guard | Medium | Fixed (c1a550a): `try/finally` wraps create block in both `_handle_new` and `_handle_load` |
| S3 | Close-then-reload race: `close_session()` removes from sessions before await, `closing` never set | Medium | Fixed (c1a550a): `self.closing.add(session_id)` at top of `close_session()` before any mutation |
| S4 | D32 guard test vacuous â€” only tests frozenset constructor, never calls `presence._scan()` | Medium | Fixed (c1a550a): real test in `test_data.py` that calls guard logic; mutation fails the test |
| S5 | `start_watchdog` lifespan fake accepts calls silently â€” Phase 2 "NOT called" claim unverifiable | Medium | Fixed (c1a550a): call counter added; assertion `watchdog_calls == 0` |
| S6 | Double `_deregister_supervisor` / redundant `_publish_live_all` on 3 exit paths | Low | Fixed (c1a550a): removed explicit calls from callers; `_detach()` is sole site |
| S7 | `None` pids enter D32 `acp_pids` frozenset during spawn window | Low | Fixed (c1a550a): `None` filtered in `_publish_live_all()` at source |
| S8 | `_acp_live` type annotation `dict[str, int]` misleads â€” can hold `None` values | Low | Fixed (c1a550a): annotation updated to `dict[str, int \| None]` (pre-filter); aligned `publish_acp_sessions` |
| S9 | `_start_lifecycle_tasks()` called before `load_session()` in `_handle_load` | Low | Fixed (c1a550a): moved to after `load_session()` succeeds |
| S10 | `_sweep_once_local()` loop body unguarded â€” unexpected exception kills idle-TTL permanently | Low | Fixed (c1a550a): loop body wrapped in `try/except Exception: log.exception(...)` |
| S11 | `_find_supervisor` subagent_sessions path untested | Low | Fixed (c1a550a): test added for `subagent_sessions` lookup branch |
| S12 | Test name overpromises (`do_not_block_each_other` verifies state isolation, not turn scheduling) | Low | Fixed (c1a550a): renamed to `test_two_sessions_have_independent_inflight_state` |

## Harness Improvement Opportunities

## Completion Summary

**Completed**: 2026-10-21  
**What was built**: Replaced the PowerAtlas ACP single shared kiro-cli process with one dedicated `kiro-cli acp` process per ACP session. Two ACP sessions now run fully concurrently — no turn serialization, no "Waiting for another session's turn to finish" messages. The singleton `_supervisor` became `_supervisors: dict[str, _Supervisor]` with `_find_supervisor()` routing, per-supervisor lifecycle tasks, and complete dead-code removal (`queued_behind`, `queue_status`, `_maybe_recycle_idle`). 1,109 test fixture references migrated; 4,276 tests pass.

**Measured per-session cost**: 555–672 MB WorkingSet64 per session (kiro-cli 2.31.1); ~7.6 GB at cap 8.  
**Step 9 fixes**: 1 High (double `_reserved` halving capacity), 4 Medium (CancelledError leak, close-reload race, vacuous D32 test, lifespan wiring assertion), 7 Low.  
**QA**: PASS — parallel sessions confirmed, dead code absent from wire, capacity counter correct, process lifecycle verified.


