# ACP v3 Production Hardening

> **Date**: 2026-09-08
> **Status**: Draft
> **Scope**: Bring `/acp-v3` (kiro-cli v3 ACP protocol support) from throwaway-spike quality to production quality, on par with the mature `/acp` (v2) surface — without merging the two engines.
> **Estimated effort**: 2-3 days

---

## Intent

### Problem statement & desired outcomes

`plans/done/260908-1636_ACP_V3_SPIKE.md` built a working `/acp-v3` prototype and validated protocol feasibility, but was explicitly scoped as throwaway spike infrastructure. A live-testing pass against the running PowerAtlas instance found it functionally usable for the core loop (create/prompt/stream/edit/reload/resume/close) but confirmed one real regression-shaped gap (session deletion, explicitly stubbed as not-implemented) and one root-caused cosmetic bug. Deep codebase research (three parallel sub-agent traces) and live protocol probes against a standalone kiro-cli v3 process (bypassing the running PowerAtlas app entirely) then surfaced a substantially larger set of gaps: several shared helper functions silently no-op for v3 sessions because they're hardcoded to the v2 singleton, one entire notification kind (`session_info_update` — carrying context %, steering feedback, title updates, and MCP-auth signals) has no dispatch branch on either engine, slash-command execution is entirely broken on v3, and the crew/subagent panel has zero v3 wiring despite the underlying protocol support being confirmed present and simpler than v2's. A final round of live probing (post-exploration, in response to user pushback on two proposed scope narrowings) confirmed subagent fan-out works fully on v3 regardless of the `autonomousAgents` governance flag, and discovered a previously-unknown inbound request (`session/request_permission`) that the agent uses to ask structured clarifying questions — unhandled today, causing a silent hang.

Desired outcome: `/acp-v3` reaches the point where it can be recommended as a genuine v2-parity alternative, hardened enough that a user reaching for it hits no silent data loss, no silently-broken features, and no unhandled hangs — while `_SupervisorV3` remains a separate class from `_Supervisor` (merging them is an explicit non-goal of this pass, per user decision).

### Success criteria

- SC-1: The `new_session()` notification-drop race (frames arriving before `self.history[session_id]` is registered — confirmed via orchestrator.log evidence, root-caused to asyncio scheduling order) is fixed for `_SupervisorV3` specifically.
- SC-2: Individual v3 session deletion works end-to-end (`sess_`-prefixed IDs no longer hit "not yet implemented"), and workspace-level delete correctly enumerates and deletes v3 sessions too (currently silently blind to them, per `_acp_sessions_for_workspace`'s own docstring).
- SC-3: `_on_notification` gains a `session_info_update` branch (v3-specific — v2 never emits this kind) that surfaces: context-window percentage, the three-state steering echo (`steering_queued`/`steering_injected`/`steering_cleared`), session title/focus updates, and MCP-auth-required signals (`_meta.kiro.displayError`).
- SC-4: `_flush_bubble`, `_emit_subagents_frame`, and `_handle_subagent_subscribe` (currently hardcoded to the v2 `_supervisor` singleton even when invoked from a v3 code path) are made engine-aware, so v3 sessions get correctly-recorded "rendered" markdown frames and correctly-routed sub-agent detail-panel replay.
- SC-5: The crew/subagent panel works on v3 — live, mid-turn, not just at turn-end. Confirmed wire shape (live probe): subagent spawn/progress/completion arrives as ordinary `tool_call`/`tool_call_update` frames tagged `_meta.kiro.kind: "agent-subtask"` with an `agentSubtaskId`, and the subagent's own output streams as `agent_message_chunk` tagged with the same `agentSubtaskId` — no child session exists at all (architecturally simpler than v2). Server-side translation into the existing (engine-agnostic) `subagents` frame shape is the only new work; the client-side `setCrew()` renderer needs no changes. Confirmed unaffected by the `autonomousAgents: false` / `admin_disabled` governance flag (live-verified: a real two-subagent fan-out completed successfully under that exact flag state).
- SC-6: `presence.py`'s `_match_provider` correctly attributes resumed v3 sessions to `"kiro-cli-v3"` instead of always `"kiro-cli"` (currently dead code — the `"kiro-cli-v3"` dict entry is unreachable).
- SC-7: Production liveness detection is implemented per the spike's Phase 5 recommendation — read `session.json`'s `status` field, zero ACP round-trip. Re-confirmed live this session on kiro-cli 2.21.1: concurrent `session/load` still succeeds (no refusal) despite v3 now writing `.lock` files, so lock-file-based checking remains the wrong approach.
- SC-8: `_kiro.dev/commands/options` and `_kiro.dev/commands/execute` — confirmed broken on v3 via live probe (`-32603`, identical "no persistence classification" error the spike found for `session/terminate`) — are replaced with the correct v3-native mechanism, found via a dedicated Phase 0 probe. Confirmed via client-code read that selecting *any* slash command from the `/acp-v3` palette currently triggers this broken path (`acp.html:2152`).
- SC-9: `session/request_permission` (a genuine inbound *request* — confirmed live via probe in spec mode: the agent asking a structured multiple-choice clarifying question and blocking the turn on an answer) is handled with a real interactive UI: the question and its named options render in the transcript, the turn blocks until the user picks one, the chosen `optionId` is sent back over the wire.
- SC-10: The "unrecognized notification kind" fallback (both `_Supervisor` and `_SupervisorV3`'s `_on_notification`) is promoted from DEBUG (invisible — production runs at INFO) to a visible level, so a future/unknown kind is diagnosable from `orchestrator.log` without deliberately enabling DEBUG first.
- SC-11: `_SupervisorV3` overrides and all 9 `_handle_*_v3` WS-frame functions get direct test coverage (currently zero — confirmed via full-file grep against `tests/test_web.py`). 5 of the 9 handlers (`new`, `prompt`, `steer`, `permission_response`, `commands_execute`) get coverage alongside their phase's fix; the other 4 (`load`, `subscribe`, `cancel`, `close` — untouched by any phase's fix) get baseline characterization tests added in Phase 1, so the "zero coverage" gap is fully closed even where no behavior changes.
- SC-12: All existing tests (`pytest`, `node tests/acp_page.test.mjs`) continue to pass, and `/acp` (v2) is verified unaffected by every change (shared-helper fixes in particular touch code v2 also calls) — via one user-approved PowerAtlas restart and live click-through of both `/acp` and `/acp-v3`.

### Invariants

- `/acp` (v2) behaves identically to before this plan for every session lifecycle action: create, prompt, cancel, steer, close, delete, resume. No shared-helper fix (SC-4) or dispatch-table behavior change (SC-3) may alter v2's observed output. (Exception, explicitly carved out: SC-10 changes both classes' unrecognized-notification-kind log level from DEBUG to INFO — a v2 logging-visibility change, not a v2 behavior change; observed session output is unaffected.)
- Every v3 feature already confirmed working before this plan (session creation with `sess_`-prefixed IDs, prompt/streaming round-trips, tool-call and diff rendering for normal tool calls, reload/resume replay, close session, slash-command palette *population*, workspace picker, rail grouping) continues to work exactly as before.
- `record()`'s silent-no-op-on-unregistered-session idiom (`acp.py:4254-4266`) is preserved — it must never raise — even as SC-1's fix changes when a v3 session's history buffer exists.
- The existing `_pending_commands` single-slot buffering behavior (`acp.py:4150`) for the commands/skills catalogue is unaffected by any new keyed buffer SC-1 introduces.
- `_kiro.dev/metadata`-based v2 context-percentage tracking (`_note_context`, `METADATA_METHOD` branch) is unchanged, even though SC-3 adds a parallel v3-only path for the same concept via a different wire mechanism.
- The full existing test suite (`pytest`, `node tests/acp_page.test.mjs`) continues to pass throughout — no phase may leave the suite red.

### Scope boundaries & non-goals

**In scope:** every gap enumerated in SC-1 through SC-11, all confirmed live where a live probe was possible (standalone-probed this session: concurrent-load behavior, `_session/steer`, `commands_options`/`execute`, a real two-way subagent fan-out, and spec-mode's `session/request_permission` — all against a throwaway kiro-cli v3 process bypassing the running PowerAtlas app, cleaned up after use).

**Explicitly out of scope, with reasoning:**
- **Merging `_SupervisorV3` into `_Supervisor`.** User decision. 84% of `_SupervisorV3`'s methods already inherit unchanged from `_Supervisor`, and zero drift has occurred in the shared `_on_notification` logic since the spike. Stays a separately-scoped future follow-up.
- **Linux cross-platform support.** User decision. v3's `_spawn` reuses the identical Windows-only `_create_job()` guard v2 already has — not a v3-specific parity gap.
- **Mode-switcher UI** (letting a user pick `spec`/`quick-spec`/`bug-fix`/`plan` instead of always `kiro_default`). `/acp-v3` stays hardcoded to `kiro_default`, matching that `/acp` v2 has no mode-selection UI either. SC-9's `session/request_permission` handling exists to protect `kiro_default` sessions from any built-in tool that might ask a clarifying question, not to unlock other modes as a feature.
- **Full MCP OAuth "Connect" flow.** The real signal (`session_info_update` → `_meta.kiro.displayError`) carries a human-readable message, not a structured `authorizationUrl` — SC-3 covers surfacing the message; a full OAuth completion flow isn't buildable from what's confirmed.
- **`_kiro/spec/*` notifications as originally imagined in ROADMAP.md.** No such dedicated notification methods exist — spec mode only adds a `"spec"` tag to the builtin-tools list and relies on ordinary `session/update` subtypes plus (per SC-9) `session/request_permission`.
- **"Kiro Crew"** (`kiro-cli crew` subcommand) — confirmed to be a wholly separate, standalone installable CLI product, unrelated to the ACP protocol surface.
- **Removing or deprecating `/acp` v2.**

---

## 1) Current State

**Governance, hard constraint**: `AGENTS.md:5` — *"Never restart PowerAtlas autonomously... Always defer to the user."* Every phase below touches `acp.py`, `web.py`, `presence.py`, or `status_classifier.py` — Python-level changes that need a process restart to verify live (`AGENTS.md:7`). This plan batches all such changes and verifies via `pytest` + standalone protocol probes (neither needs a restart) through Phase 7, reserving exactly one user-approved restart for Phase 8's live verification.

**`record()`'s silent-drop idiom is deliberate and repeated**: `acp.py:4254-4266` (`record`) and `acp.py:3245-3286` (`_stamp_activity`) both no-op silently when `session_id` isn't yet registered; `_stamp_activity`'s docstring (`3284`) states *"record() and _note_context() model the idiom."* SC-1's fix must respect this (never raise on an unregistered session) while closing the actual data-loss gap.

**The race (SC-1), precisely**: `_SupervisorV3.new_session()` (`acp.py:4788-4829`) can only learn `session_id` from the RPC result (`result["_meta"]["id"]`, line `4807`), after `await self._request("session/new", ...)` (line `4803`) resolves — `self.history[session_id] = _History()` follows at line `4816`. A notification for that same `session_id` (which KAS can send *before* the `session/new` result, per the reader thread's strict FIFO delivery via `call_soon_threadsafe`, `acp.py:3149-3161`) reaches `_on_notification` and calls `record()` while `self.history` has no entry yet — silently dropped. `_on_notification` extracts `session_id = params.get("sessionId")` (`4476`) directly from the notification's own payload, independent of RPC state — so the session is *identifiable* the moment the frame arrives, just not yet *registered*.

**One existing buffering precedent**, structurally too narrow to reuse as-is: `_pending_commands` (`acp.py:4150`, write sites `4019-4026`/`4642-4645`, flush `4143-4171`, called from `new_session` at `4123`/`4814`) buffers exactly one thing (the commands/skills catalogue) in a single last-writer-wins slot, justified because that content is session-agnostic. `tool_call` payloads are not interchangeable across sessions, so SC-1 needs a keyed (per-session, per-frame) buffer instead — see Design Decisions.

**acp.py's isolation-boundary constraint is narrower than its own docstring claims.** Docstring (`14-24`) says "exactly two names" (`config.CONFIG_DIR`, `launcher._SESSION_ID_RE`); actual imports (`82-84`) are three, including `data_kiro`. The mechanically-enforced rule (Phase 1 exit-criterion grep from the spike, still passing) only forbids importing `data_kiro_v3` into `acp.py`. `web.py` has no such constraint — it already imports `data_kiro_v3` freely via the provider registry (`data.py:98,106`). **Implication for SC-2**: a v3 delete helper belongs in `data_kiro_v3.py` (called from `web.py`), not inlined into `acp.py`.

**v2's delete implementation, to mirror for SC-2**: `api_acp_delete_sessions` (`web.py:3019-3113`) → `_acp_delete_many` (`web.py:2849-2915`) → `_acp_delete_session` (`web.py:2764-2846`), targeting `_acp_session_paths` (`web.py:2746-2761`: `KIRO_SESSION_DIR/{id}{.json,.jsonl,.history,.lock}` plus an optional bare-id directory). Uses rename-to-staging-then-unlink (`_ACP_DELETE_STAGING = ".pa-deleting"`) to survive Windows sharing violations (`winerror 32`). No `data_kiro.py` delete function exists — v2 deletion is bespoke in `web.py`.

**v3 on-disk layout, directly verified**: `~/.kiro/sessions/<workspace-hash>/sess_<uuid>/` is a **directory** containing `messages.jsonl`, `session.json`, `publish.cursor`, and — only for sessions that used subagents — `sub-executions/<agentSubtaskId>.jsonl` and `publish-sub.cursor`. A delete needs to remove the whole directory tree (`shutil.rmtree`-equivalent), not a fixed file-suffix list.

**`_acp_sessions_for_workspace`'s v2-only blindness, confirmed** (`web.py:2929-2930` docstring: *"covers only v2 sessions... v3 sessions... are not enumerated"*) — the workspace-level delete path silently no-ops for v3-only workspaces; SC-2 needs to close this too.

**`session_info_update` has no dispatch branch at all**, confirmed by direct grep (`session_info_update`/`contextUsage`/`steering_queued`: zero matches in `acp.py`) and by reading `_on_notification`'s full branch list (`3633-4045`/`4466-4653`) — `kind = update.get("sessionUpdate")` (`3637`) extracts it correctly, but no `if kind == "session_info_update"` branch exists. `METADATA_METHOD` (branch 2) only matches the separate, v2-only top-level method `_kiro.dev/metadata`. Every `session_info_update` frame falls through to the DEBUG-only fallback (`4036-4044`/`4651-4653`) today.

**Live-verified `session_info_update` `_meta.kiro.kind` vocabulary** (this session's probes): `context_usage` (`contextUsage.usagePercentage` + a token breakdown), `steering_queued`/`steering_injected`/`steering_cleared` (`messageId`, `content`), `focus_update` (session title, auto-derived from the first prompt), `user_message_id_assigned`, `turn_end` (redundant — turn-end is already read off the `session/prompt` RPC result, not any notification, so this can be ignored), `display_error` (`message`/`errorType`, e.g. `"mcp_connection_error"`), `pendingInteraction` (precedes a `session/request_permission` request with the same question/options as a preview — can be ignored, since the actual request carries the same data and is what needs answering).

**`_flush_bubble`, `_emit_subagents_frame`, `_handle_subagent_subscribe` are hardcoded to `_supervisor`** even when invoked from `_SupervisorV3`'s own paths: `_flush_bubble` calls `_emit(...)` unconditionally at `acp.py:5108`, never `_emit_v3`; `_emit_subagents_frame` reads `_supervisor.crews`/`_supervisor.crew_spawn_toolcallids` at `5037`/`5042`; `_handle_subagent_subscribe` reads `_supervisor.crews`/`_supervisor.subagent_history` at `5567`/`5579`. A correctly-scoped `_emit_subagents_frame_v3` already exists (`7269-7280`) but is only called at turn-end (`_handle_prompt_v3`'s `finally`, `6939`; `_evict_crew_children_v3`, `7305`), not on each live update.

**Confirmed live wire shape for SC-5**: subagent spawn is an ordinary `tool_call` with `title: "Sub-agent: <name>"`, `_meta.kiro.kind: "agent-subtask"`, `agentSubtaskId`, `rawInput: {name, prompt, explanation, contextFiles}` — **not** `_meta.kiro.toolName == "subagent"` (the field the existing spawner-anchor logic at `3806-3828`/`4529-4538` checks; that check never matches on v3). Completion: `tool_call_update` with `status: "completed"`, `rawOutput` carrying the subagent's final answer directly. The subagent's own streamed text arrives as `agent_message_chunk` frames tagged `_meta.kiro.agentSubtaskId` (not a separate child session).

**`presence.py`'s v3 gap is dead code, cosmetic-only in impact**: `_PROVIDER_SPECS` (`70-74`) declares `"kiro-cli"` before `"kiro-cli-v3"`; `_match_provider()` (`552-571`) returns on first match, so `"kiro-cli-v3"` is unreachable. `status_classifier.py:574-578` shows status computation itself is unaffected — `_classify_from_path` auto-detects the v3-format transcript via `_is_v3_format()` regardless of the mislabeled provider, and still calls `classify_kiro_v3()`. Only provider *attribution* is wrong.

**Liveness — `classify_kiro_v3()` already exists** (shipped in `260818_KIRO_CLI_V3_DASHBOARD_SUPPORT`, one day before the ACP v3 spike — a separate effort covering the read-only dashboard listing), classifying status from `messages.jsonl` tail `payload.type` values. The spike's Phase 5 recommendation — read `session.json.status` directly, zero round-trip — is unimplemented anywhere (confirmed via full-tree grep). The two approaches serve different call sites: `classify_kiro_v3` backs the dashboard listing (out of scope here); SC-7 needs a fast, round-trip-free "is this session held elsewhere" check for `_acp_availability`/`_lock_holder`, which is what Phase 5 specifically recommended — see Design Decisions.

**The literal lock-file mechanism**: `acp._lock_holder()` (`2116-2172`), called from `_acp_availability()` (`web.py:1795-1825`), which v3 listing already goes through (`_acp_listing_v3`, `web.py:2238`). For a `sess_`-prefixed ID this hardcoded-to-`KIRO_SESSION_DIR` path never matches → caught `FileNotFoundError` → returns `None` → fails open to `"available"`. Not a crash risk — a v3 session actively held elsewhere just shows as falsely "available" today.

**`_session/steer` works functionally on v3** (live-confirmed: the probed session actually followed the steered instruction). The echo is the three-state `session_info_update` sequence above (SC-3), not v2's `AgentExecutionSteeringInjected` kind (which has no v3 equivalent — `_kiro.dev/*` isn't part of v3's namespace).

**`commands_options`/`commands_execute` are confirmed broken**: live probe returned the identical `-32603 "[PersistenceClassification]... has no persistence classification"` error the spike found for `session/terminate`. `acp.html:2152` confirmed to call exactly this path when a user selects any palette entry — the palette populates correctly (confirmed live) but selecting anything silently errors today.

**`session/request_permission`, confirmed live, is a real inbound request** (has an `id`, blocks the turn on a response) — arrived during a spec-mode probe as the agent asked a genuine multiple-choice clarifying question. PowerAtlas's `_on_agent_request` (`4713-4718` v3 override, falls through to base `3215-3226` for anything but `getAccessToken`) has no handler — the probe session hung until killed. Not reached from `kiro_default` mode in this session's testing (several different live prompts never triggered it), but structurally possible from any built-in tool that asks a clarifying question in any mode.

**Test coverage gap, precisely bounded**: `TestSupervisorV3` (`tests/test_web.py:19808-20319`, 13 tests) covers only `_get_tool_diffs_v3`, `_stored_session_cwd_v3`, `_fulfill_token`, and `_publish_live` union — zero tests touch `new_session`, `load_session`, `close_session`, `_on_notification`, `_emit_v3`, `steer`, or any of the 9 `_handle_*_v3` functions (217 references to v2 `_handle_*` functions in test_web.py, 0 to any v3 counterpart).

## 2) Goal

Close every confirmed v3 gap (data-loss race, broken delete, dropped notification kind, mis-wired shared helpers, non-functional crew panel and slash commands, unhandled permission requests) with `_SupervisorV3` kept structurally parallel to `_Supervisor`, verified via pytest and standalone protocol probes throughout, with exactly one user-approved restart reserved for final live verification.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| SC-1 fix scope | v3-only: a new keyed pending-frame buffer on `_SupervisorV3`, flushed inside `new_session()` right after `self.history[session_id]` is created | Engine-agnostic fix to the shared `record()`/`new_session` idiom | The race is only *observed* on v3 (v2 doesn't fire an early unsolicited tool call); touching shared base-class code risks v2 regression for a benefit v2 doesn't need. Lower blast radius, matches "keep parallel" decision. |
| SC-1 mechanism | `self._pending_early_frames: dict[str, list[dict]] = {}` (session_id → ordered raw notification dicts, capped at `_MAX_PENDING_EARLY_FRAMES = 50` entries per session — a `fetch_cloud_config`-style burst is a handful of frames; 50 is generous headroom, and a session hitting the cap drops the oldest buffered frame with a `log.warning`, never raises). In `_SupervisorV3._on_notification`, when `session_id not in self.sessions and self._reserved > 0` (identical guard shape to the existing `_pending_commands` check at `4642`), append the raw `msg` to `self._pending_early_frames.setdefault(session_id, [])` (bounded per above) and return *before* any `record()`/`_emit_v3` call. In `new_session()`, immediately after `self.history[session_id] = _History()` (mirroring `_flush_pending_commands`'s call site), pop `self._pending_early_frames.pop(session_id, [])` and replay — see the separate "SC-1 replay isolation" row below for why this is NOT a direct in-line call. | A single-slot buffer like `_pending_commands`; re-ordering `new_session()` to register earlier; an unbounded buffer | `tool_call` payloads are keyed and not interchangeable (unlike the commands catalogue), so keyed-by-session-id is required. The `self._reserved > 0` guard is a global (not per-session) in-flight counter — review findings (Architect, Senior engineer) confirmed a notification for an unrelated/never-registering session_id can be buffered while *any* other `new_session`/`load_session` call is in flight, so the cap plus a sweep (next row) is required, not optional. |
| SC-1 orphan-buffer sweep | `_sweep_once` (`acp.py:7372+`) gains a v3-only pass: any `_pending_early_frames` entry older than `PROMPT_SILENCE_SECONDS`-equivalent (reuse the existing idle-sweep cadence, not a new timer) whose `session_id` never appeared in `self.sessions` is dropped with a `log.warning`. Track entry age via a parallel `self._pending_early_frames_at: dict[str, float]` (monotonic timestamp of first buffer for that key). | No sweep (accept the leak as bounded-but-permanent per session_id) | A buffered entry for a session_id that never completes `new_session()` (RPC failure, or the id belongs to a different in-flight reservation entirely) would otherwise never be cleaned up — review finding (Reliability engineer): the existing `new_session()` failure-path cleanup only fires for the *matching* session's own rollback, not for a session_id that was never the subject of a `new_session()` call in this process at all. |
| SC-1 replay isolation | The replay loop in `new_session()` runs in its own `try/except Exception: log.warning(...); continue` **per buffered frame**, and — critically — executes *after* the method's existing rollback-guarded `try` block has already committed (i.e., after the point where a raised exception would trigger `self.sessions.pop(...)`/`self.history.pop(...)` rollback), not inside it. | Replay inline inside the existing rollback-guarded section (original Phase 2 draft) | Review finding (Reliability engineer, Architect, Senior engineer — independently, 3 personas): replaying inside the rollback block means one bad buffered frame raising inside `_on_notification` would roll back and discard a session that KAS had *already successfully created* — turning the original bug (one frame silently dropped) into a strictly worse one (the whole session orphaned: real KAS-side state with no local PowerAtlas record and no way to close it). Isolating replay after the commit point, with its own per-frame catch, means a bad buffered frame is logged and skipped, never taken as grounds to discard a real session. |
| SC-7 approach | Implement the file-status-field approach fresh (`session.json.status`), scoped to `_acp_availability`/`_lock_holder`'s "is this held elsewhere" question — a new `_lock_holder_v3`-shaped helper. Leave `classify_kiro_v3` (dashboard listing, different call site) untouched. | Extend `classify_kiro_v3`'s existing transcript-tail heuristic to also answer the availability question | Different jobs: `classify_kiro_v3` answers "what is this session doing" (WORKING/WAITING/ERRORED) from `messages.jsonl`; SC-7 needs "is another process holding this session" from `session.json.status`, zero-round-trip, matching the spike's own Phase 5 conclusion. Conflating them risks regressing the already-working dashboard listing. |
| SC-8 method | **Pending Phase 0.** No confirmed v3-native replacement for `commands_options`/`commands_execute` exists yet — Phase 0 probes candidate methods (see Phase 0) before Phase 7 implements the fix. | — | Genuinely unresolved; guessing risks another `-32603` round-trip discovered only in production. |
| SC-9 reply wire shape | **Pending Phase 0.** The exact JSON-RPC reply shape that unblocks a hung turn after `session/request_permission` is unconfirmed — the exploration's probe session was killed before ever answering the request. Phase 0 (expanded — see Phase 0) probes this alongside SC-8, before Phase 6 implements the handler. | Implement Phase 6 against a guessed shape, discover the real one only via Phase 8's live check | Review finding (Architect, Senior engineer): SC-8 already got a dedicated pre-implementation probe for exactly this class of unknown; SC-9's reply shape is the same class of unknown and deserves the same discipline, not a "TBD" left inside the implementation phase itself. |
| SC-9 UI shape & pending-request tracking | New WS frame types `permission_request` (server→client: `requestId`, `sessionId`, question/options) and `permission_response` (client→server: `requestId`, chosen `optionId`). Server holds pending state in `self._pending_permission: dict[str, dict] = {}` keyed by the request's JSON-RPC `id`, value `{"session_id": ..., "options": [...]}` — a plain dict, **not** an `asyncio.Future`; the JSON-RPC reply is written directly and synchronously from `_handle_permission_response_v3` once the client answers (no separate `_fulfill_permission` coroutine waiting on the dict — that was an earlier draft's inconsistency between this row and the Phase 6 Changes section, resolved here in favor of the simpler direct-write shape, since nothing needs to *await* the answer server-side). `_handle_permission_response_v3` MUST verify `conn.session_id == stored["session_id"]` before honoring an answer (mirroring `_handle_steer_v3`'s existing `conn.session_id != session_id → "not_subscribed"` pattern, `acp.py:6974`) — without this, any connected WS client could resolve any pending permission request by guessing/observing its `requestId` (JSON-RPC ids are small sequential integers on this wire). The pending entry is popped (or marked answered) **synchronously, before** the reply-write `await`, and a second response for an already-answered `requestId` is rejected — closes the double-answer race a client-side button-disable alone can't prevent. | `asyncio.Future`-based mechanism (original draft); no session-ownership check; pop-after-write ordering | Review findings (Security auditor: ownership check, High; Reliability engineer: mechanism inconsistency, Medium; Security auditor: atomicity, Medium) — all three are closed by this single specification. User decision (Q6, prior turn) on real-interactive-UI-vs-alternatives stands unchanged; this row only fixes the *mechanism*, not the UX choice. |
| SC-9 cleanup trigger | Clean up `self._pending_permission` for a session unconditionally in `_handle_prompt_v3`'s own `finally` block (turn end) — mirroring how `crew_spawn_anchors` cleanup already happens there — **not** only on explicit `close_session`/`cancel`. | Clean up only on session close/cancel (original draft) | Review finding (Reliability engineer, High): explicit `close_session` is normally refused while a turn is inflight (the sweeper's own inflight-exclusion condition), and a pending permission request only ever exists mid-turn — so "on close" is a largely unreachable trigger in practice. Turn-end `finally` is the path that actually always fires, matching how the existing crew-anchor cleanup already handles the identical shape of problem. |
| Presence.py fix (SC-6) | `_scan()` keeps its existing binary-name match (identifying "this is a kiro-cli process" is unaffected), then **before** committing to the matched provider label, extracts the session ID using the matched spec's own flag (reusing `_extract_session_id`, which both `"kiro-cli"` and `"kiro-cli-v3"` specs already point at the same `--resume-id` flag for — confirmed coincidence, called out explicitly here rather than left implicit) and reroutes the label to `"kiro-cli-v3"` when that ID matches `launcher._SESSION_ID_RE`'s `sess_`-prefixed shape. | Reorder `_PROVIDER_SPECS` dict (put `"kiro-cli-v3"` first); extract session ID before any provider match | Reordering the dict doesn't work — `_match_provider()` matches on binary name, and both entries share the same binary names (`70-74`), so dict order is irrelevant. Extracting the ID *before* any match doesn't work either, since `_extract_session_id` itself needs a matched spec's flag name as an input — the two steps are sequentially dependent (match → extract-with-that-spec's-flag → reroute-if-v3-shaped), not reorderable. Review finding (Senior engineer): the original draft's "extract before match" description didn't match `_scan()`'s actual call order. |
| `_sidecar_records()` fix (SC-6, extended scope) | `_sidecar_records()` (`presence.py`) — which independently hardcodes `provider="kiro-cli"` for every lock file it reads, regardless of the session ID inside — gets the same `sess_`-prefix reroute as `_match_provider`, in the same phase. | Leave `_sidecar_records()` untouched (original draft scoped SC-6 to `_match_provider` only) | Review finding (Senior engineer, Medium): `_sidecar_records()` is a second, independent hardcoded-`"kiro-cli"` site that the original fix missed — post-fix, the two-pass reconciliation between live-process attribution and sidecar-file attribution would disagree for every v3 session, which is worse than the pre-fix uniform mislabeling. |

## 4) External Dependencies & Costs

### Required external changes

None — no new infrastructure, cloud resources, CI/CD, or third-party services. All work is local code + a local kiro-cli v3 process the app already spawns.

### Cost impact

None.

## 5) Implementation Phases

### Phase 0: Protocol probes — SC-8's command-execution method + SC-9's permission-reply shape

**Goal**: Determine (a) the correct v3-native mechanism for slash-command execution (replacing the broken `_kiro.dev/commands/execute`), and (b) the exact JSON-RPC reply shape that unblocks a turn after answering `session/request_permission`. Standalone probes, no PowerAtlas involvement, no restart.

**Why horizontal**: Establishes empirical premises that Phase 6 (reply shape) and Phase 7 (command method) depend on. Mirrors the original spike's own Phase 0 methodology, and closes a review finding (Architect, Senior engineer, High) that SC-9's reply shape was left "TBD" inside its implementation phase with no dedicated probe, unlike SC-8.

**File scope**: no production files. Throwaway probe script(s) only (deleted after use, `git status` confirmed clean) — same technique used during exploration (spawn `kiro-cli acp --agent-engine v3` directly via subprocess, complete the auth handshake, `session/new`, then experiment).

**Probes to run**:

*SC-8 (command execution)*:
1. Inspect `initialize`'s `agentCapabilities._meta.kiro.extensionMethods` list (already partially captured during exploration — re-capture in full) for any `_kiro/*` or bare `session/*` method plausibly related to command execution (candidates seen so far: none named explicitly, but the list is long and wasn't fully enumerated).
2. Try sending a slash-command's underlying action as a normal `session/prompt` with the command's `_meta.kiro.contextQuery` value inserted as text (the `available_commands_update` notification's shape, captured during exploration, suggests commands might just be steering-document context queries dispatched as prompts, not a separate RPC at all).
3. If neither resolves it, try each `_kiro/*` candidate from the `extensionMethods` list that wasn't already ruled out.

*SC-9 (permission reply shape)*:
4. Reach a `session/request_permission` state again (spec mode, per the exploration's technique — modeId `"spec"` on a throwaway session), and this time **answer it**: try `{"result": {"optionId": "<chosen>"}}` first (the shape the exploration's Design Decision guessed); if the turn doesn't resume, try `{"result": {"selectedOptionId": "<chosen>"}}` and `{"result": "<chosen optionId as bare string>"}` as fallback candidates. Confirm success by observing the turn actually resumes (an `agent_message_chunk` or `turn_end` follows).

**Exit criteria**:
- [ ] Either a working v3 command-execution mechanism is found and documented here with its exact request shape, OR the probe conclusively shows no RPC-level mechanism exists and command execution must be synthesized client-side — either outcome unblocks Phase 7.
- [ ] The exact `session/request_permission` reply shape that resumes a hung turn is confirmed and documented here with a wire-log excerpt — unblocks Phase 6.
- [ ] Probe script(s) deleted; `git status` confirms no tracked file changes.

**Covers**: SC-8 (partial — investigation only, Phase 7 implements), SC-9 (partial — investigation only, Phase 6 implements)

### Phase 1: Liveness detection + presence attribution + log-level fix + baseline handler coverage [QA]

**Goal**: Land the lowest-risk, most self-contained fixes first — presence.py provider attribution, production liveness detection, and the notification-log-level bump — establishing a clean baseline before touching `_on_notification`'s core dispatch logic. Also close out SC-11's coverage for the four `_handle_*_v3` functions no other phase touches.

**File scope**: `src/power_atlas/presence.py`, `src/power_atlas/acp.py`, `src/power_atlas/web.py` (the `_acp_availability` call site)

**Changes**:

1. **SC-6** — per the Design Decisions table's "Presence.py fix" and "`_sidecar_records()` fix" rows: reroute `_match_provider`'s output to `"kiro-cli-v3"` for `sess_`-shaped session IDs (post-match, using the matched spec's own `--resume-id` flag to extract the ID), and apply the identical reroute inside `_sidecar_records()` for lock-file-derived records. Fix the stale comment at `presence.py:66-69` (currently cites "Follow-up Work item #2" for this disambiguation — that numbering belongs to a different plan; update it to cite this plan's Phase 1, or remove the stale cross-reference entirely).

2. **SC-7** — per the Design Decisions table's "SC-7 approach" row: add `_lock_holder_v3(session_id: str) -> int | None` (return the holding PID, or `None` if not held) reading `session.json`'s `status` field directly (`"in_progress"`/`"waiting_on_user"` → held; `"idle"`/`"failed"`/absent → not held), zero ACP round-trip, validated against `launcher._SESSION_ID_RE` before any path join (matching the existing `_stored_session_cwd_v3`/`_get_tool_diffs_v3` guard pattern). Wire it into `_acp_availability()` (`web.py:1795-1825`) as a `sess_`-prefixed branch parallel to the existing `_lock_holder` call, so v3 listing (`_acp_listing_v3`, already calling `_acp_availability`) gets a real availability signal instead of the current fail-open "always available."

3. **SC-10** — in both `_Supervisor._on_notification` (`acp.py:4036-4044`) and `_SupervisorV3._on_notification` (`4651-4653`), change the unrecognized-kind fallback from `log.debug(...)` to `log.info(...)`.

4. **SC-11 baseline coverage** — add direct unit tests for `_SupervisorV3.load_session`, `_handle_subscribe_v3`, `_handle_cancel_v3`, and `_handle_close_v3` (the 4 of 9 `_handle_*_v3` functions no other phase's fix touches) — characterization tests confirming current behavior, since nothing here is being changed, just covered. Mirror the existing `TestSupervisorV3` test shape (`tests/test_web.py:19808-20319`).

**Exit criteria**:
- [ ] A resumed v3 session (session ID `sess_`-prefixed) is attributed to `"kiro-cli-v3"` in `presence.py`'s snapshot, verified by a new unit test constructing a fake process record with a v3 session ID.
- [ ] A resumed v2 session continues to attribute to `"kiro-cli"` (regression test).
- [ ] `_sidecar_records()` attributes a v3-shaped lock file to `"kiro-cli-v3"`; a v2-shaped one continues attributing to `"kiro-cli"` (regression test).
- [ ] `_lock_holder_v3` correctly reports held/not-held against a synthetic `session.json` for each `status` value; rejects a malformed session_id via `_SESSION_ID_RE` before any path join (mirroring the existing `_stored_session_cwd_v3` path-traversal test pattern).
- [ ] `_acp_availability()` reports a held v3 session as unavailable (previously always "available").
- [ ] `_on_notification`'s unrecognized-kind fallback is visible at INFO level (verify via a unit test capturing log output for a synthetic unknown-kind notification, both classes).
- [ ] New direct tests exist for `load_session`, `_handle_subscribe_v3`, `_handle_cancel_v3`, `_handle_close_v3` on `_SupervisorV3`.
- [ ] New/updated tests added to `tests/test_data.py` (presence), `tests/test_web.py` (liveness, log level, handler coverage).
- [ ] `.venv-PowerAtlas\Scripts\pytest` passes.

**Covers**: SC-6, SC-7, SC-10, SC-11 (partial — 4 of 9 handlers)

### Phase 2: Notification-drop race fix [QA] [P:5]

**Goal**: Implement the SC-1 keyed early-frame buffer per the Design Decisions table.

**File scope**: `src/power_atlas/acp.py` (`_SupervisorV3` only)

**Changes**:

1. Add `self._pending_early_frames: dict[str, list[dict]] = {}` and `self._pending_early_frames_at: dict[str, float] = {}` to `_SupervisorV3.__init__` — but note `_SupervisorV3` has no `__init__` override today (confirmed: inherits `_Supervisor.__init__` unchanged). Add a minimal `__init__` override that calls `super().__init__()` then sets these two new fields — do not otherwise duplicate the base `__init__` body. Add a module-level `_MAX_PENDING_EARLY_FRAMES = 50` constant near the other v3 constants.

2. In `_SupervisorV3._on_notification` (`acp.py:4466-4653`), immediately after extracting `session_id` (`4476`) and before the existing branch chain, add:
   ```python
   if isinstance(session_id, str) and session_id not in self.sessions and self._reserved > 0:
       buf = self._pending_early_frames.setdefault(session_id, [])
       self._pending_early_frames_at.setdefault(session_id, time.monotonic())
       if len(buf) >= _MAX_PENDING_EARLY_FRAMES:
           log.warning("ACP v3: pending-early-frame buffer full for %s, dropping oldest", session_id)
           buf.pop(0)
       buf.append(msg)
       return
   ```
   Place this *after* `self._stamp_activity(session_id)` (preserve existing activity-stamping for all frames, including buffered ones) but before every other branch.

3. In `_SupervisorV3.new_session()` (`acp.py:4788-4829`), the buffered-frame replay does **not** go inside the method's existing rollback-guarded `try` block (per the Design Decisions "SC-1 replay isolation" row — putting it there risks discarding an already-successfully-created KAS session over one bad buffered frame). Structure the method so replay happens *after* the point where `self.sessions[session_id]`/`self.history[session_id]` are durably committed and the rollback-triggering `try` has already exited successfully:
   ```python
   _buffered = self._pending_early_frames.pop(session_id, [])
   self._pending_early_frames_at.pop(session_id, None)
   for _buffered_msg in _buffered:
       try:
           self._on_notification(_buffered_msg)
       except Exception:
           log.warning("ACP v3: replay of buffered frame failed for %s, skipping", session_id, exc_info=True)
   ```
   Re-dispatching through `_on_notification` itself (not duplicating its logic) means a successfully-replayed frame gets identical handling — including the `tool_call`/`crew_spawn_anchors`/timing-stamp logic already inside that method — to a normally-timed frame; the per-frame `try/except` means a *failed* replay is logged and skipped, never grounds to roll back the session.

4. Add cleanup: on any `new_session()` failure path that already pops `self.sessions`/`self.history` (existing rollback logic per the spike's F9 fix, `4817-4822`), also pop `self._pending_early_frames`/`self._pending_early_frames_at` for `session_id` to avoid a leaked buffer entry.

5. Extend `_sweep_once`'s v3 pass (`acp.py:7372+`) per the Design Decisions "SC-1 orphan-buffer sweep" row: any `_pending_early_frames` entry whose recorded `_pending_early_frames_at[session_id]` is older than the sweep's existing idle threshold, and whose `session_id` never appeared in `self.sessions`, is dropped with a `log.warning` and removed from both dicts. This is the only way to reclaim a buffer entry for a session_id that never completes `new_session()` at all (RPC failure, or the id belonged to a different in-flight reservation that never registers this particular id).

6. Clean up the stale class-level docstring comment at `acp.py:4454` (*"`_on_notification` deferred to Phase 2 (requires `_emit_v3`...)"*) — this refers to the archived spike's own Phase 2, which shipped long ago; the sentence is now not just stale-numbered but factually describing history, not a pending item. Remove or rewrite it to state the current fact plainly (the override exists and is fully implemented) without a "Phase N" reference that could be misread as pointing at this plan's own Phase 2.

**Exit criteria**:
- [ ] New test: construct a `_SupervisorV3`, simulate a notification arriving for a `session_id` not yet in `self.sessions` while `self._reserved > 0`, confirm it's buffered (not dropped, not recorded prematurely).
- [ ] New test: complete `new_session()` for that same `session_id`, confirm the buffered frame is replayed and lands in `self.history[session_id]` with correct content (e.g., a buffered `tool_call` with `title="Fetching your cloud config"` shows up correctly titled, not as the generic "tool call" fallback the exploration observed live).
- [ ] New test: a notification for a session_id that's genuinely unknown (not pending, `self._reserved == 0`) is still silently dropped as before (regression — the fix must not change behavior for a truly-unrelated stray frame).
- [ ] New test: buffering past `_MAX_PENDING_EARLY_FRAMES` drops the oldest entry, never raises, and the buffer never exceeds the cap.
- [ ] New test: a buffered frame that raises inside `_on_notification` during replay is logged and skipped, and the session is **not** rolled back (construct a buffered frame engineered to raise, confirm `new_session()` still returns successfully and `self.sessions[session_id]` remains present).
- [ ] New test: a `_pending_early_frames` entry for a session_id that never calls `new_session()` is evicted by the `_sweep_once` v3 pass after the idle threshold elapses.
- [ ] Existing `TestSupervisorV3` tests still pass (no signature changes to public methods).
- [ ] `.venv-PowerAtlas\Scripts\pytest` passes.

**Covers**: SC-1

### Phase 3: `session_info_update` dispatch branch [QA]

**Goal**: Add the missing branch to `_SupervisorV3._on_notification`, surfacing context %, steering echo, title, and MCP-auth-required signals.

**File scope**: `src/power_atlas/acp.py` (`_SupervisorV3._on_notification` only — this is v3-specific; v2 never emits `session_info_update`, so no base-class change)

**Changes**:

1. In `_SupervisorV3._on_notification`, add a new branch (after the existing `available_commands_update` branch, before the method-based branches) matching `kind == "session_info_update"`. Dispatch on `_meta.kiro.kind`:
   - `"context_usage"` → extract `_meta.kiro.contextUsage.usagePercentage`, call a new `_note_context_v3(session_id, percent)` (a v3-scoped **sibling** function, reading `self.sessions` — i.e. `_supervisor_v3.sessions` — not the v2 singleton. Deliberately a new function, not a parameterization of `_note_context` the way Phase 4 parameterizes its three helpers: `_note_context`'s call site is tied to `METADATA_METHOD`, a method v3 never sends at all, so there's no shared call site to parameterize — v2 and v3 reach "note the context percentage" via structurally different wire mechanisms, not the same code path with a different target. Review finding (Senior engineer, Medium) raised this as a possible inconsistency; noted here explicitly as an intentional one.).
   - `"steering_queued"`/`"steering_injected"`/`"steering_cleared"` → `_emit_v3` a `steer_status` frame (new frame type) carrying `{status: <kind>, messageId, content}` for the client to render as transient feedback.
   - `"focus_update"` → `_emit_v3` a `title` frame (check whether an existing `title` frame type already exists for v2's title-update path — reuse if so, add if not) carrying the new session title.
   - `"display_error"` → `_emit_v3` an `agent_error` frame (or reuse an existing error-display frame type if one exists — check `acp.html`'s frame handler for precedent) carrying `{message, errorType}`.
   - `"user_message_id_assigned"`, `"turn_end"`, `"pendingInteraction"` → no-op (turn-end already handled via RPC result per Current State; `pendingInteraction` is a preview of the `session/request_permission` request Phase 6 handles directly, redundant to act on twice).
   - Any other `_meta.kiro.kind` value → falls through to the SC-10 fallback (now visible at INFO).

2. Client-side (`acp.html`): add handlers for the new/reused frame types in the WS `handle()` dispatcher (`~6525`+) — render `steer_status` as transient text near the composer (e.g., "Steering..." → "Injected" → cleared after a few seconds), `title` updates the tab/header title (check for existing v2 title-handling code to reuse), `agent_error` renders inline in the transcript similar to a failed tool call. **All agent-supplied strings in these new render paths (steering `content`, `title`, `display_error.message`) MUST be set via `.textContent`/`createElement`, never `innerHTML`** — matching the codebase's existing convention elsewhere, called out explicitly here because Phase 3 introduces the first new agent-controlled text-rendering surface this plan adds (review finding, Security auditor, Low).

3. Add a context-percentage UI element check: confirm `acpContext`/`acpContextFill`/`acpContextLabel` (`acp.html` IDs, confirmed present in the shared template) are updated by whatever frame type context_usage maps to — reuse the existing v2 `meta`/context-update frame type if its shape is compatible, to avoid a third code path for the same UI element.

4. **Register every new frame type** (`steer_status`, and `title`/`agent_error` if not reusing an existing v2 type) in both `CLIENT_TYPES`/`SERVER_TYPES` (`acp.py:162-176`) — `envelope()` refuses any type not in `SERVER_TYPES` (`acp.py:966`), so a new frame type left unregistered is silently rejected before reaching any of the rendering logic above (review finding, Security auditor + Senior engineer, Medium).

**Exit criteria**:
- [ ] New test: a `session_info_update` frame with `kind: "context_usage"` updates `_supervisor_v3.sessions[sid]["contextPercent"]` (or equivalent field) correctly.
- [ ] New test: a `steering_queued`→`steering_injected`→`steering_cleared` sequence produces three distinct emitted frames in order.
- [ ] New test: a `display_error` frame with `errorType: "mcp_connection_error"` emits a frame containing the message text.
- [ ] `node tests/acp_page.test.mjs` passes with new checks for the client-side frame handlers (steer_status, title/context update, agent_error rendering).
- [ ] `.venv-PowerAtlas\Scripts\pytest` passes.

**Covers**: SC-3

### Phase 4: Engine-aware shared helpers + live crew panel [QA]

**Goal**: Fix the three helpers hardcoded to `_supervisor`, and wire live (mid-turn) crew panel updates for v3 using the confirmed `agent-subtask` wire shape.

**File scope**: `src/power_atlas/acp.py` only (no `acp.html` changes needed — `setCrew()` confirmed engine-agnostic)

**Changes**:

1. **`_flush_bubble` (SC-4)** — `acp.py:5069-5108`. Add an `emit_fn` parameter (defaulting to `_emit` for v2 call sites) so `_SupervisorV3`'s call sites (`6794`, `6907`, `6950`, plus the `_on_notification` call sites at `4510`/`4528`/`4578`/`4585`) pass `_emit_v3` explicitly. Alternative: have `_flush_bubble` look up the correct emitter by checking `session_id in (_supervisor_v3.sessions if _supervisor_v3 else {})` — reject this per the spike's own "Rejected: Option B (look up supervisor in `_emit`) — couples a shared utility to both supervisor singletons" precedent (Phase 2 of the spike); prefer the explicit-parameter approach for consistency with that established pattern.

2. **`_emit_subagents_frame` (SC-4/SC-5)** — `acp.py:5026-5046`. Same parameterization: accept the supervisor instance (or crews dict + crew_spawn_toolcallids dict) as arguments instead of reading the module-level `_supervisor` directly. Update v2 call sites to pass `_supervisor`'s attributes explicitly; v3 call sites (once added per point 4 below) pass `_supervisor_v3`'s. **Port v2's `fan_out_id` filtering** (present in the current `_emit_subagents_frame` but absent from `_emit_subagents_frame_v3`) into the parameterized version, so a v3 session with multiple sequential or overlapping fan-outs doesn't cross-contaminate one fan-out's panel with another's entries (review finding, Senior engineer, Medium). Once this parameterized version covers everything `_emit_subagents_frame_v3` (`7269-7280`) did, **retire `_emit_subagents_frame_v3`** — its remaining callers (`_handle_prompt_v3`'s `finally`, `6939`; `_evict_crew_children_v3`, `7305`) switch to calling the parameterized `_emit_subagents_frame` with v3 arguments instead, closing the now-redundant duplicate.

3. **`_handle_subagent_subscribe` (SC-4)** — `acp.py:5535-5590`. Same parameterization for `crews`/`subagent_history` — the function already correctly identifies v3 sub-agents via `_supervisor_v3.subagent_sessions` (`6671`) but then reads the wrong dicts; pass the correct dicts through based on which supervisor's `subagent_sessions` matched.

4. **Live crew wiring (SC-5)** — in `_SupervisorV3._on_notification`'s `tool_call`/`tool_call_update` branch, add detection for `_meta.kiro.kind == "agent-subtask"` (parallel to, not replacing, the existing `_meta.kiro.toolName == "subagent"` check which never matches on v3). On detection:
   - Opening `tool_call`: record a new crew entry in `self.crews` (v3 supervisor's own dict) keyed by the parent `session_id`, with role=`rawInput.name`, task=`rawInput.explanation` or `rawInput.prompt`, status=pending, keyed sub-entry by `agentSubtaskId`.
   - `tool_call_update` with matching `agentSubtaskId`: update that sub-entry's status (in_progress/completed/failed) and, on completion, its output (`rawOutput`).
   - `agent_message_chunk` with `_meta.kiro.agentSubtaskId` set: append to that sub-entry's streamed-output buffer (for live partial display, if the UI supports it — otherwise the completing `tool_call_update`'s `rawOutput` alone is sufficient for SC-5's bar).
   - After each update, call the now-parameterized `_emit_subagents_frame` (point 2) — not just at turn-end — so the panel updates live.

5. **Clean up the stale class docstring comments** at `acp.py:4456-4464` (*"Crew panel (`_kiro.dev/subagent/list_update`) compatibility... not probed in Phase 0..."* and *"This override is what Phase 1 deferred..."*) — these are now factually wrong, not just stale-numbered: the exploration's live probe confirmed v3 does **not** use `_kiro.dev/subagent/list_update` at all (it has no `_kiro.dev/*` namespace); crew data arrives via ordinary `tool_call`/`tool_call_update`/`agent_message_chunk` frames tagged `_meta.kiro.kind: "agent-subtask"`. Rewrite these lines to state the confirmed mechanism plainly, removing both the "not probed" hedge (it now is) and the archived spike's "Phase 0"/"Phase 1" references (review finding, doc-impact scan — these sit exactly where this phase's own changes land, and their stale "Phase N" language risks being misread as referring to this plan's own phases).

**Exit criteria**:
- [ ] New test: `_flush_bubble` called in a v3 context records into `_supervisor_v3.history`, not `_supervisor.history`.
- [ ] New test: `_emit_subagents_frame` (parameterized) correctly reads from whichever crews dict is passed, verified for both v2 and v3 inputs.
- [ ] New test: `_handle_subagent_subscribe` for a v3 sub-agent session returns correct `role`/`task`/`turnActive` (not the current blank/always-true bug) and replays its transcript (not empty).
- [ ] New test: a synthetic `tool_call` notification with `_meta.kiro.kind: "agent-subtask"` populates `_supervisor_v3.crews` correctly and triggers a live `subagents` frame broadcast (not deferred to turn-end).
- [ ] Existing v2 crew-panel tests still pass (regression — v2 call sites of all three parameterized functions behave identically).
- [ ] `.venv-PowerAtlas\Scripts\pytest` passes.

**Covers**: SC-4, SC-5

### Phase 5: v3 session deletion [QA] [P:2]

**Goal**: Implement individual and workspace-level v3 session deletion.

**File scope**: `src/power_atlas/data_kiro_v3.py`, `src/power_atlas/web.py`

**Why parallel-eligible with Phase 2**: touches `data_kiro_v3.py` and `web.py` exclusively — no overlap with Phase 2's `acp.py`-only changes, and no dependency in either direction.

**Changes**:

1. Add `delete_session(session_id: str) -> bool` to `data_kiro_v3.py`. **First line: `if not _SESSION_ID_RE.fullmatch(session_id): return False`** (importing `launcher._SESSION_ID_RE` — this file has no such guard anywhere today; review finding (Security auditor + Senior engineer, High) confirmed the original draft's claim that a guard was "already used elsewhere in this file" to reuse was simply false — `_find_v3_session_path`/`find_session_workspace` join `session_id` into paths with zero validation, so this is a fresh guard to write, not a reuse). Then locate the session's directory via the existing hash-dir scan pattern (reuse `_find_v3_session_path`'s directory-resolution logic, or extract a shared helper if not already exposed), then remove the whole directory tree. Use the same rename-to-staging-then-unlink pattern as v2's `_acp_delete_session` (`web.py:2764-2846`) to survive Windows sharing violations — rename the directory to a staging name, then `shutil.rmtree` it, catching `PermissionError`/`OSError` (winerror 32) the same way. **Also invalidate `data_kiro_v3._session_path_cache.pop(session_id, None)`** after a successful delete, so a stale cached path to now-deleted files can't be served (review finding, Senior engineer, Medium).

2. Update `api_acp_v3_delete_sessions` (`web.py:3169-3255`): remove the `sess_`-prefix rejection guard (`3244-3246`), call the new `data_kiro_v3.delete_session` for each requested v3 ID, following the same held-session check pattern as v2 (`sv3.sessions` membership check, refuse if currently open — matching the existing "PowerAtlas has this session open" UX). **Additionally check `_lock_holder_v3(session_id)` (from Phase 1's SC-7 work) before deleting** — refuse if another process holds the session, closing the gap where v2 has `_lock_holder` protection against external holders but v3 previously had nothing (review finding, Architect, Medium; only fixable now that Phase 1 has landed `_lock_holder_v3`).

3. Fix `_acp_sessions_for_workspace` (`web.py:2918-2944`) to also enumerate v3 sessions for a given workspace `cwd` (scan the v3 hash-dir layout for sessions matching that workspace path, alongside the existing v2 enumeration). **This function is shared by both `api_acp_delete_sessions` (v2, `web.py:3056`) and `api_acp_v3_delete_sessions` (v3, `web.py:3194`) — extending it to return v3 IDs changes what the v2 endpoint's workspace-delete sees too.** Add an explicit `include_v3: bool` parameter (default `False`, so v2's call site is unaffected unless it opts in) rather than changing the function's unconditional output — the v3 call site passes `include_v3=True`. Add a regression test proving v2's workspace-delete output is byte-for-byte unchanged (review finding, Security auditor, High — directly protects this plan's own Invariant 1).

4. **The enumeration fix in point 3 alone does not make workspace-level v3 delete work** — `_acp_delete_many`/`_acp_delete_session` (the functions the workspace-delete path actually calls to perform the deletion, both in `web.py`, both hardcoded to v2's `KIRO_SESSION_DIR`-suffix layout) must also dispatch by ID shape: a `sess_`-prefixed ID routes to the new `data_kiro_v3.delete_session` (point 1); anything else routes to the existing v2 path unchanged. Without this, workspace delete would newly *find* v3 sessions (point 3) but still fail to *delete* them (review finding, Architect + Senior engineer + Reliability engineer — three-way convergence, High: this is the difference between SC-2 actually working and only appearing to).

5. **`.lock` files** (confirmed present on current kiro-cli builds per SC-7's own finding, though their on-disk location relative to the `sess_*/` directory wasn't confirmed during exploration): before implementing, check whether kiro-cli writes the v3 `.lock` file *inside* `sess_<uuid>/` (in which case the whole-directory `shutil.rmtree` in point 1 already covers it, no extra code needed) or as a *sibling* file outside it (in which case `delete_session` needs an explicit extra unlink, mirroring v2's `.lock` handling in `_acp_session_paths`). Confirm via a quick `Get-ChildItem` against a real held v3 session directory and its parent before writing the deletion code, and note the finding here.

**Exit criteria**:
- [ ] New test: deleting a closed v3 session removes its directory tree (`messages.jsonl`, `session.json`, `publish.cursor`, `.lock` per point 5's finding, and `sub-executions/` if present) from disk.
- [ ] New test: deleting a currently-open (held-by-this-process) v3 session is refused with the same UX as v2's held-session refusal.
- [ ] New test: deleting a v3 session held by an *external* process (per `_lock_holder_v3`) is refused (new coverage — v2 already had this via `_lock_holder`, v3 didn't until Phase 1+5 together close it).
- [ ] New test: workspace-level delete against a workspace containing only v3 sessions now finds **and actually deletes** them (regression test for the previously-silent blindness — must assert files are gone from disk, not just that they were "found").
- [ ] New test: workspace-level delete against a mixed v2+v3 workspace, invoked through the **v2** endpoint (`api_acp_delete_sessions`), returns byte-for-byte the same result as before this phase (proves `include_v3` defaulting to `False` actually isolates v2).
- [ ] New test: path-traversal guard in `delete_session` rejects a malformed session_id as its first action, before any path is constructed (mirroring the existing `_stored_session_cwd_v3` test pattern).
- [ ] New test: a locked file inside a v3 session's directory during delete produces a clean "in_use" refusal via the rename-staging fallback, with no partial deletion left behind (mirrors v2's own sharing-violation coverage, previously untested for the directory-based v3 case).
- [ ] `_session_path_cache` no longer returns a path for a deleted session_id.
- [ ] **Verify via pytest/direct HTTP call, not browser automation** — clicking "Delete session" in an automated browser session triggers a native `confirm()` dialog that crashed the Chrome extension during exploration; test via `TestClient` (`starlette.testclient`) posting directly to `/api/acp-v3/sessions/delete`.
- [ ] `.venv-PowerAtlas\Scripts\pytest` passes.

**Covers**: SC-2

### Phase 6: `session/request_permission` interactive UI [QA]

**Goal**: Implement the new request-response interaction pattern per the Design Decisions table.

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/web.py` (WS frame routing, if any new frame types need registration), `src/power_atlas/templates/acp.html`

**Changes** (per the Design Decisions "SC-9 UI shape & pending-request tracking" and "SC-9 cleanup trigger" rows — read those first, this section only adds implementation detail):

1. **Server (`acp.py`)**: extend `_SupervisorV3._on_agent_request` (`4713-4718`) to recognize `session/request_permission` (currently falls through to `super()._on_agent_request`, which refuses). On receipt: `_emit_v3` a new `permission_request` frame to subscribers carrying `{requestId, sessionId, toolCall: {title, ...}, options: [{optionId, name, kind}]}`, and store pending state in `self._pending_permission: dict[str, dict] = {}` (new field, added via the same minimal `__init__` override from Phase 2 — merge into that same override) keyed by the JSON-RPC request `id`, value `{"session_id": session_id, "options": [...]}`.

2. **Server**: add a new WS-inbound frame type `permission_response` (client→server), handled by a new `_handle_permission_response_v3(conn, session_id, payload)` in the `_dispatch_v3` routing table (mirroring the other `_handle_*_v3` functions' registration pattern — takes `session_id` as an explicit parameter like every other v3 handler, not just `(conn, payload)`). Logic, in order: (a) look up the pending entry by `payload["requestId"]`; if absent, refuse (`"unknown_request"`) without side effects; (b) **verify `conn.session_id == entry["session_id"]`**, refuse (`"not_subscribed"`) if not — mirrors `_handle_steer_v3`'s existing ownership check (`acp.py:6974`) and closes a review finding (Security auditor, High: without this, any connected WS client could resolve any pending permission request for a session it never subscribed to, since request ids are small sequential integers); (c) validate `payload["optionId"]` is one of `entry["options"]`, refuse (`"invalid_option"`) if not; (d) **pop the entry from `self._pending_permission` before the write** (not after) — a second `permission_response` for the same now-popped `requestId` hits step (a)'s "unknown_request" refusal, closing the double-answer race (review finding, Security auditor, Medium); (e) write the JSON-RPC reply using the exact shape Phase 0 confirmed.

3. **Server**: clean up `self._pending_permission` for a session unconditionally inside `_handle_prompt_v3`'s existing `finally` block (turn end) — **not** only on session close/cancel (review finding, Reliability engineer, High: explicit close is normally refused while a turn is inflight, and a pending permission request only exists mid-turn, so "clean up on close" is a largely unreachable trigger; turn-end `finally` is the path that actually always fires, mirroring how `crew_spawn_anchors` cleanup is already done there).

4. **Client (`acp.html`)**: add a `permission_request` frame handler rendering the question + options as inline buttons in the transcript (styled similar to an existing choice/confirmation UI element if one exists in the template — check for precedent before inventing new CSS; **use `.textContent`/`createElement` for the agent-supplied question/option text, never `innerHTML`**, per the same hygiene note as Phase 3). Clicking a button sends `permission_response` with the chosen `optionId` and disables the other buttons client-side (a UX nicety — the server-side pop-before-write in point 2(d) is the actual double-answer guard, not this).

5. **Register `permission_request`/`permission_response`** in `CLIENT_TYPES`/`SERVER_TYPES` (`acp.py:162-176`) — same requirement as Phase 3's new frame types (review finding, Security auditor + Senior engineer, Medium).

**Exit criteria**:
- [ ] New test: `_on_agent_request` receiving `session/request_permission` emits a `permission_request` frame with correct shape and stores pending state (`session_id`, options) keyed by request id.
- [ ] New test: `_handle_permission_response_v3` with a valid `optionId` from the owning connection writes the correct JSON-RPC reply (per Phase 0's confirmed shape) and clears pending state.
- [ ] New test: a response from a connection whose `session_id` doesn't match the pending entry's is refused (`"not_subscribed"`) and pending state is untouched.
- [ ] New test: an invalid/unknown `optionId` is rejected without corrupting pending state.
- [ ] New test: two responses to the same `requestId` — the second is refused as `"unknown_request"` (already popped), proving the pop-before-write ordering actually prevents a double-answer.
- [ ] New test: `_pending_permission` is cleared at turn-end (via `_handle_prompt_v3`'s `finally`) even when no explicit close/cancel ever happens.
- [ ] `node tests/acp_page.test.mjs` passes with new checks for the permission-request UI rendering (via `.textContent`, not `innerHTML`) and click-to-respond behavior.
- [ ] Live verification (Phase 8): trigger a real spec-mode permission request via the standalone-probe technique pointed at `/acp-v3` itself (not just the standalone bypass) — or, if `/acp-v3` truly cannot reach spec mode (hardcoded `kiro_default`), verify via a modified standalone probe that exercises the new server-side handler code path directly against the running PowerAtlas instance's `_supervisor_v3`. Document which verification path was used.
- [ ] `.venv-PowerAtlas\Scripts\pytest` passes.

**Covers**: SC-9

### Phase 7: `commands_options`/`commands_execute` v3 replacement [QA]

**Goal**: Implement whatever Phase 0 found as the correct v3 mechanism.

**File scope**: `src/power_atlas/acp.py` (`_SupervisorV3.commands_options`/`commands_execute` overrides, currently inherited unchanged from v2)

**Changes**: Contingent on Phase 0's finding. If a working RPC method was found, add `_SupervisorV3.commands_options`/`.commands_execute` overrides calling it. If Phase 0 concluded no RPC-level mechanism exists and commands must be synthesized client-side, implement that instead (e.g., `_handle_commands_execute_v3` translates the command selection into a `session/prompt` call using the command's `_meta.kiro.contextQuery`, never reaching the broken RPC methods at all) — update `acp.html:2152`'s call site accordingly if the client-side contract changes.

**Exit criteria**:
- [ ] Selecting a slash command from the `/acp-v3` palette (live verification, Phase 8) produces the expected effect (steering document inserted / command executed) instead of a silent `-32603` error.
- [ ] New test(s) covering whichever mechanism was implemented, mirroring the existing `TestSupervisorV3` test shape.
- [ ] `.venv-PowerAtlas\Scripts\pytest` passes.

**Covers**: SC-8

### Phase 8: Full verification — restart + live browser check

**Goal**: One user-approved PowerAtlas restart; verify every SC live, confirm `/acp` v2 is unaffected, and document `/acp-v3` in README.md now that it's no longer a throwaway prototype.

**File scope**: `README.md` (write); everything else is verification only.

**Changes**:

0. **Add a `/acp-v3` section to README.md**, alongside the existing `/acp` (v2) documentation (crew panel, steer, delete, status dots, diff rendering — all at `README.md:232-306` per the doc-impact scan). `AGENTS.md:11`'s throwaway-prototype exemption for README updates explicitly ends "the moment the surface is kept; promoting it to product is what makes the README row required work" — this plan's own stated goal (bringing `/acp-v3` to production quality, a genuine v2-parity alternative) is exactly that promotion event, so this is a governance-required doc update, not an optional one (review finding, doc-impact scan). Cover: it's a separate route (not a toggle on `/acp`), it's `kiro_default`-mode-only today (no mode picker), and note the interactive permission-request UI (SC-9) as a v3-only feature v2 doesn't have.

1. Run full `pytest` + `node tests/acp_page.test.mjs` one final time (no restart needed for this step).
2. **Present the restart need to the user explicitly and wait for approval** — do not restart PowerAtlas autonomously (`AGENTS.md:5`).
3. After restart, live-verify via browser (Chrome, `claude-in-chrome` MCP tools, using `window.location.href` navigation per the exploration's finding about the `navigate` tool triggering a 403):
   - `/acp-v3`: create a session in the scratch `acp-cwd` folder, confirm the `fetch_cloud_config` auto-call now shows its correct title (SC-1 live check), confirm context % populates (SC-3), send a prompt, confirm normal tool-call rendering unaffected (regression), reload and confirm replay unaffected (regression).
   - `/acp-v3`: select a slash command from the palette, confirm it now works (SC-8 live check).
   - `/acp-v3`: trigger a real subagent fan-out (a prompt asking for genuinely parallel work), confirm the crew panel updates live, mid-turn, not just after the turn ends (SC-5 live check — this is now empirically known reachable in `kiro_default` mode, unlike SC-9).
   - `/acp-v3`: close a session, then delete it via the UI **only if the confirm() dialog risk has been mitigated** (e.g., test via a non-automated manual click by the user, or skip UI-click verification entirely and rely on Phase 5's pytest/API coverage — do not risk another Chrome-extension crash for this check).
   - `/acp-v3`: attempt to trigger `session/request_permission` live if reachable (SC-9); otherwise confirm via the alternate verification path documented in Phase 6.
   - `/acp`: full regression pass — create, prompt, reload, close, delete (via API/manual click, same caution as above) — confirm byte-for-byte unaffected behavior.
4. Clean up all test sessions/files created during this verification pass (per established discipline this session — delete on-disk session files and scratch test files after use).

**Exit criteria**:
- [ ] `README.md` documents `/acp-v3` per point 0.
- [ ] `.venv-PowerAtlas\Scripts\pytest` passes (full suite).
- [ ] `node tests/acp_page.test.mjs` passes (full suite).
- [ ] Every live-verification bullet above checked off, with results recorded in this plan's Implementation Divergences section if anything differs from expectation.
- [ ] `/acp` v2 confirmed unaffected across every action tested.
- [ ] All test artifacts (sessions, files) cleaned up; `git status` clean.

**Covers**: SC-12

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| R1 — Shared-helper fixes (Phase 4) touch code v2 also calls | Medium — a parameterization mistake could silently break v2's crew panel or bubble rendering | Phase 4 exit criteria require explicit v2-regression tests for all three parameterized functions, not just v3 behavior checks. Addressed within this plan. |
| R2 — SC-1's fix touches `new_session()`, a core session-creation path | Medium — a bug in the buffer/replay logic could cause double-recording or a hang | Scoped to `_SupervisorV3` only (Design Decision), re-dispatches through the real `_on_notification` rather than duplicating logic, extensive unit test coverage in Phase 2. Addressed within this plan. |
| R3 — SC-8's correct v3 replacement is unknown until Phase 0 | Medium — Phase 7 could be blocked or need redesign if Phase 0 finds no viable mechanism | Phase 0 explicitly probes multiple candidates before Phase 7 starts; if no RPC mechanism exists, client-side synthesis is the documented fallback. Addressed within this plan. |
| R4 — SC-9's interactive-UI design is entirely new surface (new WS frame type both directions, new pending-request state, new UI component) | Medium — largest net-new surface in this plan | Not reachable in production `kiro_default` usage today, so it can be built and tested via the standalone-probe technique without needing the live PowerAtlas instance for initial verification — only final integration needs the Phase 8 restart. Addressed within this plan. |
| R5 — Every fix requires a user-timed restart to verify live, and `AGENTS.md:5` forbids requesting one autonomously mid-plan | Low (process risk, not correctness risk) | All phases batch into Phase 0-7 (no restart needed — pytest + standalone probes only), with exactly one restart reserved for Phase 8. Addressed within this plan. |
| R6 — Browser-based testing of session deletion is unsafe via automation (confirmed Chrome-extension crash this session) | Low | Phase 5 verifies via pytest/direct HTTP calls, never via automated UI clicks; Phase 8 defers any UI-click check to manual user action or skips it. Addressed within this plan. |
| R7 — Deleting a v3 session directory tree that includes `sub-executions/` needs recursive deletion, not v2's fixed-suffix file list | Low | Phase 5 explicitly specifies `shutil.rmtree`-equivalent whole-directory deletion, with a test covering a session that has `sub-executions/`. Addressed within this plan. |
| R8 — SC-3/SC-6's exact new frame-type shapes may collide with or duplicate existing v2 frame types the client already handles (e.g., a "title" or "context" frame may already exist) | Low | Phase 3's changes explicitly instruct checking `acp.html`'s existing frame handlers for reusable precedent before adding new frame types — implementer must verify before inventing. Addressed within this plan. |
| R9 — Review cycle (4 personas) found 8 High-severity gaps in the original phase drafts: SC-7/SC-11 with no covering phase, Phase 5's delete not actually reaching v3 sessions via the workspace path, a false guard-reuse citation, Phase 2's replay-in-rollback-block risk, Phase 6's missing ownership check, and its unreachable cleanup trigger | High (pre-fix) | All 8 fixed directly in this revision — see the Design Decisions rows and Phase 1/2/5/6 changes marked "review finding." Addressed within this plan. |
| R10 — `_pending_early_frames` (SC-1) could grow unbounded under a chatty/misbehaving KAS process, or leak permanently for a session_id that never completes `new_session()` | Medium | Phase 2 adds a per-session cap (`_MAX_PENDING_EARLY_FRAMES`) and a `_sweep_once`-integrated eviction pass for orphaned entries. Addressed within this plan. |
| R11 — `_pending_permission` (SC-9) has no expiry; an abandoned/disconnected client leaves a KAS turn blocked indefinitely on a question nobody will ever answer | Low | Not fixed in this revision — genuinely a product-shape decision (auto-refuse after N minutes? default to a specific option? just rely on `PROMPT_SILENCE_SECONDS`'s existing ~30-minute backstop?) rather than a deterministic bug fix. Deferred — see Follow-up Work. |

## 7) Verification

```powershell
# Python test suite (run after every phase)
.venv-PowerAtlas\Scripts\pytest

# JS template tests (run after any acp.html change)
node tests/acp_page.test.mjs

# Standalone kiro-cli v3 protocol probes (Phase 0, and ad-hoc verification of Phases 2/3/4/6/7
# without touching the running PowerAtlas instance) — spawn directly:
# kiro-cli acp --agent-engine v3
# (see exploration's probe technique: initialize, answer getAccessToken via
# `kiro-cli chat _ get-kas-token`, session/new, then exercise the feature under test)
# Always clean up: delete created ~/.kiro/sessions/<hash>/sess_*/ directories and the
# throwaway script itself; confirm `git status` clean.

# Phase 8 only — live browser verification against the restarted PowerAtlas instance
# (requires explicit user approval for the restart first)
```

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Add a `/acp-v3` section (governance-required per `AGENTS.md:11` — the throwaway-prototype exemption ends once this plan promotes it toward production) | 8 |
| `plans/ROADMAP.md` | Remove the liveness-detection item (line 38, now implemented in Phase 1) and the merge item's status is unchanged (still deferred, per this plan's own Scope boundaries) | 1 |
| `plans/ROADMAP.md` | Correct the MCP OAuth item (line 34) — the real signal is `session_info_update` → `displayError`, not `_kiro/mcp/status` → `failedAuthorization`/`authorizationUrl` as originally written; narrow scope per this plan's Scope boundaries | 3 |
| `plans/ROADMAP.md` | Correct the `_kiro/spec/*`/`_kiro/workflow/*` item (line 36) — no dedicated notification methods exist; replace with a note pointing at SC-9's `session/request_permission` finding instead | 6 |
| `plans/ROADMAP.md` | Update/remove the `kiro-cli-v3` liveness attribution follow-up (line 27) once Phase 1 lands | 1 |
| `memory/MEMORY.md` | Update the `_session/steer` entry (~line 261-271) — the v3 echo mechanism is now confirmed (three-state `session_info_update`, not `AgentExecutionSteeringInjected`) | 3 |
| `memory/MEMORY.md` | Add an "Update (Phase 1, date)" note to the v3-liveness entry (~line 201-209) once `_lock_holder_v3`/`session.json.status` actually lands, mirroring the existing update-note pattern already used on the `_publish_live` entry (line 235) | 1 |
| `docs/KNOWLEDGE.md` | Update the `_get_tool_diffs_v3` / diff-backfill paragraph area if the delete implementation's directory-resolution helper is extracted as a new shared function worth documenting | 5 |
| `plans/tests/260701_POWERATLAS.md` | §2.27 (`api_acp_v3_delete_sessions`, lines ~359-361) currently documents the pre-hardening "not yet implemented" / "scoped out" behavior — update to reflect Phase 5's working implementation | 5 |
| `AGENTS.md` | No change expected (line 7's `/acp-v3` note is already accurate) — confirm during Phase 8 | 8 (doc-table-only) |

Doc-impact scan (mandatory sub-agent dispatch) also flagged, as code-comment cleanup rather than `*.md` documentation — folded into the relevant phases directly rather than listed here: stale "Phase 0"/"Phase 1"/"Phase 2" references inside `acp.py`'s `_SupervisorV3` class docstring (`4454`, `4456-4464`) that name the *archived spike's* phase numbers at exactly the lines this plan's own Phase 2 and Phase 4 edit — see those phases' Changes sections for the specific fix.

## 9) Implementation Divergences from Plan
`<Reserved>`

## Follow-up Work (Deferred)

1. **Merge `/acp` and `/acp-v3`.** User decision this planning session: kept parallel. Once this plan lands and v3 is production-hardened, the merge becomes a lower-risk, separately-scoped future project. Source: exploration Q2 / this plan's Scope boundaries.
2. **Linux cross-platform support for the ACP subsystem.** Not v3-specific (v2 has the identical Windows-only `_create_job()` limitation). Source: exploration Q3.
3. **Mode-switcher UI** for `/acp-v3` (letting a user pick `spec`/`quick-spec`/`bug-fix`/`plan`). Would unlock the modes SC-9's permission-handling defends against but doesn't itself build a path to. Source: Scope boundaries.
4. **Full MCP OAuth "Connect" flow.** No `authorizationUrl`-bearing signal has been observed; revisit if one is ever found. Source: Scope boundaries.
5. **`_pending_permission` expiry/timeout for an abandoned `session/request_permission` request.** Genuinely a product-shape decision (auto-refuse? default option? rely on the existing `~30`-minute `PROMPT_SILENCE_SECONDS` backstop?), not a deterministic bug fix — deferred rather than guessed at during this plan. Source: Risk R11, Review Log finding (Senior engineer, Medium).

## Review Log

### 2026-09-08 — Plan Creation (via /qplan, medium effort, 4 personas + doc-impact scan)

21 findings (8 High, 12 Medium, 1 Low informational) across Architect (gap-critic lens), Senior engineer, Security auditor, Reliability engineer, plus the mandatory doc-impact sub-agent. Three of the 8 High findings were independently raised by 2-3 personas each (strong convergent signal). All 8 High findings fixed. 11 of 12 Medium findings fixed; 1 deferred to Follow-up Work as a genuine product-shape decision, not a bug. Detailed rationale for every fix lives inline in the plan body (Design Decisions rows and phase Changes sections marked "review finding") rather than restated here.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | SC-7 (liveness detection) had a full Design Decision but no phase implemented it. | Fixed — folded into Phase 1 (now covers SC-6, SC-7, SC-10, partial SC-11). |
| 2 | High | SC-11 claimed all 9 `_handle_*_v3` functions get test coverage; 4 were never touched by any phase. | Fixed — Phase 1 adds baseline characterization tests for `load`/`subscribe`/`cancel`/`close`. |
| 3 | High | Phase 5's workspace-delete enumerated v3 sessions but never routed them to a working v3 delete call. | Fixed — Phase 5 point 4 adds explicit ID-prefix dispatch in `_acp_delete_many`/`_acp_delete_session`. |
| 4 | High | Phase 5 cited a path-traversal guard "already used elsewhere" in `data_kiro_v3.py` that doesn't exist. | Fixed — Phase 5 point 1 specifies a fresh `_SESSION_ID_RE.fullmatch` guard as the function's first line. |
| 5 | High | Phase 2's buffered-frame replay ran inside `new_session()`'s rollback-triggering try/except (3-persona convergence). | Fixed — Design Decisions "SC-1 replay isolation" row moves replay after the commit point, per-frame try/except. |
| 6 | High | `permission_response` had no check that the answering connection owns the request's session. | Fixed — Phase 6 point 2(b) adds `conn.session_id == entry["session_id"]` check, mirroring `_handle_steer_v3`. |
| 7 | High | Phase 6's cleanup trigger ("on close/cancel") is largely unreachable — close is normally refused mid-turn. | Fixed — Phase 6 point 3 moves cleanup into `_handle_prompt_v3`'s turn-end `finally`. |
| 8 | High | Phase 6's JSON-RPC reply shape was left "TBD" with no dedicated probe, unlike SC-8. | Fixed — Phase 0 expanded to probe both SC-8 and SC-9's reply shape before either implementation phase. |
| 9 | Medium | SC-1's buffering guard used a global `_reserved` counter, not session-scoped — leaks for unrelated stray notifications. | Fixed — capped buffer + `_sweep_once`-integrated eviction for orphaned entries (Design Decisions "SC-1 orphan-buffer sweep"). |
| 10 | Medium | `_pending_early_frames` had no size/byte bound, unlike `self.history`'s existing precedent. | Fixed — `_MAX_PENDING_EARLY_FRAMES = 50` cap, oldest-drop on overflow. |
| 11 | Medium | Buffer entries for a session_id whose `new_session()` never completes (RPC failure) leak permanently. | Fixed — same sweep mechanism as #9 reclaims these regardless of cause. |
| 12 | Medium | Buffering guard also fires during `load_session()`'s reservation window, but replay/cleanup was wired only into `new_session()`. | Fixed — the sweep (not phase-specific replay wiring) is what reclaims any orphaned entry regardless of which reservation buffered it. |
| 13 | Medium | Phase 6's Design Decision (`asyncio.Future`) and Changes section (plain dict) specified two incompatible mechanisms. | Fixed — Design Decisions "SC-9 UI shape" row standardizes on the plain-dict, direct-write mechanism. |
| 14 | Medium | Five new WS frame types (Phase 3, Phase 6) were never added to the `CLIENT_TYPES`/`SERVER_TYPES` allowlist `envelope()` enforces. | Fixed — explicit registration step added to both phases' Changes and exit criteria. |
| 15 | Medium | No atomicity between popping `_pending_permission` and writing the reply — two responses to one request could both pass validation. | Fixed — Phase 6 point 2(d) pops before the write; a second response hits "unknown_request." |
| 16 | Medium | `_note_context` is a fourth helper hardcoded to `_supervisor.sessions`, same bug class as SC-4's three, fixed by a new sibling function instead of parameterization. | Orchestrator: proposed-accept — Phase 3 now states explicitly why this asymmetry is intentional (no shared v2 call site exists to parameterize). |
| 17 | Medium | `_sidecar_records()` independently hardcodes `provider="kiro-cli"`, unaffected by the `_match_provider` fix alone. | Fixed — added to Phase 1 change 1 as a second, explicit fix site. |
| 18 | Medium | `_pending_permission` has no timeout — an abandoned client leaves a KAS turn blocked indefinitely. | Deferred — genuine product-shape decision, not a deterministic fix; recorded in Follow-up Work #5 and Risk R11. |
| 19 | Medium | `_emit_subagents_frame_v3` omits v2's `fan_out_id` filter; Phase 4 wired it into live updates without porting the filter. | Fixed — Phase 4 point 2 ports the filter and retires the now-redundant `_emit_subagents_frame_v3`. |
| 20 | Medium | Phase 5's new `delete_session` didn't invalidate `data_kiro_v3._session_path_cache`, risking a stale path to deleted files. | Fixed — Phase 5 point 1 adds explicit cache invalidation after a successful delete. |
| 21 | Medium | SC-7 says v3 "now writes `.lock` files" but the confirmed on-disk layout never lists `.lock`; Phase 5's directory-delete may miss it. | Fixed — Phase 5 point 5 requires confirming `.lock`'s actual location before implementation, with a fallback if it's a sibling file. |
| 22 | Medium | Phase 5's deletion had no protection against a v3 session held by an external process (v2 has `_lock_holder`, v3 had nothing). | Fixed — Phase 5 point 2 adds a `_lock_holder_v3` check (now available from Phase 1), refusing delete when externally held. |
| 23 | Low | `presence.py:66-69`'s comment cites a stale Follow-up item number for the sess_-prefix disambiguation. | Fixed — Phase 1 change 1 corrects the cross-reference. |
| 24 | Low | Phase 1's originally-described mechanism ("extract before match") didn't match `_scan()`'s actual call order. | Fixed — Design Decisions "Presence.py fix" row corrected to match-then-reroute, with the flag-sharing coincidence called out explicitly. |
| 25 | Low | Invariant 1's wording could be read as blocking SC-10's own stated v2 log-level change. | Fixed — Invariant 1 now explicitly carves out the logging-level exception. |
| 26 | Low | New agent-controlled UI surfaces (Phase 3, Phase 6) had no explicit `.textContent`-over-`innerHTML` instruction. | Fixed — added to both phases' client-side change items. |
| 27 | Low | `data_kiro_v3.py`'s internal cache-algorithm comments ("Phase 1 (fast)"/"Phase 2 (rebuild)") could read as project-phase references. | Orchestrator: proposed-accept — doc-impact's own assessment confirms surrounding text makes the algorithmic meaning clear; no action needed. |

Health: **Green** (all High findings resolved; the one unresolved Medium is an explicit, reasoned deferral to Follow-up Work, not an oversight).

## Harness Improvement Opportunities

- The `Agent` tool's `subagent_type: "fork"` guidance discourages checking on background forks mid-flight, but there's no equivalent guidance for background `general-purpose` sub-agent dispatches from `/qexplore`'s Step 1.5 — the orchestrator twice dispatched a trivial "check on the other agents" fork purely to keep the turn from ending while waiting, costing real tokens (one such call alone consumed ~380K subagent tokens for a one-line acknowledgment) for zero value, since task-notifications already arrive automatically without any action needed. Cost: ~380K wasted subagent tokens plus a full extra agent-dispatch round-trip, twice. Suggested change: state explicitly (in `/qexplore` Step 1.5 or the general multi-agent-coordination guidance in `shared/AGENTS.md`) that after dispatching parallel background sub-agents, the orchestrator should simply end its turn and wait — no filler action, no "keep-alive" dispatch.
