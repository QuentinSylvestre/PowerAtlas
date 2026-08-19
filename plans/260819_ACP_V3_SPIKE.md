# ACP v3 Spike — `/acp-v3` Prototype

> **Date**: 2026-08-19
> **Status**: Draft
> **Scope**: Build a working `/acp-v3` prototype that drives kiro-cli v3 ACP sessions, covering full v2 feature parity plus v3-specific behaviors, as throwaway spike infrastructure.

---

## Intent

### Problem statement & desired outcomes

The `/acp` surface in PowerAtlas drives kiro-cli exclusively via the v2 ACP protocol (`kiro-cli acp -a`). kiro-cli v3 (`--agent-engine v3`) ships a materially different protocol: an auth handshake requiring the client to answer an inbound `_kiro/auth/getAccessToken` JSON-RPC request with a fresh OIDC token from `kiro-cli chat _ get-kas-token`; `sess_`-prefixed session IDs; no lock files; `_meta.kiro.modeId` for agent selection; and different notification method names (`session/update` subtypes vs `_kiro.dev/*`).

The desired outcome is a working `/acp-v3` page that covers every feature `/acp` currently covers — session creation, resume, prompting, streaming, tool call rendering, crew panels, steer/queue, diff recovery on reload, image paste, slash commands, session deletion — plus any surfaceable v3-specific behaviors discovered during the spike. The spike validates feasibility, measures every remaining protocol unknown, and produces a recommendation for the v3 liveness detection approach. The follow-on plan inherits no protocol unknowns.

### Success criteria

- SC-1: `_SupervisorV3` spawns `kiro-cli acp --agent-engine v3`, completes the `_kiro/auth/getAccessToken` handshake, and creates a session via `session/new` with `modeId=kiro_default`.
- SC-2: At least one full `session/prompt` round-trip completes — agent streams tokens, tool calls render, turn ends cleanly.
- SC-3: Diff recovery works on reload for a session containing `fs_write` or `str_replace` edits — the diff row expands after a page reload.
- SC-4: All current `/acp` v2 features work on `/acp-v3` at parity: session rail (workspace/day/status grouping), session creation with workspace picker, resume of exited sessions, cancel, close, queue, steer, crew panel, image paste, slash command palette, session deletion.
- SC-5: The v3 liveness detection probe produces a documented recommendation (whether `session.json` status, agent refusal timing, or a presence hook injection is the right approach for production).
- SC-6: `CLOSE_METHOD` behavior on v3 is measured — the spike confirms whether `_kiro.dev/session/terminate` works or a different method is needed.
- SC-7: `_on_notification` v3 compatibility is confirmed by running a real turn and logging all notification method names received.
- SC-8: All current tests pass. New tests cover `_get_tool_diffs_v3`, `_stored_session_cwd_v3`, and the `_on_agent_request` token handler.
- SC-9: MCP OAuth and `_kiro/spec/*` / `_kiro/workflow/*` are documented as roadmap items.
- SC-10: Browser verification via Playwright confirms `/acp-v3` feature parity with `/acp` for SC-1 through SC-4.

### Scope boundaries & non-goals

**In scope:**
- `_SupervisorV3(Supervisor)` subclass in `acp.py` — overrides `_spawn`, `_on_agent_request`, `load_session`, `_stored_session_cwd`, and `_build_kas_session_params_v3`
- `_get_tool_diffs_v3(session_id)` function inlined in `acp.py` — reads v3 `messages.jsonl`, extracts `fs_write`/`str_replace` diffs, returns same `{toolCallId: {path, oldText, newText}}` shape as v2
- `_stored_session_cwd_v3(session_id)` function inlined in `acp.py` — reads v3 `session.json` → `workspacePaths[0]`
- Token handler in `_on_agent_request` — calls `kiro-cli chat _ get-kas-token` via `subprocess.run` on demand, replies with `{accessToken, expiresAt, profileArn, provider}`
- `_supervisor_v3 = _SupervisorV3()` singleton alongside `_supervisor`
- `_publish_live()` on both supervisors emits the union of both session sets to the shared hook
- Shared `_registry`, `_bubbles`, `_tasks` between v2 and v3 supervisors
- `/acp-v3` GET route and `/ws/acp-v3` WebSocket route in `web.py`
- `/api/acp-v3/sessions`, `/api/acp-v3/workspaces`, `/api/acp-v3/sessions/delete` listing/management endpoints
- `acp.html` gains `engine` Jinja variable; WebSocket URL and v3-specific blocks gated on `engine === "v3"`
- Remote-address allowlist in `web.py` updated for `/acp-v3` and `/ws/acp-v3`
- V3 liveness detection probe: test `session.json` status field reliability, agent refusal round-trip timing, presence hook injection feasibility
- `CLOSE_METHOD` probe on v3: confirm `_kiro.dev/session/terminate` works or find the correct method
- `_on_notification` v3 compatibility probe: log all notification method names on a real v3 turn
- Feature inventory: document every `/acp` v2 feature against v3 behavior as the spike proceeds
- Roadmap documentation for MCP OAuth and `_kiro/spec/*` / `_kiro/workflow/*`
- New tests for inlined v3 helpers and token handler
- Playwright browser verification of `/acp-v3`

**Not in scope:**
- Merging `_SupervisorV3` back into `_Supervisor` (follow-on plan)
- Linux cross-platform support (follow-on)
- MCP OAuth flow (`_kiro/mcp/status` with `failedAuthorization`) — roadmap item
- `_kiro/spec/*` and `_kiro/workflow/*` notifications — roadmap item
- Production-grade liveness detection implementation (spike produces recommendation only)
- Removing or deprecating `/acp` v2 path

---

## 1) Current State

`_Supervisor` class (`acp.py:2297–2420`): ~25 fields across 5 categories (spawn-time, session registry, sub-agent crew, compaction, per-session). Spawns `kiro-cli acp -a` (constants `KIRO_BINARY = "kiro-cli"`, `ACP_ARGS = ("acp", "-a")` at `acp.py:601–602`). `_on_message` dispatch (`acp.py:3047–3056`) already has three branches: response, inbound-request (`_on_agent_request`), notification. `_on_agent_request` (`acp.py:3098–3128`) currently refuses all inbound requests with `-32601`. Token retrieval: `kiro-cli chat _ get-kas-token` is an `.exe`, callable via `subprocess.run` without `shell=True`; probed 2026-08-19 — returns `{kind, data:{accessToken, expiresAt, profileArn, provider}}`, Enterprise account, `profileArn` required. `kiro-cli acp --agent-engine v3 -a` exits 2; `kiro-cli acp --agent-engine v3` (no `-a`) starts KAS with `--auth=acp-callback`, confirmed 2026-08-19.

v3 `messages.jsonl` field shapes (probed 2026-08-19 from live sessions): `fs_write` → `payload.args.path` + `payload.args.text`; `str_replace` → `payload.args.path`, `payload.args.oldStr`, `payload.args.newStr`; `tool_result` → `payload.toolCallId`, `payload.success` (bool). v3 `session.json` has `workspacePaths[0]` (not `cwd`).

`serve_socket` / `_dispatch` (`acp.py:4492`, `4569`): supervisor-agnostic — no `_supervisor.*` calls; the handler functions (`_handle_new`, `_handle_load`, etc.) call `_supervisor.*` directly. A parallel `serve_socket_v3` + `_dispatch_v3` is needed calling `_supervisor_v3.*`.

`_SESSION_ID_RE` (`launcher.py:25`, `^[\w\-]+$`): passes `sess_<uuid>` format — probe confirmed 2026-08-19. `CLOSE_METHOD = "_kiro.dev/session/terminate"` (`acp.py:425`): v3 compatibility unprobed — AS-5 open item. Remote allowlist (`web.py:1130`): exact-match dict; `/acp-v3` and `/ws/acp-v3` absent, must be added. `data_kiro_v3` has no `get_tool_diffs` equivalent; v3 diff backfill must be inlined in `acp.py` per isolation boundary constraint (`acp.py` imports only `config.CONFIG_DIR` and `launcher._SESSION_ID_RE` from the package).

## 2) Goal

Build `_SupervisorV3(_Supervisor)` and a `/acp-v3` page that drives kiro-cli v3 ACP sessions end-to-end, achieving full v2 feature parity and producing measurements for all remaining protocol unknowns.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| `/acp-v3` surface | New separate route | Extend `/acp` with v2/v3 flag | Keeps v2 path intact; spike scope is throwaway; delta is explicit |
| Auth token source | `subprocess.run` on demand, binary resolved via `shutil.which("kiro-cli")` at module level | Pre-fetch at spawn; user-supplied | Mirrors TUI behavior; freshest token; `shutil.which` avoids PATH shadowing |
| `_SupervisorV3` implementation | Subclass `_Supervisor`, override methods listed in Phase 1 | Full copy; protocol adapter | Subclass makes the diff literal; inheritance safe for throwaway |
| v3 cwd / diff helpers | Inlined in `acp.py` | Import from `data_kiro_v3` | `data_kiro` is already imported (`acp.py:82`) — but `data_kiro_v3` adds a second guarded import; inlining avoids touching the exit criterion grep and keeps the boundary explicit for throwaway code |
| `_on_notification` routing | Override in `_SupervisorV3` to replace `_emit` calls with `_emit_v3` for v3 sessions | Share `_on_notification` | `_emit` is hardcoded to `_supervisor.record()` — v3 history must route to `_supervisor_v3.record()`; shared `_on_notification` silently corrupts both history buffers |
| Shared singletons | `_registry`, `_bubbles`, `_tasks` shared; `_publish_live` emits union | Separate registry per supervisor | v2/v3 session IDs disjoint by format; sharing safe; `_publish_live` signature takes `(frozenset, agent_pid)` — union must supply both |
| `acp.html` template | Single file with `engine` Jinja variable | Separate `acp_v3.html` | Avoids maintaining two copies of 7,437-line file |
| Platform | Windows-only | Cross-platform | Linux support is follow-on; pywin32 teardown guarantee is required |
| Handler functions | Parallel `_dispatch_v3` + `_handle_*_v3` functions calling `_supervisor_v3` | Make `_dispatch` supervisor-aware | `_dispatch` is supervisor-agnostic by design; preserves that boundary |
| `_supervisor_v3` initialization | Inside `apply_config` / lifespan, not at module level | Module-level `_supervisor_v3 = _SupervisorV3()` | `apply_config` rebinds `MAX_SESSIONS` and `ACP_IDLE_TTL_SECONDS` — v3 must read these at runtime same as v2 |
| `_sweep_once` v3 coverage | Extend `_sweep_once` to also iterate `_supervisor_v3.sessions` | Separate `_sweep_once_v3` | Single sweep function avoids a second `_sweep_loop` task |

## 4) External Dependencies & Costs

### Required external changes

| Category | Change needed | Owner | Status |
|---|---|---|---|
| kiro-cli binary | `--agent-engine v3` on `acp` subcommand — confirmed available | n/a | Done (probed) |
| Amazon Q Enterprise auth | Active profile for `kiro-cli chat _ get-kas-token` | User | Available (probed) |

All other rows N/A.

### Cost impact

None — no new infrastructure, cloud resources, or third-party services. Token fetches are local subprocess calls.

## 5) Implementation Phases

### Phase 0: Wire probes — protocol unknowns [QA]

**Goal**: Verify AS-5, AS-6, and R4 before writing any production code. Run throwaway probe scripts; record results in this plan. Results drive implementation decisions in Phase 1.

**Why horizontal**: Establishes empirical premises (CLOSE_METHOD, initialize/auth timing, notification method names, ACP-session path discovery) that all subsequent phases depend on. Combining with Phase 1 would commit implementation choices to untested premises.

**File scope**: no production files modified. Throwaway scripts only (deleted after use, confirmed clean with `git status`).

**Probes to run**:

1. **AS-6 — `initialize` / auth timing**: spawn `kiro-cli acp --agent-engine v3` via `subprocess.Popen` (stdout pipe, stderr pipe), send `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{"fs":{"readTextFile":false,"writeTextFile":false},"terminal":false}}}`, log every line received. Confirm: `getAccessToken` request arrives before or after `initialize` result. Expected: `initialize` result arrives first per governance docs.

2. **AS-5 — CLOSE_METHOD**: extend probe 1 to complete auth handshake, `session/new`, and then send `{"jsonrpc":"2.0","id":N,"method":"_kiro.dev/session/terminate","params":{"sessionId":"<sid>"}}`. Log response. Expected: either a result (method works) or a `-32601` (method unknown on v3).

3. **AS-4 — notification method names**: extend probe to send a `session/prompt` with a trivial request. Log every `method` field received in notifications. Check for any method names not handled by `_on_notification`'s existing dispatch.

4. **R4 — ACP-session path discovery**: after `session/new` creates a v3 session, call `data_kiro_v3._find_v3_session_path(session_id)` immediately. Confirm it returns the correct `messages.jsonl` path without a full cache rebuild.

**Record findings** under a `### Phase 0 Results` heading added to this plan after execution.

**Exit criteria**:
- [ ] AS-6 result recorded: `initialize` response arrives before / after `getAccessToken` request (circle one, with evidence line from wire log)
- [ ] AS-5 result recorded: `_kiro.dev/session/terminate` response on v3 documented (method works / unknown / other)
- [ ] AS-4 result recorded: list of all notification method names observed on a real v3 turn
- [ ] R4 result recorded: `_find_v3_session_path` returns correct path for ACP-created session (yes / no, with evidence)
- [ ] All probe scripts deleted; `git status` confirms no tracked file changes

**Covers**: SC-5 (liveness probe), SC-6, SC-7

### Phase 1: `_SupervisorV3` skeleton + auth handshake [QA]

**Goal**: Add `_SupervisorV3(_Supervisor)` to `acp.py` with the auth handshake working end-to-end. `ensure_started()` on `_supervisor_v3` must complete without error. No HTTP routes yet.

**File scope**: `src/power_atlas/acp.py`

**Changes**:

1. Add constants after the existing `ACP_ARGS` line (`acp.py:602`):
   ```python
   ACP_V3_ARGS = ("acp", "--agent-engine", "v3")
   # Resolved once at module load — avoids PATH shadowing at token-fetch time.
   _KIRO_V3_TOKEN_BINARY: str | None = shutil.which("kiro-cli")
   ```

2. Add `_get_tool_diffs_v3(session_id: str) -> dict` inline function in `acp.py`. Scans `Path.home() / ".kiro" / "sessions"` hash dirs for `<sess_prefix>/messages.jsonl`. Includes a single 200 ms retry on cache miss (unconditional — avoids a conditional that depends on Phase 0's R4 result).

   > **Rejected**: importing `data_kiro_v3` directly — adds a second guarded import to the exit criterion grep (though `data_kiro` already exists at `acp.py:82`, adding `data_kiro_v3` touches the grep contract). **Use instead**: inline the hash-dir scan logic.

   Function contract: returns `{toolCallId: {"path": ..., "oldText": ..., "newText": ...}}`. Reads `messages.jsonl`, iterates lines, collects `payload.type == "tool_call"` with `payload.toolName in ("fs_write", "str_replace")`, cross-checks `payload.type == "tool_result"` for `payload.success == True` on matching `payload.toolCallId`. For `fs_write`: `oldText=None`, `newText=payload.args["text"]`, path=`payload.args["path"]`. For `str_replace`: `oldText=payload.args["oldStr"]`, `newText=payload.args["newStr"]`, path=`payload.args["path"]`.

3. Add `_stored_session_cwd_v3(session_id: str) -> str` inline function. **Validates `session_id` against `_SESSION_ID_RE.fullmatch` before any path construction — returns `""` immediately on mismatch.** Scans `Path.home() / ".kiro" / "sessions"` hash dirs for `<sess_id>/session.json`, reads `workspacePaths[0]`. Returns `""` on any error.

4. Add `_SupervisorV3(_Supervisor)` class with overrides. The **full list of overrides** is:

   - `_spawn` — uses `ACP_V3_ARGS` instead of `ACP_ARGS`; otherwise identical to `_Supervisor._spawn`. Implemented as a copy-and-substitute to avoid `_acp_args`-class-attribute refactoring (acceptable for throwaway spike). Exit criteria verify the copy does not omit `_build_child_env`, `_CREATE_NO_WINDOW`, or the job-object assignment.

   - `_on_agent_request` — handles `_kiro/auth/getAccessToken` via `_fulfill_token`; falls through to `super()._on_agent_request(msg)` for all other methods.

   - `_on_notification` — **overrides to route `_emit` calls to `_emit_v3`**. Copy `_Supervisor._on_notification` body, replacing every `_emit(session_id, frame)` call with `_emit_v3(session_id, frame)`. This ensures v3 frame history is recorded in `_supervisor_v3.history`, not `_supervisor.history`.

   - `new_session` — uses `_build_kas_session_params_v3()` (which adds `modeId`) instead of `_build_kas_session_params()`. This is required for SC-1 (`modeId=kiro_default`).

   - `load_session` — no lock hint; uses `_stored_session_cwd_v3` for cwd; uses `_get_tool_diffs_v3` for diff backfill.

   - `_publish_live` — emits union of both supervisors. Signature is `_publish_live(self)` matching `_Supervisor._publish_live`. Read the actual `_publish_live` signature from `acp.py` before implementing — if it takes `(self, agent_pid)` or another arg, match it.

5. Add `_build_kas_session_params_v3(mode_id: str = "kiro_default") -> dict`:
   ```python
   def _build_kas_session_params_v3(mode_id: str = "kiro_default") -> dict[str, Any]:
       return {"_meta": {"kiro": {"modeId": mode_id, "steering": [{**d} for d in _OVERLAY_STEERING]}}}
   ```

6. Add `_supervisor_v3: _SupervisorV3 | None = None` at module level as a `None` sentinel. **Initialize inside `apply_config` (not at module level)**, matching how `_supervisor` relies on module-level globals that `apply_config` rebinds. Log at `ERROR` if `_SupervisorV3()` construction fails, and leave `_supervisor_v3 = None`.

7. `_publish_live` override emits the union. Read the actual `_Supervisor._publish_live` signature (`acp.py`) before writing the override — match its argument list exactly:
   ```python
   # In _SupervisorV3._publish_live (example — check actual signature):
   def _publish_live(self) -> None:
       hook_fn = sessions_changed_hook
       if hook_fn is None:
           return
       v2_sessions = frozenset(_supervisor.sessions) if _supervisor is not None else frozenset()
       hook_fn(frozenset(self.sessions) | v2_sessions, self._proc.pid if self._proc else 0)
   ```
   Similarly update `_Supervisor._publish_live` to include v3 sessions in its union.

8. Add `_fulfill_token` async method on `_SupervisorV3`:
   ```python
   async def _fulfill_token(self, request_id) -> None:
       binary = _KIRO_V3_TOKEN_BINARY
       if binary is None:
           log.warning("ACP v3: kiro-cli not on PATH; cannot fulfill token request")
           await asyncio.to_thread(self._write, {
               "jsonrpc": "2.0", "id": request_id,
               "error": {"code": -32000, "message": "kiro-cli binary not found"},
           })
           return
       try:
           result = await asyncio.to_thread(
               subprocess.run, [binary, "chat", "_", "get-kas-token"],
               capture_output=True, text=True, timeout=15,
               env=_build_child_env({}),  # scrub CLAUDE_* markers; no ACP_CLIENT_NAME needed
               creationflags=_CREATE_NO_WINDOW,
           )
           if result.returncode != 0:
               raise RuntimeError(f"exit {result.returncode}")
           token_data = json.loads(result.stdout)["data"]
           response = {
               "jsonrpc": "2.0", "id": request_id,
               "result": {
                   "accessToken": token_data["accessToken"],
                   "expiresAt": token_data["expiresAt"],
                   "profileArn": token_data.get("profileArn", ""),
                   "provider": token_data.get("provider", ""),
               },
           }
       except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
           # Kill the subprocess on timeout to avoid blocking the thread pool.
           try:
               if hasattr(exc, 'process') and exc.process:
                   exc.process.kill()
           except Exception:
               pass
           log.warning("ACP v3: token fetch timed out or failed")
           response = {"jsonrpc": "2.0", "id": request_id,
                       "error": {"code": -32000, "message": "Token fetch timed out"}}
       except (json.JSONDecodeError, KeyError, ValueError):
           # Do NOT include stdout/exc in the message — may contain partial token data.
           log.warning("ACP v3: token response had unexpected format")
           response = {"jsonrpc": "2.0", "id": request_id,
                       "error": {"code": -32000, "message": "Token response format error"}}
       except Exception:
           log.warning("ACP v3: token fetch failed (unexpected error)")
           response = {"jsonrpc": "2.0", "id": request_id,
                       "error": {"code": -32000, "message": "Token fetch failed"}}
       try:
           await asyncio.to_thread(self._write, response)
       except AcpError as exc:
           log.warning("ACP v3: could not deliver token response: %s — discarding session", exc)
           # Do not leave KAS waiting; tear down the session.
           self._discard("Token delivery failed: could not write auth response")
   ```

9. Extend `_sweep_once` to also iterate `_supervisor_v3.sessions`:
   ```python
   # After the existing v2 sweep loop in _sweep_once, add:
   if _supervisor_v3 is not None:
       for session_id, meta in list(_supervisor_v3.sessions.items()):
           if _sweepable(session_id, meta, now):
               # ... mirror the v2 sweep logic for _supervisor_v3
   ```
   Also update the `_sweep_loop` early-exit guard to check both supervisors.

10. Update `shutdown()` (`acp.py:4483`):
    ```python
    def shutdown() -> None:
        _supervisor.shutdown()
        if _supervisor_v3 is not None:
            _supervisor_v3.shutdown()
    ```

11. Update `_registry.attach` and `_registry.detach` to call `touch_used` on the correct supervisor (look up whether the session is in `_supervisor.sessions` or `_supervisor_v3.sessions`):
    ```python
    def attach(self, conn, session_id):
        ...
        if _supervisor_v3 is not None and session_id in _supervisor_v3.sessions:
            _supervisor_v3.touch_used(session_id)
        else:
            _supervisor.touch_used(session_id)
    ```

**Exit criteria**:
- [ ] `_SupervisorV3` class present with all overrides: `_spawn`, `_on_agent_request`, `_on_notification`, `new_session`, `load_session`, `_publish_live`
- [ ] `_fulfill_token` present with separate `except` blocks for `json.JSONDecodeError`/`KeyError` (no token in error message), `TimeoutExpired` (subprocess killed), and general `Exception`; `_discard` called on pipe-write failure
- [ ] `_KIRO_V3_TOKEN_BINARY = shutil.which("kiro-cli")` at module level; used in `_fulfill_token`
- [ ] `_supervisor_v3` initialized inside `apply_config`, not at module level; construction failure logged at ERROR
- [ ] `_sweep_once` extended to cover `_supervisor_v3.sessions`; `_sweep_loop` guard includes v3
- [ ] `shutdown()` calls `_supervisor_v3.shutdown()` if not None
- [ ] `_registry.attach`/`detach` route `touch_used` to the correct supervisor
- [ ] `_publish_live` union: read actual `_Supervisor._publish_live` signature before implementing; match it
- [ ] `_stored_session_cwd_v3` validates `session_id` via `_SESSION_ID_RE.fullmatch` before any path join
- [ ] `_get_tool_diffs_v3` includes unconditional 200 ms retry on empty result
- [ ] `acp.py` isolation boundary intact: `grep -E "from \.data_kiro_v3|import data_kiro_v3" src/power_atlas/acp.py` returns no hits
- [ ] Manual smoke test: call `_supervisor_v3.ensure_started()` from a Python script, observe no exception; verify KAS subprocess starts and `_ready` is `True`
- [ ] `.venv-PowerAtlas\Scripts\pytest` passes (all existing tests)
- [ ] Phase 0 KAS v3 startup time measured and noted; if > 20 s, raise `INITIALIZE_TIMEOUT_SECONDS` for v3

**Covers**: SC-1 (partial — handshake only), SC-8 (partial)

### Phase 2: Session lifecycle + turn [QA]

**Goal**: Add the v3 handler functions and `serve_socket_v3` so that `/ws/acp-v3` can create sessions, send prompts, and observe streamed turns with tool calls.

**File scope**: `src/power_atlas/acp.py`

**Changes**:

1. Add parallel module-level handler functions that mirror their v2 counterparts but call `_supervisor_v3`:

   | v2 function | v3 function | Change from v2 |
   |---|---|---|
   | `_handle_new(conn, payload)` | `_handle_new_v3(conn, payload)` | Call `_supervisor_v3.new_session(cwd)`; use `_build_kas_session_params_v3()` |
   | `_handle_load(conn, session_id)` | `_handle_load_v3(conn, session_id)` | Call `_supervisor_v3.load_session(sid, cwd)`; no lock hint; cwd from `_stored_session_cwd_v3` |
   | `_handle_subscribe(conn, session_id)` | `_handle_subscribe_v3(conn, session_id)` | Check `_supervisor_v3.subagent_sessions` first, then `_supervisor_v3.sessions`; use `_supervisor_v3._diff_backfill` |
   | `_handle_prompt(conn, session_id, payload)` | `_handle_prompt_v3(conn, session_id, payload)` | Call `_supervisor_v3.prompt(sid, ...)`; same queue/steer logic |
   | `_handle_steer(conn, session_id, payload)` | `_handle_steer_v3(conn, session_id, payload)` | Call `_supervisor_v3.steer(sid, ...)` |
   | `_handle_cancel(conn, session_id)` | `_handle_cancel_v3(conn, session_id)` | Call `_supervisor_v3.cancel(sid)` |
   | `_handle_close(conn, session_id)` | `_handle_close_v3(conn, session_id)` | Call `_supervisor_v3.close_session(sid)` |
   | `_handle_commands_options(conn, session_id, payload)` | `_handle_commands_options_v3(...)` | Call `_supervisor_v3.commands_options(...)` |
   | `_handle_commands_execute(conn, session_id, payload)` | `_handle_commands_execute_v3(...)` | Call `_supervisor_v3.commands_execute(...)` |

2. Add `_dispatch_v3(conn, frame)` mirroring `_dispatch` but routing to `_handle_*_v3` functions.

3. Add `serve_socket_v3(ws: WebSocket)` — identical to `serve_socket` except calls `_dispatch_v3`.

4. `_emit` is shared and already supervisor-agnostic (`_supervisor.record()` + `_registry.broadcast()`). v3 sessions will use the same `_emit` — `_supervisor_v3.record()` replaces `_supervisor.record()` for v3 session IDs. Two options:
   - Option A: Add a second `_emit_v3` function calling `_supervisor_v3.record()`.
   - Option B: Make `_emit` look up which supervisor holds the session ID.

   > **Rejected**: Option B (look up supervisor in `_emit`) — couples a shared utility to both supervisor singletons. **Use instead**: Option A — `_emit_v3(session_id, frame)` calling `_supervisor_v3.record()` and `_registry.broadcast()`. All v3 handler functions call `_emit_v3`; v2 handlers continue calling `_emit`.

**Exit criteria**:
- [ ] All 9 `_handle_*_v3` functions present in `acp.py`
- [ ] `_dispatch_v3` and `serve_socket_v3` present
- [ ] `_emit_v3` function present, calling `_supervisor_v3.record()` and `_registry.broadcast()`
- [ ] Manual smoke test (requires Phase 3 route to exist OR a raw WebSocket client): connect to `/ws/acp-v3`, send `{"type":"new","payload":{"cwd":"<workspace>"}}`, observe `meta.pending` frame, then session frame with a `sess_`-prefixed ID
- [ ] `.venv-PowerAtlas\Scripts\pytest` passes

**Covers**: SC-1, SC-2 (partial — turn requires Phase 3 page)

### Phase 3: `/acp-v3` HTTP routes + `acp.html` engine variable [QA]

**Goal**: Add all HTTP/WebSocket routes for `/acp-v3` in `web.py` and the `engine` variable to `acp.html`. After this phase the full `/acp-v3` page is reachable.

**File scope**: `src/power_atlas/web.py`, `src/power_atlas/templates/acp.html`

**`web.py` changes**:

1. Add path constants after the existing `_ACP_*` constants (`web.py:692–715` area):
   ```python
   _ACP_V3_PATH = "/acp-v3"
   _ACP_V3_WS_PATH = "/ws/acp-v3"
   _ACP_V3_LISTING_PATH = "/api/acp-v3/sessions"
   _ACP_V3_WORKSPACES_PATH = "/api/acp-v3/workspaces"
   _ACP_V3_DELETE_PATH = "/api/acp-v3/sessions/delete"
   ```

2. Add `/acp-v3` and `/ws/acp-v3` to `_REMOTE_ALLOWED_PATHS` dict (`web.py:1130`):
   ```python
   _ACP_V3_PATH: "http",
   _ACP_V3_WS_PATH: "websocket",
   _ACP_V3_LISTING_PATH: "http",
   _ACP_V3_WORKSPACES_PATH: "http",
   _ACP_V3_DELETE_PATH: "http",
   ```

3. Add route handlers mirroring their v2 counterparts but calling `_supervisor_v3`:
   - `@app.get(_ACP_V3_PATH)` → `acp_v3_page`: renders `acp.html` with `engine="v3"` and `acp_token`. **Must include**: `_request_host_allowed(request)` check (DNS rebinding defence), `_acp_csp(nonce, host)` CSP response header, `Cache-Control: no-store`, and `can_delete` context variable computed via same `_is_remote_peer` / `_is_mobile_ua` logic as `acp_page`.
   - `@app.websocket(_ACP_V3_WS_PATH)` → `ws_acp_v3`: calls `_acp_token_ok(ws.query_params.get("t", ""))` and `_ws_origin_ok(ws)` **before** `await ws.accept()`, then calls `acp.serve_socket_v3(ws)`
   - `@app.get(_ACP_V3_LISTING_PATH)` → mirrors `api_acp_sessions` but snapshots `_supervisor_v3.sessions`
   - `@app.get(_ACP_V3_WORKSPACES_PATH)` → mirrors `api_acp_workspaces` using `_supervisor_v3`
   - `@app.post(_ACP_V3_DELETE_PATH)` → mirrors `api_acp_delete_sessions` checking `_supervisor_v3` held set

4. Update `same_origin_guard` to extend the `elif request.url.path == _ACP_PATH` check to `elif request.url.path in (_ACP_PATH, _ACP_V3_PATH)` — this is required so the navigation guard fires for `/acp-v3`. Update `_acp_navigation_ok` (`web.py:844`) to accept `/acp-v3` referrers.

5. Initialize `_supervisor_v3` in the app lifespan (same location as `acp.start_sweeper()` is called).

**`acp.html` changes**:

1. Add `engine` Jinja variable to the template context (set from the route handler). At the top of the `<script>` section, add:
   ```js
   const ENGINE = {{ engine | tojson }};  // "v2" or "v3"
   const WS_PATH = ENGINE === "v3" ? "/ws/acp-v3" : "/ws/acp";
   ```

2. Replace the hardcoded `/ws/acp` WebSocket URL construction with `WS_PATH`.

3. Update the session-listing API URL (`/api/acp/sessions` → use `ENGINE === "v3" ? "/api/acp-v3/sessions" : "/api/acp/sessions"`) for the rail's data source.

4. Update workspace picker API URL similarly for `/api/acp-v3/workspaces`.

5. Update session delete API URL for `/api/acp-v3/sessions/delete`.

**`AGENTS.md` change** (doc update from doc-impact analysis):

Update the statement at line 7: "All JS for `/acp` is inline in `src/power_atlas/templates/acp.html`" → "All JS for `/acp` and `/acp-v3` is inline in `src/power_atlas/templates/acp.html`. `/acp-v3` uses the same template with `engine="v3"`; the same hard-reload rule applies."

**Exit criteria**:
- [ ] `GET /acp-v3` returns 200 and renders `acp.html` with `engine="v3"` in context
- [ ] `acp_v3_page` calls `_request_host_allowed(request)` (grep confirms — DNS rebinding defence)
- [ ] `acp_v3_page` sets `Content-Security-Policy` header via `_acp_csp(nonce, host)` (grep confirms)
- [ ] `acp_v3_page` sets `Cache-Control: no-store` header (grep confirms)
- [ ] `acp_v3_page` passes `can_delete` context variable using same logic as `acp_page`
- [ ] `ws_acp_v3` calls `_acp_token_ok` and `_ws_origin_ok` before `await ws.accept()` (read implementation, confirm order)
- [ ] `same_origin_guard` extended to include `_ACP_V3_PATH` (grep `_ACP_V3_PATH` in guard condition)
- [ ] `engine` Jinja variable present in template context and JS `const ENGINE` set correctly
- [ ] `WS_PATH` uses `/ws/acp-v3` when `ENGINE === "v3"`
- [ ] Rail session listing uses `/api/acp-v3/sessions` when `engine === "v3"`
- [ ] `/ws/acp-v3` and `/api/acp-v3/*` in `_REMOTE_ALLOWED_PATHS` (grep confirms)
- [ ] `AGENTS.md` line 7 updated with `/acp-v3` + `engine="v3"` note
- [ ] Browser hard-reload on `/acp-v3` loads the page; `/acp` still works unchanged
- [ ] `node tests/acp_page.test.mjs` passes (may need `engine` variable fixture — see below)
- [ ] `.venv-PowerAtlas\Scripts\pytest` passes

**`acp_page.test.mjs` fix**: The test renders `acp.html`; any test that exercises code now gated on `ENGINE` needs the fixture to inject `engine = "v2"` (so v2 paths remain unchanged in tests). Identify all tests that would fail due to the new `ENGINE` variable being undefined and add the fixture injection.

**Covers**: SC-4 (page loads), SC-1 (full end-to-end path now reachable)

### Phase 4: Diff recovery [QA]

**Goal**: Verify that `_get_tool_diffs_v3` produces correct backfill for a v3 session with file edits, and that diff rows expand after a page reload.

**File scope**: `src/power_atlas/acp.py` (if R4 probe revealed path-cache issues), `src/power_atlas/data_kiro_v3.py` (read-only reference)

**Changes**:

1. If R4 probe (Phase 0) showed `_find_v3_session_path` misses ACP-created sessions on first call: add a `_session_path_cache` invalidation call after `session/new` in `_SupervisorV3.new_session` — specifically, call `data_kiro_v3._session_path_cache.pop(session_id, None)` via the inlined path resolver reset.

   > **Rejected**: importing `data_kiro_v3` for cache invalidation — violates isolation boundary. **Use instead**: the inlined `_find_v3_session_path_inline(session_id)` helper (already in `acp.py` from Phase 1) re-scans the hash dirs on every call — it only caches positive results in `data_kiro_v3._session_path_cache`, which `acp.py` does not share. The inline scanner is stateless; it will find a newly-created session on the next call. If the R4 probe shows the inline scanner misses the session, the fix is to add a small retry (1 re-scan after 200ms) in `_get_tool_diffs_v3`.

2. Run a full load → edit → reload cycle on a v3 session manually:
   - Create a session on `/acp-v3`, run a prompt that produces an `fs_write` tool call
   - Reload the page
   - Confirm the edit row expands with the diff (oldText/newText present)

**Exit criteria**:
- [ ] `_get_tool_diffs_v3` returns non-empty dict for a session with at least one `fs_write` tool call that succeeded
- [ ] Reload of `/acp-v3` page with a session containing file edits: diff row expands with correct path and diff content
- [ ] `str_replace` diff recovery also verified (oldText = `oldStr`, newText = `newStr`)
- [ ] R4 probe result addressed (cache invalidation or retry documented)
- [ ] `.venv-PowerAtlas\Scripts\pytest` passes

**Covers**: SC-3

### Phase 5: Liveness detection probe + CLOSE_METHOD resolution [QA]

**Goal**: Complete SC-5 and SC-6. Run the liveness detection probe and document the recommendation. Confirm `CLOSE_METHOD` on v3 (from Phase 0 AS-5 result) and implement if a different method is needed.

**File scope**: `src/power_atlas/acp.py` (if CLOSE_METHOD probe shows a different method is needed)

**Liveness probe procedure**:

1. With a v3 session live in `/acp-v3` (turn active or idle), check `session.json` `status` field. Record the value. Repeat for an idle session. Determine if the field reliably distinguishes live from idle between turns.

2. With a v3 session live in a terminal (`kiro-cli chat --agent-engine v3`), attempt `session/load` on that session via `_supervisor_v3`. Measure the round-trip time to refusal. Compare to the 0.73–0.84 s measured for v2.

3. Assess whether injecting a presence hook (reading `_supervisor_v3.sessions` from `presence.py` via the existing `set_sessions_changed_hook` pattern) provides a faster hint than the agent refusal. The hook pattern is already implemented for the union of sessions.

4. Record the recommendation (one of: status-field check, rely on agent refusal, presence hook) with timing evidence.

**CLOSE_METHOD resolution** (using Phase 0 AS-5 result):
- If `_kiro.dev/session/terminate` works on v3: no change needed; add a comment in `_SupervisorV3` confirming it.
- If v3 returns `-32601` (unknown method): add `CLOSE_METHOD_V3` constant and override `close_session` in `_SupervisorV3` to use the correct method.
- Record the finding in Phase 0 Results section.

**Exit criteria**:
- [ ] Liveness recommendation documented in `### Phase 5 Results` section of this plan: chosen approach, timing evidence, rationale
- [ ] SC-5 satisfied: written recommendation present with ≥2 data points (status field reading + agent refusal time)
- [ ] CLOSE_METHOD confirmed or corrected for v3 (grep `CLOSE_METHOD_V3` or confirming comment in `_SupervisorV3`)
- [ ] If CLOSE_METHOD changed: `.venv-PowerAtlas\Scripts\pytest` passes
- [ ] Idle sweeper behavior verified: a v3 session that exceeds `ACP_IDLE_TTL_SECONDS` with no subscriber is reclaimed

**Covers**: SC-5, SC-6

### Phase 6: Feature inventory + roadmap documentation [P:7]

**Goal**: Walk every `/acp` v2 feature against v3 behavior; document the inventory in this plan. Add MCP OAuth and spec/workflow as roadmap items.

**File scope**: `plans/260819-1740_ACP_V3_SPIKE.md`, `plans/ROADMAP.md`

**Why horizontal**: Documentation-only phase; no executable code. Eligible for parallel with Phase 7 (no shared files, no dependency).

**Inventory table** to be produced (skeleton — filled during execution):

| Feature | v2 behavior | v3 behavior | Delta/note |
|---|---|---|---|
| Session creation | `session/new {cwd, mcpServers, _meta.kiro.steering}` | Same + `modeId` | `_build_kas_session_params_v3` adds `modeId` |
| Session resume | `session/load`, lock hint | `session/load`, no lock hint | Lock detection removed |
| Auth handshake | None (trust-all-tools) | `_kiro/auth/getAccessToken` | `_fulfill_token` handler |
| Session IDs | bare UUID | `sess_<uuid>` | `_SESSION_ID_RE` passes both |
| Diff recovery | `data_kiro.get_tool_diffs` reads `cli/<id>.jsonl` | `_get_tool_diffs_v3` reads `messages.jsonl` | Field names differ (args.text vs content) |
| Streaming / tool calls | `session/update` subtypes + `_kiro.dev/*` methods | `session/update` subtypes only | v2-only branches become dead code |
| Slash commands | `_kiro.dev/commands/available` notification | `session/update` with `available_commands_update` | Existing subtype branch handles this |
| CLOSE | `_kiro.dev/session/terminate` | TBD (Phase 0/5 result) | May need `CLOSE_METHOD_V3` |
| Liveness | Lock file hint | TBD (Phase 5 result) | Recommendation documented |
| Crew/subagents | `_kiro.dev/subagent/list_update` | TBD (Phase 0 observation) | Same or different method |
| MCP OAuth | Not surfaced | `_kiro/mcp/status` with `failedAuthorization` | Out of scope — roadmap item |
| Spec/workflow | Not present | `_kiro/spec/*`, `_kiro/workflow/*` | Out of scope — roadmap item |

**Roadmap additions** (`plans/ROADMAP.md`):

Under an appropriate section (or create `### ACP v3 Follow-up`):
- `[POST-SPIKE] MCP OAuth in /acp-v3`: when a v3 session's MCP server requires OAuth, `_kiro/mcp/status` notification arrives with `failedAuthorization: true` and an `authorizationUrl`. Surface this in the UI (a "Connect" prompt). Identical to the v2 OAuth flow per governance docs.
- `[POST-SPIKE] _kiro/spec/* and _kiro/workflow/* notification handling`: v3 emits spec workflow and runner notifications not present in v2. Assess whether the PowerAtlas UI should surface them.
- `[POST-SPIKE] CLOSE_METHOD and liveness detection production implementation`: implement the production-grade liveness detection approach recommended in Phase 5, and confirm/codify the v3 close method.
- `[POST-SPIKE] Merge /acp and /acp-v3`: once the spike is validated, plan the merge of `_SupervisorV3` into `_Supervisor` (engine parameter or refactor) and retire the separate route.

**Exit criteria**:
- [ ] Feature inventory table filled with v3 behavior column for every row
- [ ] `### Phase 6 Results` section added to this plan with completed inventory
- [ ] MCP OAuth, spec/workflow, CLOSE_METHOD/liveness, and merge items added to `plans/ROADMAP.md`
- [ ] `docs/KNOWLEDGE.md` line ~121 (`_Supervisor.load_session()` / `get_tool_diffs` paragraph) updated to note v3 parallel `_get_tool_diffs_v3`

**Covers**: SC-9

### Phase 7: Tests + Playwright verification [QA] [P:6]

**Goal**: Write pytest tests for the new inlined v3 helpers and token handler. Update `acp_page.test.mjs` for the `engine` variable. Run Playwright browser verification of `/acp-v3`.

**File scope**: `tests/test_web.py`, `tests/acp_page.test.mjs`, `src/power_atlas/acp.py` (no production changes — tests only)

**`test_web.py` additions** (new test class `TestSupervisorV3`):

1. `test_get_tool_diffs_v3_fs_write`: create a temp `messages.jsonl` with a `tool_call` (`fs_write`, `status="approved"`) + `tool_result` (`success=True`) pair; verify `_get_tool_diffs_v3` returns `{toolCallId: {path, oldText: None, newText: <text>}}`.

2. `test_get_tool_diffs_v3_str_replace`: similar with `str_replace`; verify `oldText=oldStr`, `newText=newStr`.

3. `test_get_tool_diffs_v3_failed_result_excluded`: include a `tool_result` with `success=False`; verify the tool call is not in the returned dict.

4. `test_stored_session_cwd_v3`: create a temp `session.json` with `{"workspacePaths": ["/some/path"]}` in a mock hash-dir structure; verify `_stored_session_cwd_v3` returns `"/some/path"`.

5. `test_stored_session_cwd_v3_rejects_path_traversal`: pass a session_id containing path separators (e.g., `"../../etc/passwd"`); verify `_stored_session_cwd_v3` returns `""` (regex rejects it).

6. `test_fulfill_token_success`: patch `subprocess.run` to return the known `get-kas-token` JSON; verify `_fulfill_token` writes `{"result": {"accessToken": ..., "profileArn": ..., ...}}` to the pipe.

7. `test_fulfill_token_subprocess_failure`: patch `subprocess.run` to raise `subprocess.TimeoutExpired`; verify `_fulfill_token` writes an error response (not raises), and that the error message does NOT contain any `accessToken` value.

8. `test_fulfill_token_malformed_json`: patch `subprocess.run` to return invalid JSON in stdout; verify `_fulfill_token` writes an error response with a fixed message (not the raw stdout).

9. `test_fulfill_token_nonzero_exit`: patch `subprocess.run` to return `returncode=1`; verify error response written.

10. `test_fulfill_token_calls_discard_on_write_failure`: patch `self._write` to raise `AcpError`; verify `_discard` is called.

11. `test_publish_live_union`: verify that `_supervisor._publish_live()` calls the hook with the union of v2 and v3 sessions.

12. `test_fulfill_token_called_twice`: patch `subprocess.run` to succeed twice; verify two separate `_fulfill_token` calls both complete without error (confirms re-entrant safety).

**`acp_page.test.mjs` additions**:

1. Add `ENGINE = "v2"` fixture injection in the test DOM setup so existing tests are unaffected.
2. Add `test_engine_v3_ws_path`: render with `ENGINE = "v3"`, verify the constructed WebSocket URL contains `/ws/acp-v3`.
3. Add `test_engine_v3_session_api_url`: verify session listing API URL uses `/api/acp-v3/sessions` when `ENGINE = "v3"`.

**Playwright verification** (browser interaction, not `acp_page.test.mjs`):

1. Navigate to `/acp-v3`. Verify page loads without JS errors.
2. Create a new session via the workspace picker. Verify a `sess_`-prefixed session ID appears in the URL.
3. Send a trivial prompt. Verify: agent chunk frames appear in the transcript, turn ends cleanly.
4. Reload the page. Verify: session history replays correctly.
5. Verify `/acp` still works unchanged after all changes.

**Memory update** (from doc-impact analysis): update `memory/MEMORY.md` entry at heading `### acp.py and presence.py may not import each other` (line ~209): append a note that `_publish_live` now emits the union of both supervisors' sessions, guarded by `_supervisor_v3 is not None`.

**Exit criteria**:
- [ ] 12 new pytest tests in `TestSupervisorV3` class; all pass
- [ ] `test_fulfill_token_malformed_json` and `test_fulfill_token_subprocess_failure` confirm error message does NOT contain any `accessToken` value
- [ ] `test_stored_session_cwd_v3_rejects_path_traversal` passes (regex gate confirmed)
- [ ] 3 new `acp_page.test.mjs` tests pass; all existing tests still pass
- [ ] Playwright: `/acp-v3` loads, session creates, prompt streams, reload replays — all observed without errors
- [ ] Playwright: `/acp` unchanged — existing session, prompt, reload still work
- [ ] `node tests/acp_page.test.mjs` passes
- [ ] `.venv-PowerAtlas\Scripts\pytest` passes
- [ ] `memory/MEMORY.md` `_publish_live` union note added

**Covers**: SC-2, SC-4, SC-8, SC-10

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| R1 — initialize blocks on auth token (AS-6) | High — `ensure_started()` times out, v3 unusable | Phase 0 probe establishes ordering before Phase 1 writes any code; `_fulfill_token` is async and dispatched with `_spawn_task` so it does not block the event loop |
| R2 — CLOSE_METHOD unknown on v3 (AS-5) | High — sweeper and close path fail silently | Phase 0 AS-5 probe; Phase 5 corrects the method if needed; sweeper tested in Phase 5 exit criteria |
| R3 — `_on_notification` v3 incompatibilities (AS-4) | Medium — unknown notification methods silently discarded | Phase 0 AS-4 probe logs all method names; `_on_notification` override in `_SupervisorV3` replaces `_emit` calls with `_emit_v3`; fall-through logs warnings for unknown methods |
| R4 — Path cache misses ACP-created sessions | Medium — diff recovery returns empty on first load | Phase 0 R4 probe; unconditional 200 ms retry in `_get_tool_diffs_v3` (not conditional on probe result) |
| R5 — Enterprise `profileArn` missing | Medium — `session/prompt` returns `-32000 profileArn is required` | `_fulfill_token` always includes `profileArn` and `provider`; probe confirmed availability |
| R6 — Dual-supervisor `_publish_live` race at startup | Low — `NoneType` AttributeError if `_supervisor_v3` not yet set | `_supervisor_v3 is not None` guard added; initialization inside `apply_config` before first request |
| R7 — v2/v3 session ID collision | Low — structurally impossible | v2 IDs are bare UUIDs (no `sess_` prefix); v3 IDs have `sess_` prefix; disjoint by construction |
| R8 — Token value in exception message | High — leaks `accessToken` in logs / wire | Separate `except json.JSONDecodeError` before general `except`; fixed strings in all error responses; no `exc` or `result.stdout` in messages |
| R9 — DNS rebinding / navigation guard bypass | High — attacker page triggers trust-all-tools agent spawn | `same_origin_guard` extended to include `_ACP_V3_PATH`; `_request_host_allowed` in `acp_v3_page`; `Cache-Control: no-store` prevents caching of token-bearing response |
| R10 — `shutdown()` subprocess leak | High — KAS v3 process leaks on PowerAtlas exit | Phase 1 adds `_supervisor_v3.shutdown()` to `shutdown()` with `is not None` guard |
| R11 — `_sweep_once` ignores v3 sessions | High — idle v3 sessions accumulate indefinitely | Phase 1 extends `_sweep_once` to iterate `_supervisor_v3.sessions`; Phase 5 verifies sweeper behavior |
| R12 — `_on_notification` records to wrong supervisor | High — v3 history corrupts v2 buffer; v3 replay empty | `_SupervisorV3._on_notification` overrides all `_emit` calls with `_emit_v3`; confirmed in Phase 2 exit criteria |

## 7) Verification

```powershell
# Python test suite
.venv-PowerAtlas\Scripts\pytest

# JS template tests
node tests/acp_page.test.mjs

# Smoke: /acp still works (no regression)
# Navigate to http://127.0.0.1:<port>/acp in browser — hard reload, create session, send prompt

# Smoke: /acp-v3 works
# Navigate to http://127.0.0.1:<port>/acp-v3 in browser
# Create session, send prompt, observe streamed turn
# Reload page, verify history replay
# Run session with fs_write or str_replace tool call, reload, expand diff row

# Isolation boundary check
python -c "import ast, sys; [print(n.module or n.names[0].name) for n in ast.walk(ast.parse(open('src/power_atlas/acp.py').read())) if isinstance(n, (ast.Import, ast.ImportFrom)) and getattr(n,'module','') not in ('','json','os','re','shutil','subprocess','threading','time','asyncio','collections','contextlib','dataclasses','logging','pathlib','typing','typing_extensions','win32api','win32con','win32job','pywintypes','starlette','fastapi') and getattr(n,'module','').startswith('power_atlas')]"
# Expected: only 'power_atlas.config' and 'power_atlas.launcher' (or their sub-names)
```

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `AGENTS.md` | Line 7: expand `/acp` + `acp.html` statement to include `/acp-v3` with `engine="v3"` | 3 |
| `docs/KNOWLEDGE.md` | Line ~121: add note about `_get_tool_diffs_v3` as v3 parallel to `get_tool_diffs` | 6 |
| `memory/MEMORY.md` | Line ~209 (`_publish_live` entry): note union behavior for dual supervisors | 7 |
| `memory/MEMORY.md` | Line ~244 (`_session/steer` availability): qualify as v2-only, pending v3 spike results | 7 |
| `plans/CLOSED_INVESTIGATIONS.md` | Line ~46 (`_kiro/auth/getAccessToken`): note `_SupervisorV3` answers it as a proper ACP client | 6 |
| `plans/ROADMAP.md` | Line ~175: add `/ws/acp-v3` to security scope; add post-spike follow-up items | 6 |
| `plans/tests/260701_POWERATLAS.md` | Lines ~153, ~351–353, ~582: extend remote-path enumeration and delete-endpoint test targets for v3 parallels | 7 |

## 9) Implementation Divergences from Plan
`<Reserved>`

## Follow-up Work (Deferred)

1. **Merge `/acp` and `/acp-v3`**. Once spike validates v3 parity, plan the merge of `_SupervisorV3` into `_Supervisor` (engine parameter or subclass retained). Source: Q6 decision (parallel supervisors for spike only).

2. **Linux cross-platform support for `_SupervisorV3`**. The pywin32 job-object teardown in `_spawn` is Windows-only. Source: Q8 decision.

3. **Production-grade liveness detection for v3 sessions**. Implement the approach recommended by Phase 5 probe. Source: Q11, R2.

4. **MCP OAuth surface in `/acp-v3`**. `_kiro/mcp/status` with `failedAuthorization: true` surfaces an OAuth URL — add the "Connect" prompt UI. Source: Q5 decision.

5. **`_kiro/spec/*` and `_kiro/workflow/*` notification handling**. Assess whether these v3-specific notifications should be surfaced in `/acp-v3`. Source: Q5 decision.

## Review Log

### 2026-08-19 — Plan Creation (via /qplan, high effort, 4 personas)

25 raw findings (after dedup across 4 personas). 17 auto-resolved; 8 escalated (all Low or already addressed in the resolution column below).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `_on_notification` is inherited and routes all `_emit` calls to `_supervisor.record()`, corrupting v2 history and leaving v3 history empty | Fixed — Phase 1 adds `_SupervisorV3._on_notification` override replacing `_emit` with `_emit_v3`; Phase 2 adds `_emit_v3`; both noted in Design Decisions |
| 2 | High | `shutdown()` only calls `_supervisor.shutdown()`; `_supervisor_v3` subprocess leaks on exit | Fixed — Phase 1 exit criterion requires `shutdown()` to call `_supervisor_v3.shutdown()` |
| 3 | High | `_sweep_once` hardcoded to `_supervisor`; v3 sessions never swept | Fixed — Phase 1 extends `_sweep_once` to iterate `_supervisor_v3.sessions`; Phase 5 verifies |
| 4 | High | `_publish_live` signature mismatch — plan union pseudocode passes one arg but actual signature takes two | Fixed — Phase 1 exit criterion: "read actual `_Supervisor._publish_live` signature before implementing; match it" |
| 5 | High | `new_session` not in the five listed overrides — calls v2 `_build_kas_session_params()` (no modeId); SC-1 never satisfied | Fixed — Phase 1 now lists `new_session` as a required override; Design Decisions table updated |
| 6 | High | `same_origin_guard` path check is `== _ACP_PATH` exactly; `/acp-v3` bypasses navigation guard entirely | Fixed — Phase 3 now requires extending the guard to include `_ACP_V3_PATH`; Risk R9 added |
| 7 | High | `_fulfill_token` exception message includes raw `exc`; malformed JSON parse may leak `accessToken` | Fixed — Phase 1 `_fulfill_token` code sample uses separate `except json.JSONDecodeError` with fixed message; general `except` also uses fixed string; Risk R8 added |
| 8 | High | Missing `_request_host_allowed` call in `acp_v3_page` — DNS rebinding leaks `_ACP_TOKEN` | Fixed — Phase 3 exit criterion requires `_request_host_allowed`; Phase 3 route spec updated |
| 9 | High | `_stored_session_cwd_v3` no `_SESSION_ID_RE` validation before path join — path traversal possible | Fixed — Phase 1 spec and exit criteria require `_SESSION_ID_RE.fullmatch` guard at top of function |
| 10 | High | `_fulfill_token` timeout leaves subprocess running in thread pool on Windows | Fixed — Phase 1 `_fulfill_token` code sample includes explicit `kill()` in `except TimeoutExpired` block |
| 11 | High | `_supervisor_v3` initialized at module level; `apply_config` rebinds ignore it | Fixed — Design Decisions updated; Phase 1 specifies init inside `apply_config` / lifespan |
| 12 | Medium | `_sweep_loop` early-exit guard is v2-only; zero v2 sessions with idle v3 sessions never sweeps | Fixed — Phase 1 exit criterion: "sweep loop guard includes v3" |
| 13 | Medium | `_fulfill_token` inherits full env including `POWER_ATLAS_SESSION`; should use `_build_child_env` | Fixed — Phase 1 `_fulfill_token` code sample uses `env=_build_child_env({})` |
| 14 | Medium | `/acp-v3` route missing CSP header and `Cache-Control: no-store` | Fixed — Phase 3 exit criteria enumerate both headers explicitly |
| 15 | Medium | `ws_acp_v3` token/origin check order ambiguous — must precede `accept()` | Fixed — Phase 3 specifies `_acp_token_ok` and `_ws_origin_ok` before `await ws.accept()` |
| 16 | Medium | `can_delete` context variable not specified for `acp_v3_page` | Fixed — Phase 3 route spec includes `can_delete` via same `_is_remote_peer`/`_is_mobile_ua` logic |
| 17 | Medium | `_fulfill_token` `AcpError` on write leaves KAS waiting indefinitely | Fixed — Phase 1 code sample calls `_discard` on write failure |
| 18 | Medium | `_supervisor_v3 = None` permanently on construction failure with no error log | Fixed — Phase 1 specifies `ERROR` log + explicit `None` assignment on failure |
| 19 | Medium | Multiple `getAccessToken` requests over session lifetime not tested | Fixed — Phase 7 adds `test_fulfill_token_called_twice` |
| 20 | Medium | `_registry.attach/.detach` call `_supervisor.touch_used()` unconditionally; v3 idle clock never reset | Fixed — Phase 1 exit criterion requires `attach`/`detach` to route `touch_used` to correct supervisor |
| 21 | Low | Phase 0 doesn't measure KAS v3 startup time vs timeout | Fixed — Phase 1 exit criterion adds startup time measurement and notes to raise timeout if >20 s |
| 22 | Low | `_get_tool_diffs_v3` retry conditional on Phase 4 probe — should be unconditional | Fixed — Phase 1 specifies unconditional 200 ms retry; Phase 4 conditional language removed |
| 23 | Low | `kiro-cli` binary not resolved via `shutil.which` for token fetch | Fixed — `_KIRO_V3_TOKEN_BINARY = shutil.which("kiro-cli")` at module level; used in `_fulfill_token` |
| 24 | Low | `_overlay_steering` placeholder content needs injection-safety note | Fixed — Phase 1 `_fulfill_token` comment notes field must not contain user-controlled strings |
| 25 | Low | Phase 5 sweeper exit criterion can't pass until H2 resolved | Fixed — Phase 1 resolves the sweeper gap; Phase 5 exit criterion cross-references it |

Health: **Green** (all High and Medium findings resolved; no unresolved findings remaining).

## Harness Improvement Opportunities

- `/qexplore` skill invocation as the first message skipped the mandatory session-tab-title steps — the self-check fired too late (after the skill's Step 1 was already executing). Cost: one extra user correction turn. Suggested change: move the self-check to before any skill SKILL.md loading, not after.



