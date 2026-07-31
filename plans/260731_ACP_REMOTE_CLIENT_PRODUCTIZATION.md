# ACP Remote Client Productization

> **Date**: 2026-07-31
> **Status**: Draft — review cycle 1 complete; 6 decisions outstanding before Phase 3 is implementable  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Promote the throwaway `/acp` prototype into a NetBird-reachable remote client that dispatches, drives and resumes kiro-cli sessions PowerAtlas creates, with a Zed-style session browser, an idle sweeper, and a security model that survives leaving loopback.
> **Estimated effort**: 6-9 days

---

## Intent

### Problem statement & desired outcomes

PowerAtlas can watch kiro-cli sessions but cannot drive them from anywhere except the machine they run on. The goal is the kiro-cli equivalent of what Claude Code already ships as remote control, scoped to devices on the user's NetBird network: open PowerAtlas from a phone, see existing sessions grouped by workspace, resume one or start a new one, and drive it to completion — including long unattended tasks — without a terminal.

The `260725_KIRO_CLI_ACP_CLIENT_PROTOTYPE` work proved the protocol end to end and was deliberately labelled throwaway on the grounds that it measured a chat surface while the roadmap item existed for unattended automation. That rationale is now retired: the chat surface **is** the product, and the four blockers gating a rebuild were resolved or knowingly accepted by the 2026-07-31 spikes (`plans/ROADMAP.md`). What remains is productization, not proving.

Desired outcomes:

- Reach PowerAtlas from a phone over NetBird and drive a kiro-cli session to completion.
- Find the session to resume without typing a Windows path on a touch keyboard.
- Leave a long agentic task running and come back to it, on either device.
- Do all of the above without exposing `custom_launchers[].env` credentials or the launcher execution routes to the 17 peers sharing the NetBird account.

### Success criteria

- **SC-1**: With the remote bind enabled, a phone on NetBird can load `/acp`, authenticate once, and drive a session — create, prompt, stream, cancel, close — with the laptop's dashboard untouched on loopback.
- **SC-2**: With the remote bind disabled (the default), behaviour is byte-identical to today: loopback only, no new listening surface.
- **SC-3**: A request arriving on the NetBird address for any path outside the remote allowlist is refused, **including WebSocket upgrades**, verified by a test that exercises the socket path separately from the HTTP path.
- **SC-4**: `/api/launchers`, `/api/settings` and `GET /` are unreachable from the NetBird address; reaching them requires loopback.
- **SC-5**: A device without the remote secret is refused on both HTTP and the WebSocket, even when it reaches the NetBird address.
- **SC-6**: The session browser lists workspaces with their sessions, defaulting to 10 groups expanded with 3 sessions each, both independently paginated, with a per-session availability indicator; sessions locked by a live foreign process are greyed and not loadable.
- **SC-7**: An ACP-owned session shows a live status dot in the dashboard for its whole lifetime, including more than 120 seconds after the agent process started and while idle between turns for more than 5 minutes.
- **SC-8**: A turn that streams for longer than the old 600 s ceiling completes rather than timing out; a turn that goes silent for the configured window fails and cancels agent-side.
- **SC-9**: A session idle beyond the TTL with no attached subscriber, no in-flight turn and no in-flight close is terminated and its `.lock` removed; a session with an attached tab or a running turn is never swept regardless of age.
- **SC-10**: `MAX_SESSIONS` is read from configuration rather than a module constant, defaults to 8, and is not read from disk on the event loop.
- **SC-11**: The full suite passes: `pytest` green, and `node tests/acp_page.test.mjs` green against the reworked page.
- **SC-12**: README's three deliberately-incomplete sites are completed, per `AGENTS.md:7`'s exemption ending on promotion.

### Scope boundaries & non-goals

**In scope**: `/acp` page rework including the session browser; a purpose-built read-only listing endpoint; the remote bind and its allowlist; the device secret and cookie; the presence skew and listing-cache fixes; `last_used` plus the idle sweeper; the inactivity ceiling replacing the wall-clock prompt timeout; `MAX_SESSIONS` config plumbing; README.

**Explicitly out of scope**:

- **The permission/policy engine.** Ships with `-a`, knowingly. Reopens if unattended scheduling without a human is wanted.
- **Attaching to sessions live in a terminal.** Structurally impossible over ACP — `kiro-cli chat` is itself an ACP wrapper and its grandchild holds the lock.
- **The Claude Code ACP half.** Never started; needs a separate npm bridge.
- **Redacting or encrypting `custom_launchers[].env`.** Knowingly declined (D4) so values stay readable in the WebUI; the loopback split is the sole protection.
- **TLS.** WireGuard already encrypts the transport. Reopens only if a real LAN interface is bound alongside NetBird.
- **`tests/conftest.py` and the 18 known config-leaking tests.** Pre-existing hazard (`memory/MEMORY.md:95-97`), recorded not fixed.
- **The `session-tab-title.md` steering rework.** Deprioritised; session titles will render as raw first prompts.
- **v3 and classic session stores.** 23 + 3 sessions stay invisible; ACP is v2-only.

---

## 1) Current State

**`acp.py` (2711 lines) works and is kept.** One supervised `kiro-cli acp -a` process holds N sessions; a daemon reader thread parses NDJSON off stdout and posts to the loop via `call_soon_threadsafe` (`acp.py:1597`); a Windows job object with `KILL_ON_JOB_CLOSE` guarantees teardown (`acp.py:1293-1316`). Session state (`sessions`, `history`, `inflight`, `closing`, `_reserved`) is loop-owned and unlocked by design, an invariant held by every mutation running on the loop.

**Coupling to `web.py` is three call sites and no shared state**: a guarded import (`web.py:41-49`, failure degrades to `acp = None`), `acp.shutdown()` in `lifespan`'s nested finally (`:526-530`), and `acp.serve_socket(ws)` on an already-accepted socket (`:902-909`). `acp → web` is nil. `acp.py` imports exactly two package names — `config.CONFIG_DIR` (`:70`) and `launcher._SESSION_ID_RE` (`:71`) — declared as a module-docstring invariant (`acp.py:15-28`).

**Security is positional, not authenticated.** Zero `Cookie`/`Authorization`/`HTTPBasic`/`ssl_` references in `src/`. `_ALLOWED_HOSTS` is loopback-only (`web.py:558`), enforced at three points: `same_origin_guard` on all methods (`:712`), an inline re-check in `acp_page` (`:855`), and `_ws_origin_ok` on the socket (`:782`). uvicorn is pinned to `127.0.0.1` at **two** sites (`__main__.py:308` and `:328`, the latter being the random-port fallback). `_ACP_TOKEN`'s own comment (`web.py:727-731`) states it "is not a boundary".

**The middleware structurally cannot see WebSocket upgrades** — `BaseHTTPMiddleware.__call__` returns early on non-`http` scope (`web.py:752-755`), which is why `_ws_origin_ok` is documented as "the mandatory first line of *every* WebSocket route in this module".

**Presence has two independent defects making ACP sessions read `closed`.** `presence._scan` accepts a sidecar only when `-5.0 ≤ started_at − agent_create_time ≤ 120.0` (`presence.py:466-468`, constants `:93`/`:97`). Measured 2026-07-31: an ACP lock's `started_at` tracks **session open time**, not process start (probe: agent create 1785529958.899; lock1 +1.88 s; lock2 +23.77 s for sessions 21.5 s apart; lock pid == agent pid). A long-lived agent therefore fails the window for every session opened past 120 s. Separately, `_list_sidecars` caches the lock-directory listing on directory mtime with `cache_listing=True` (`presence.py:142-182`), justified by a write-once premise that `session/load`'s in-place lock rewrite falsifies, so a stale listing pins a stale parse (`:121-123`).

**No config value reaches `acp.py`.** `MAX_SESSIONS = 3` (`acp.py:260`) is a module constant read at call time in `at_capacity()` (`:1755`) and `_session_limit_message()` (`:1066`). `load_config()` has **no cache** — a full TOML parse plus sanitisation per call (`config.py:139-268`) — and is already called synchronously on the loop from ~16 routes.

**`PROMPT_TIMEOUT_SECONDS = 600.0`** (`acp.py:275`, used `:1895`) bounds a turn on wall clock. On timeout `_request` pops the future and raises without sending cancellation, so the agent keeps working while `_handle_prompt`'s finally clears `inflight` — the session reads idle while actively running.

**No last-activity timestamp exists.** The session record is exactly `{"cwd", "created"}` (`acp.py:1795-1798`, `:1846-1849`); `created` is written once and never read.

**Frontend has no build step and no breakpoints.** `base.html` is the only skeleton and carries the viewport meta (`:5`). CSP with a per-response nonce applies to `/acp` only (`web.py:811-840`); the page has exactly one `<script>` and a strict no-`innerHTML` rule, both enforced by `tests/acp_page.test.mjs`. `style.css` has exactly one `@media` rule — `prefers-reduced-motion` (`:175`) — so there are **zero width breakpoints**. `html, body { height: 100%; overflow: hidden }` (`:2`) makes this an app shell.

**Store shape, measured 2026-07-31**: 5,941 `.json`; 4,734 sub-agent sessions filtered by the four `parent_session_id` guards; **1,207 user-facing sessions across 62 workspaces**; median 2 per workspace, max 208, top six ≈80%; **841 carry a `.lock`**, nearly all stale.

**Test suite**: `tests/test_web.py` is 8592 lines, 496 definitions collecting to **684 tests**; `TestAcp*` is 40 classes / 189 definitions / **250 collected**, white-box with no seam (108 refs to `_Supervisor`, 98 to `_supervisor`, 59 to `_registry`, 32 to `_Connection`, the six `_handle_*` coroutines called directly). ~153 host/WebSocket tests cover `web.py` helpers and encode loopback deliberately. No `conftest.py`, no `pytest.ini`, **no CI**.

## 2) Goal

Bind PowerAtlas to the NetBird interface behind a default-deny path allowlist and a device cookie, rework `/acp` into a two-pane session browser plus conversation that works on a phone, make ACP sessions visible in the dashboard, and make long unattended turns survive by replacing the wall-clock prompt ceiling with an inactivity ceiling that also drives an idle sweeper.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| D1 `acp.py` disposition | Keep and harden | Rebuild per the 2026-07-26 roadmap verdict | The module a rebuild would discard needs the least change; the 250 white-box ACP tests have no seam to re-point; the "throwaway" verdict rested on the prototype answering the wrong question, which the product decision retired |
| D2 Remote transport | NetBird only; `_ALLOWED_HOSTS` widened to the NetBird **IP** | Real LAN interface; `0.0.0.0` | WireGuard gives confidentiality and a stable non-single-label address. `web.py:553-557` warns a single-label name is hijackable via LLMNR/NBT-NS/mDNS, so the FQDN is not allowlisted |
| D3 Authorization | NetBird ACL (primary) **plus** an independent device secret exchanged for a long-lived cookie | ACL alone | Bind, Host allowlist, ACL reachability and `_ACP_TOKEN` all fail together on a misconfigured policy. The account has 17 peers administered by others. The cookie is the only control that survives policy drift |
| D4 `custom_launchers[].env` | Left readable; no redaction, no at-rest encryption | Redact from read paths; DPAPI at rest | **User override** — recommended redaction, chose reliance on the loopback split so values stay readable in the WebUI. Measured input: PyCharm stores run-config env vars in plain text (`.idea/workspace.xml`), so "PyCharm's level" was already the status quo |
| D5 TLS | None | Self-signed; mkcert | WireGuard already encrypts the transport; TLS would encrypt inside an encrypted tunnel and adds nothing for authorization. Reopens only if a real LAN interface is bound |
| D6 Remote path exposure | Default-deny allowlist: `/acp`, `/ws/acp`, `/static/*`, one listing endpoint | Denylist; full app behind auth | A denylist over ~40 routes leaks by default on the next route added. Default-deny makes new routes loopback-only until deliberately exposed |
| D7 Allowlist enforcement points | **Two**: `same_origin_guard` for HTTP **and** inside the `/ws/acp` handler | Middleware alone | `BaseHTTPMiddleware` cannot see WebSocket upgrades (`web.py:752-755`); middleware-only would exempt the one route reaching `-a` |
| D8 Secret storage | Own file `CONFIG_DIR/remote-secret`, generated on first enable | In `config.toml` + a new `tests/conftest.py` | Keeps a credential out of `config.toml`'s sanitise-and-whole-file-rewrite path and out of the blast radius of 18 known config-leaking tests; avoids a new test file `AGENTS.md:8` would require the user to request |
| D9 Presence fix location | Fix `presence.py`'s own heuristic; no coupling either direction | `acp → presence`; `presence → _supervisor` | Preserves `acp.py`'s import invariant (`:15-28`) and avoids reading loop-owned state from presence's worker threads |
| D10 Skew check repair | Drop the upper bound at `presence.py:467`, keep the `-5 s` lower bound | Widen 120 s → hours; special-case the agent pid | A recycled pid writes its lock *before* the current process started, so the lower bound alone still rejects it. Widening only postpones the failure |
| D11 Turn ceiling | Inactivity ceiling of 15 min silence, reset by each `session/update` | Raise the wall clock; remove the bound | Preserves the ceiling's actual purpose (detect a stopped agent) without capping legitimate long turns. Process death is separately detected by the reader thread's finally (`acp.py:1722`) |
| D12 Idle signal | Stamp `last_used` from the same `session/update` stream | Stamp at turn start; stamp at turn end | Start-stamping makes a 45-min turn look 45 min idle the instant it ends; notification-stamping makes a working session never-idle by construction |
| D13 Sweeper close path | Call `_supervisor.close_session` directly and broadcast `session_closed`; do **not** relax `_handle_close` | Relax the `not_subscribed` guard | That guard protects a real case ("a socket not watching a session has no business releasing what another tab holds", `acp.py:2658-2665`); the sweeper has no socket and should not weaken it |
| D14 Sweeper ownership | `acp.py` owns the task; `web.py` `lifespan` starts/stops it | `web.py` owns it, like `_background_refresh` | Keeps ACP lifecycle policy inside `acp.py`; grows the `web → acp` surface from 3 call sites to 4, the smallest available increase |
| D15 `MAX_SESSIONS` plumbing | Read from config **once at startup**, injected into `acp`; default 8 | `load_config()` inside `at_capacity()` | `at_capacity()` runs on the loop and `load_config()` is an uncached full TOML parse; reading it there reproduces the exact stall `_handle_new` already threads out to avoid |
| D16 Session browser shape | Workspace groups; 10 groups default with show-more; groups expanded showing 3 sessions with per-group paging | Flat recency list; collapsed groups | User decision. 10×3 ≈ 30 visible rows also bounds the per-row lock check to ~30, not 1,207 |
| D17 Availability indicator | Three states — available / held-by-PowerAtlas / locked-elsewhere; lazy per visible row, off-loop, **fails open** | Two states; no indicator | A wrongly-greyed session is unreachable from the UI; a wrongly-available one gets the typed in-use refusal at load. 841 of 1,207 sessions carry a lock, nearly all stale, so "has a lock" alone is useless |
| D18 Listing endpoint | Purpose-built, read-only, no env, no actions | Reuse `/partials/all-sessions` | `partials/session_row.html` is hover-driven (`:3`) and carries the launch-action cluster — dashboard markup that is useless and undesirable on a phone. A narrow route is also auditable against the allowlist |
| D19 Listing pagination | Server-side paging per group and across groups; do not inherit the existing filter behaviour | Reuse `get_all_sessions_paginated` verbatim | *Resolved deterministically*: all four existing filters set `has_more = False` (`web.py:1301`, `:1318`, `:1329`, `:1340`), i.e. filtering operates on the loaded page only. Inheriting that would silently truncate a 208-session workspace |
| D20 UI reference | `kiro-ui` (Apache 2.0) as design reference only | Adopt its code | Its Express/React/Vite/Electron stack is incompatible with PowerAtlas's zero-build-step Jinja + htmx frontend; adopting it means rebuilding the dashboard |
| D21 Remote bind default | Off; absent config means loopback-only | On once configured | A version bump must never silently start listening on NetBird |
| D22 Secret delivery | URL + secret as copyable text in settings | QR code | A QR needs a new dependency for convenience only; deferred |

*D1-D18 and D20-D22 carry forward from `/qexplore`'s resolved decisions and its assumptions ledger (surfaced and un-vetoed at the exploration assumptions checkpoint). D19 is a deterministic open item resolved here by planner judgment.*

## 4) External Dependencies & Costs

### Required external changes

| Category | Change needed | Owner | Status |
|---|---|---|---|
| DNS / Networking | NetBird access-control policy restricting `100.78.142.124` to the user's own device group rather than all 17 account peers | User | **Pending — gates Phase 3** |
| Secrets / Env vars | `CONFIG_DIR/remote-secret` generated on first enable; user copies it to each device once | Automatic + user | Pending |
| Third-party services | kiro-cli must still expose `_kiro.dev/session/terminate` (undocumented private extension) at implementation time | External | Verify in Phase 0 |

### Cost impact

**None.** All compute is local; no hosting, no API volume change, no new dependencies, no new bundled artifacts. Memory ceiling rises from `~306 + 151×3 ≈ 759 MB` to `~306 + 151×8 ≈ 1.5 GB` worst case at the new `MAX_SESSIONS` default, offset by the sweeper reclaiming idle sessions.

## 5) Implementation Phases

### Phase 0: Pre-flight — verify the premises the design rests on
**Goal**: Establish the empirical state two later phases assume, before committing to them.
**File scope**: none (verification only; findings recorded in this file).
**Covers**: SC-11 (baseline)

**Why this phase exists**: Phase 3's entire authorization model assumes the NetBird policy restricts the instance to the user's devices — unverifiable from the repo, administered by others, on a 17-peer account. Phase 2's sweeper assumes `_kiro.dev/session/terminate` still exists on the installed kiro-cli, which self-updates and has regressed a measured behaviour before.

1. **NetBird policy** — confirm in the NetBird console that a policy restricts access to this peer to the user's own device group. Record the policy name and the groups it admits. **If no such policy exists, Phase 3 does not start** until one does.
2. **Re-verify terminate** — run the scratch probe against the installed kiro-cli: `_kiro.dev/session/terminate` returns `{}`, frees processes, and removes the `.lock`. Record the version.
3. **Capture green baselines** — `pytest` (full), and `node tests/acp_page.test.mjs`. Record counts; `tests/test_data.py` has ~8 known timing-flaky tests (`memory/MEMORY.md:89-93`), so re-run standalone before attributing a failure.

**Exit criteria**:
- [ ] NetBird policy verified and recorded, or Phase 3 explicitly blocked with the reason
- [ ] `_kiro.dev/session/terminate` re-verified on the installed version, with the version recorded
- [ ] Full `pytest` baseline recorded (count + any pre-existing failures)
- [ ] `node tests/acp_page.test.mjs` baseline recorded

---

### Phase 1: Presence — make ACP sessions visible in the dashboard [QA]
**Goal**: Fix both mechanisms that make an ACP-owned session read `closed`, without coupling `presence` and `acp`.
**File scope**: `src/power_atlas/presence.py`, `tests/test_web.py`
**Covers**: SC-7

**Preservation constraints** (this phase is bugfix-shaped; these must not regress):
- A stale lock naming a dead or recycled pid must still read as not-live.
- A terminal-created kiro-cli session must still read as live exactly as today.
- `Snapshot`'s constructor signature must not change (every construction site across `tests/test_data.py` and `tests/test_web.py` passes the current signature verbatim).

**Change 1 — drop the skew upper bound** (`presence.py:466-468`):

```python
# Was: reject when the sidecar's timestamp is far from the process's start.
# The upper bound was calibrated for a terminal session, whose sidecar lands
# ~1.1-1.6 s after spawn. PowerAtlas's ACP agent is spawned once and serves
# sessions for the app's lifetime, so its locks are legitimately minutes or
# hours newer than the process — measured 2026-07-31, +1.88 s and +23.77 s for
# two sessions 21.5 s apart. The lower bound alone still does the job the check
# exists for: a recycled pid's lock was written BEFORE the current process
# started, so its delta is negative and is rejected here.
delta = started - live[1]
if delta < -_SIDECAR_BACKWARD_SKEW_S:
    continue
```

Retire `_SIDECAR_SKEW_S` (`presence.py:93`) or leave it unused with a comment naming this decision.

**Change 2 — stop caching the lock-directory listing.** `_list_sidecars(_KIRO_LOCK_DIR, ".lock")` is called with `cache_listing=True` (`presence.py:207`, default `:143`). Its docstring's premise — sidecars are written once and never rewritten — is falsified by `session/load` rewriting a lock in place, which leaves directory mtime untouched so `_load_json_cached` matches a stale `(mtime, size)` and returns the old parse (`:121-123`). Pass `cache_listing=False` for the kiro lock directory and update the docstring to record why the premise does not hold.

**Exit criteria**:
- [ ] A lock whose `started_at` is hours after its live holder's start reads as live
- [ ] A lock whose `started_at` precedes its (recycled) pid's start still reads as not-live
- [ ] An in-place lock rewrite is observed on the next scan rather than pinned to the previous parse
- [ ] `presence.py`'s docstrings no longer assert the falsified write-once premise
- [ ] Tests added to `tests/test_web.py` covering both changes; full suite green

---

### Phase 2: Session lifecycle — long turns survive, idle sessions are reclaimed [QA]
**Goal**: Replace the wall-clock turn ceiling with an inactivity ceiling, stamp activity, sweep idle sessions, and make `MAX_SESSIONS` configurable.
**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/config.py`, `src/power_atlas/web.py` (lifespan hook only), `tests/test_web.py`
**Covers**: SC-8, SC-9, SC-10

**Change 1 — stamp `last_used` on every `session/update`.** `_note_context` (`acp.py:1955-1969`) already writes into the session record on a notification, so the hook exists. Extend `_on_notification` (`:1669`) to stamp `sessions[sid]["last_used"] = time.monotonic()` for every update belonging to a known session. This breaks `TestAcpSessionRecordHoldsNoDeadState` (`tests/test_web.py:5033`, `:5044`), which asserts the key set is exactly `{"cwd", "created"}` — update it to `{"cwd", "created", "last_used"}`.

Use `time.monotonic()`, not `time.time()`: the sweeper compares elapsed intervals and must not be moved by a clock adjustment.

**Change 2 — inactivity ceiling.** `PROMPT_TIMEOUT_SECONDS = 600.0` (`acp.py:275`) currently bounds a turn on wall clock via `_request(..., timeout=PROMPT_TIMEOUT_SECONDS)` (`:1895`). Replace with a deadline reset by activity: the prompt future fails only after `ACP_PROMPT_SILENCE_SECONDS` (default 900) elapses with **no** `session/update` for that session. On timeout, send `session/cancel` before raising, so the agent stops rather than working orphaned.

Keep `REQUEST_TIMEOUT_SECONDS = 90.0` unchanged for ordinary requests — only the prompt path changes.

**Change 3 — the idle sweeper.** A task owned by `acp.py`, started from `web.py`'s `lifespan` (the 4th `web → acp` call site) and stopped by the existing `acp.shutdown()`. Every minute, for each session, sweep when **all** hold:

- `now - last_used > ACP_IDLE_TTL_SECONDS` (default 1800)
- `not _registry.subscribers.get(sid)` — an attached tab means leave it alone regardless of age
- `sid not in _supervisor.inflight`
- `sid not in _supervisor.closing`

Sweeping calls `_supervisor.close_session(sid)` directly and then broadcasts `session_closed` to any subscriber and detaches them, reproducing `_handle_close`'s notification half (`acp.py:2708-2711`) **without** relaxing its `not_subscribed` guard. Failure is caught, logged at WARNING, and never propagates — if `_kiro.dev/session/terminate` disappears, the sweeper degrades to memory growth rather than a crashed background task.

**Change 4 — `MAX_SESSIONS` from config.** Add `acp_max_sessions: int = 8` to `Config` (`config.py:51`), plus `acp_idle_ttl_seconds: int = 1800` and `acp_prompt_silence_seconds: int = 900`. Read **once at startup** in `__main__._run_foreground` (where `load_config()` is already called, `:292`) and inject into `acp` via a setter. Do **not** call `load_config()` from `at_capacity()` — it runs on the loop and `load_config` is an uncached full TOML parse (`config.py:139-268`). Add the three keys to `_SETTING_TYPES` (`web.py:1857`) with range validation.

`TestAcpSessionCapMessage` (`tests/test_web.py:4030`) asserts the literal `"254 mb"`; the measured figure is now ~150 MB marginal. Update both the message (`acp.py:1055-1068`) and the assertion.

**Exit criteria**:
- [ ] A turn streaming past 600 s completes rather than raising `AgentTimeout`
- [ ] A turn silent past the configured window fails **and** `session/cancel` is sent
- [ ] `last_used` advances on every `session/update`, verified for chunk, tool_call and tool_call_update
- [ ] An idle session past TTL with no subscriber, no in-flight turn and no in-flight close is terminated and its `.lock` removed
- [ ] A session with an attached subscriber is never swept regardless of age
- [ ] A session mid-turn is never swept regardless of age
- [ ] A sweeper failure is logged and does not kill the task or the app
- [ ] `MAX_SESSIONS` defaults to 8, comes from config, and `at_capacity()` performs no disk I/O
- [ ] `TestAcpSessionRecordHoldsNoDeadState` and `TestAcpSessionCapMessage` updated to match the new record shape and figure

---

### Phase 3: Remote access — bind, allowlist, and the device cookie [QA]
**Goal**: Make PowerAtlas reachable on the NetBird interface with a default-deny path allowlist enforced on both HTTP and WebSocket, behind a device secret.
**File scope**: `src/power_atlas/web.py`, `src/power_atlas/__main__.py`, `src/power_atlas/config.py`, `tests/test_web.py`
**Covers**: SC-1, SC-2, SC-3, SC-4, SC-5
**Blocked by**: Phase 0's NetBird policy verification.

**Change 1 — bind configuration.** Add `remote_bind_address: str = ""` to `Config`. Empty means loopback-only (D21). Both `uvicorn.Config` sites must read it — `__main__.py:308` **and** `:328`, the random-port fallback — or a port collision silently reverts to loopback.

**Change 2 — widen the Host allowlist to the configured IP.** `_ALLOWED_HOSTS` (`web.py:558`) becomes loopback plus the configured bind address when set. Allowlist the **IP**, never the FQDN: `web.py:553-557` records that a single-label name is answerable by whoever wins LLMNR/NBT-NS/mDNS. `_host_allowed`'s parser (`:570-620`) is already host-agnostic except its final membership test, so only that line changes.

**Change 3 — the default-deny remote allowlist, enforced twice** (D7):

```python
# HTTP: inside same_origin_guard, after the Host check.
if _is_remote_host(request) and not _remote_path_allowed(request.url.path):
    return JSONResponse({"error": "Forbidden"}, status_code=403)
```

```python
# WebSocket: inside the /ws/acp handler, beside _ws_origin_ok.
# The middleware CANNOT see upgrades (web.py:752-755), so this is not
# redundant - it is the socket's only exposure to the split.
if _is_remote_host_ws(ws) and not _remote_path_allowed("/ws/acp"):
    await ws.close(code=1008)
    return
```

The allowlist is exactly `/acp`, `/ws/acp`, `/static/*` and the Phase 4 listing endpoint. Everything else — including any route added later — is loopback-only by default.

**Change 4 — the device secret and cookie.** Generate `CONFIG_DIR/remote-secret` (`secrets.token_urlsafe(32)`) on first enable (D8), restrictive permissions where the platform supports it. A remote request without a valid cookie is redirected to a minimal exchange page; posting the correct secret sets a long-lived `HttpOnly`, `SameSite=Strict` cookie. The check runs in the same two places as the allowlist, for the same reason. Loopback requests bypass it entirely, so today's desktop workflow is untouched.

**Change 5 — the ~153 loopback-encoding tests get re-decided, not re-pointed.** `_HOSTILE_HOSTS` (`tests/test_web.py:1495`) deliberately includes single-label names and `localhost.evil.com`; both client fixtures pin `base_url="http://127.0.0.1"` with a docstring saying `testserver` "must not be allowlisted just to make this suite pass" (`:26-29`). Each affected test needs a decision about what it now asserts, not a mechanical edit. Add new cases for: remote Host + allowlisted path + valid cookie → allowed; remote Host + non-allowlisted path → 403; remote Host + no cookie → refused; **remote Host on `/ws/acp` without a cookie → closed**, exercised through the socket path rather than HTTP (SC-3).

**Change 6 — settings surface.** Show the bind address and the secret as copyable text (D22), plus the reachable URL. Correct the stale middleware rationale at `web.py:709-711`, which asserts a non-loopback Host "cannot arise legitimately".

**Exit criteria**:
- [ ] Remote bind disabled by default; with it unset, no non-loopback listener exists and behaviour is unchanged
- [ ] Both `uvicorn.Config` sites read the same configured address
- [ ] `_ALLOWED_HOSTS` admits the configured IP and still rejects single-label and lookalike hosts
- [ ] A remote request to a non-allowlisted path is 403, verified for `/`, `/api/launchers`, `/api/settings`
- [ ] A remote WebSocket upgrade to `/ws/acp` without a cookie is closed, verified through the socket path
- [ ] A remote request with no cookie cannot reach `/acp`; with a valid cookie it can
- [ ] Loopback requests need no cookie and are unaffected
- [ ] `web.py:709-711`'s rationale updated to match reality
- [ ] Settings surface shows the reachable URL and secret; full suite green

---

### Phase 4: Listing endpoint — dispatch targets without the dashboard [QA]
**Goal**: A purpose-built read-only endpoint serving workspace-grouped sessions for the browser, safe to expose remotely.
**File scope**: `src/power_atlas/web.py`, `tests/test_web.py`
**Covers**: SC-6 (data half)

Serves workspace groups with their sessions, paginated **independently at both levels** (D19) — the existing listing filters all set `has_more = False` (`web.py:1301`, `:1318`, `:1329`, `:1340`), which would silently truncate the 208-session workspace. Returns only: workspace path and display name, session id, title, updated timestamp, and availability state. **No `env`, no launcher data, no action affordances.**

Availability is the three-state field from D17 — `available` / `held` / `locked` — computed **only for the rows in the response** (~30 by default, not 1,207), off the event loop, and failing open to `available` on any error. `held` is free from `_supervisor.sessions`; `locked` reuses the pid-liveness logic `acp._lock_holder` already implements (`acp.py:956-1012`).

Reuses `data.discover_workspaces_with_counts` (`data.py:189`) and `data_kiro.load_sessions`, inheriting the `parent_session_id` filtering that removes 4,734 sub-agent sessions.

**Exit criteria**:
- [ ] Endpoint returns workspace-grouped sessions with independent paging at both levels
- [ ] A 208-session workspace pages correctly rather than truncating
- [ ] Response contains no `env`, no launcher fields, no action affordances
- [ ] Availability is computed only for returned rows and never for the whole store
- [ ] Availability computation runs off the event loop and fails open to `available`
- [ ] Sub-agent sessions are absent
- [ ] Endpoint is on the remote allowlist and reachable with a cookie, refused without

---

### Phase 5: `/acp` rework — two-pane browser and conversation [QA]
**Goal**: Replace the single-pane prototype page with a searchable workspace-grouped session rail plus the conversation, usable on a phone.
**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/static/style.css`, `tests/acp_page.test.mjs`, `tests/test_web.py`
**Covers**: SC-6 (UI half), SC-1

Left rail: search, workspace groups, **10 groups shown with show-more**, groups **expanded** showing **3 sessions each with per-group paging** (D16). Availability rendered per D17, with `locked` greyed and non-interactive. Right pane: the existing conversation, composer pinned.

Responsive without a build step: two-pane above a breakpoint, drill-down below it (rail → conversation with a back affordance), since `style.css` has zero width breakpoints today and `html, body { height: 100%; overflow: hidden }` (`:2`) makes this an app shell. Use `100dvh` rather than `100%` for the shell height so mobile browser chrome collapsing does not clip the composer.

**Constraints that are not negotiable here**: `/acp` is the only page under CSP (`web.py:811-840`) with a per-response nonce, exactly one `<script>`, and a strict no-`innerHTML` rule. `tests/acp_page.test.mjs` enforces all three — its DOM stand-in makes `innerHTML`/`outerHTML`/`insertAdjacentHTML` throw. Any added script tag, external stylesheet or inline handler violates the policy. The harness must be extended alongside the markup, and it is **not** run by pytest or CI (`AGENTS.md:9`), so it must be run by hand.

**Exit criteria**:
- [ ] Rail lists workspace groups with search, 10 groups + show-more, 3 sessions + per-group paging
- [ ] Availability indicator renders three states; `locked` is greyed and cannot be loaded
- [ ] Selecting a session loads and streams it; creating a new session still works
- [ ] Two-pane above the breakpoint, drill-down below it, verified at ~390 px
- [ ] Composer remains reachable with a soft keyboard open
- [ ] Still exactly one `<script>`, still nonce-carrying, still no `innerHTML`
- [ ] `node tests/acp_page.test.mjs` extended for the new behaviour and green
- [ ] Full pytest suite green

---

### Phase 6: Documentation
**Goal**: Complete the README sites the throwaway exemption deferred, and correct the stale roadmap claim.
**File scope**: `README.md`, `plans/ROADMAP.md`
**Covers**: SC-12

`AGENTS.md:7`'s exemption ends on promotion — "promoting it to product is what makes the README row required work" — and the prototype left three sites deliberately incomplete: the product definition (`README.md:3`), the "click to open the dashboard UI" line (`:30`), and the feature list (`:32-53`). Document remote access, how to enable it, and the security expectation that the NetBird policy is the primary authorization layer.

`plans/ROADMAP.md:62` still describes `same_origin_guard` as POST-only; that has been false since the prototype widened it.

**Exit criteria**:
- [ ] README's three sites describe the remote client and how to enable it
- [ ] README states the NetBird policy is the primary authorization layer and the cookie the second
- [ ] `plans/ROADMAP.md:62` corrected
- [ ] The ACP roadmap entry reflects the shipped shape

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Allowlist applied only in middleware leaves `/ws/acp` — the route reaching `-a` — exempt | Critical: arbitrary command execution from any reachable peer | D7 mandates enforcement in both places; SC-3 tests the socket path separately |
| NetBird account has 17 peers; reachability is not authorization | High: a colleague's device reaches the instance | Phase 0 gates Phase 3 on policy verification; the cookie survives policy drift |
| `-a` behind a remote surface is arbitrary command execution as the user | High, accepted knowingly | Two independent layers (policy + cookie); default-deny allowlist; bind off by default |
| Random-port fallback reverts to loopback if only `__main__.py:308` changes | Medium: silent loss of remote access, or worse, a silent bind mismatch | Explicit exit criterion that both sites read one value |
| `custom_launchers[].env` remains readable | Medium, accepted (D4) | Loopback-only routing is the sole protection; no remote-reachable route returns it |
| Widening `_ALLOWED_HOSTS` to a hostname re-opens LLMNR/mDNS hijack | High | D2 allowlists the IP only; existing single-label rejection tests retained |
| `_kiro.dev/session/terminate` is undocumented with no fallback | Medium: sweeper stops reclaiming | Phase 0 re-verifies; sweeper failure is non-fatal and logged |
| The 250 white-box ACP tests break on any structural change | Medium: churn | D1 keeps the module; Phase 2's changes are additive except the two by-design test edits |
| Clock skew stamping a lock in the future is no longer rejected once the upper bound is dropped | Low: a stale lock reads as live | Accepted; recorded in D10 |
| Every mutating route is a lost-update race (`load_config` → mutate → `save_config`) | Medium, pre-existing; a second concurrent client makes it likelier | Add no remote-reachable write routes; the allowlist enforces this structurally |
| 18 tests read the real `config.toml` holding real credentials | Medium, pre-existing | D8 keeps the new secret out of that file, adding nothing to the blast radius |
| `tests/acp_page.test.mjs` is outside pytest and CI | Medium: a page regression ships silently | Phase 5 exit criteria require running it by hand |
| kiro-cli self-updates and has regressed a measured behaviour before | Medium | Phase 0 re-verification; version recorded |

## 7) Verification

**Automated**:
- `.venv-PowerAtlas\Scripts\python -m pytest` — full suite, green at every phase boundary.
- `node tests/acp_page.test.mjs` — manual invocation, required at Phase 5 and before completion (`AGENTS.md:9`).
- `tests/test_data.py` has ~8 known timing-flaky tests (`memory/MEMORY.md:89-93`) — re-run standalone before attributing a failure.

**Manual**:
- From the laptop on loopback: dashboard unchanged; launchers, settings and env still readable (D4).
- From the phone over NetBird: `/acp` reachable after entering the secret once; session rail lists workspaces; resume a session; drive a turn to completion; `/api/launchers` and `/` refused.
- From a second NetBird peer **without** the secret: refused on both `/acp` and `/ws/acp`.
- Long-turn check: a task streaming past 10 minutes completes rather than timing out.
- Sweeper check: a session idle past TTL with no tab open is terminated and its `.lock` removed; one with a tab open is not.
- Dashboard check: an ACP session created more than 2 minutes after the agent started shows a live dot, and still does after 5 minutes idle.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Product definition, dashboard-open line, and feature list to cover remote access and how to enable it (`AGENTS.md:7` exemption ends on promotion) | 6 |
| `plans/ROADMAP.md` | Correct the stale POST-only claim at `:62`; update the ACP entry to the shipped shape | 6 |
| `src/power_atlas/presence.py` docstrings | Remove the falsified write-once premise and the retired skew rationale | 1 |
| `src/power_atlas/acp.py` module comments | Version-pinned figures (`~254 MB`, `~5.4 s`, `2.14.1`) superseded by the 2026-07-31 measurements | 2 |
| `src/power_atlas/web.py:709-711` | Middleware rationale asserting a non-loopback Host "cannot arise legitimately" | 3 |

## Progress Tracker

| # | Phase/Task | Status | Notes |
|---|---|---|---|
| 0 | Pre-flight verification | Not started | Gates Phase 3 |
| 1 | Presence fixes | Not started | |
| 2 | Session lifecycle | Not started | |
| 3 | Remote access | Not started | Blocked by Phase 0 |
| 4 | Listing endpoint | Not started | |
| 5 | `/acp` rework | Not started | |
| 6 | Documentation | Not started | |

## Dependency Graph

```
Phase 0 (pre-flight)
   |
   +--> Phase 1 (presence.py)  ------------------+
   |                                             |
   +--> Phase 2 (acp.py lifecycle) --------------+
   |                                             |
   +--> Phase 3 (remote access) --> Phase 4 -----+--> Phase 6 (docs)
        [gated on NetBird policy]    (listing)   |
                                        |        |
                                        +--> Phase 5 (/acp rework)
```

Phases 1, 2 and 3 are logically independent of each other but **all three modify `tests/test_web.py`**, so none is annotated `[P:N]` — the parallel-eligibility rule requires non-overlapping file scopes. Phase 4 precedes Phase 5 because the page consumes the endpoint. Phase 6 is last because it documents the shipped shape.

## Backwards Compatibility

| Item | Strategy | Safety effect |
|---|---|---|
| Remote bind | New config key, absent means loopback-only | A version bump cannot silently start listening (D21) |
| `MAX_SESSIONS` 3 → 8 | New config key with the new default | Raises the memory ceiling to ~1.5 GB worst case, offset by the sweeper |
| Prompt ceiling 600 s wall-clock → 900 s silence | Behaviour change | Strictly more permissive for live turns; a genuinely hung agent is still caught |
| Session record gains `last_used` | Additive | Breaks one test asserting the exact key set, updated by design |
| `_ALLOWED_HOSTS` widened | Conditional on config | Unchanged when the remote bind is off |
| `/acp` page rework | Replaces the prototype page | Same route, same token, same CSP; the row button (`session_row.html:24-26`) keeps working |
| Presence skew fix | Widens what counts as live | A stale lock from a future-stamped clock now reads live (D10, accepted) |

## File Change Summary

### Created
- `CONFIG_DIR/remote-secret` (runtime artifact, not in the repo)

### Modified
- `src/power_atlas/presence.py` — skew check, listing cache, docstrings
- `src/power_atlas/acp.py` — `last_used`, inactivity ceiling, cancel-on-timeout, sweeper, injected config, cap message
- `src/power_atlas/web.py` — remote allowlist (HTTP + WS), cookie, settings surface, listing endpoint, lifespan sweeper hook, stale rationale
- `src/power_atlas/__main__.py` — bind address at both `uvicorn.Config` sites, config injection into `acp`
- `src/power_atlas/config.py` — `remote_bind_address`, `acp_max_sessions`, `acp_idle_ttl_seconds`, `acp_prompt_silence_seconds`
- `src/power_atlas/templates/acp.html` — two-pane rework
- `src/power_atlas/static/style.css` — rail, responsive breakpoint, `100dvh` shell
- `tests/test_web.py` — new coverage; re-decide the ~153 loopback-encoding tests; two by-design edits
- `tests/acp_page.test.mjs` — extended for the reworked page
- `README.md`, `plans/ROADMAP.md`

### Deleted
None.

### Unchanged
- `src/power_atlas/data.py`, `data_kiro.py`, `status_classifier.py`, `launcher.py`, `peek.py`, `tray.py`, `notifications.py`

## 9) Implementation Divergences from Plan
<Reserved -- filled during implementation>

## Review Log

### 2026-07-31 — Cycle 1 (via /qplan Step 4)

Five sub-agents: doc-impact scan, Architect (gap-critic lens), Security auditor, Reliability engineer, Senior engineer. Confidence: Architect 45%, Security 25%, Senior engineer 20-25% (on the 6-9 day estimate; realistic 9-13.5 days), Reliability 85% (own scope).

**42 findings after dedup (11 High, 20 Medium, 11 Low). 0 auto-resolved — 6 blocking decisions gate the revision pass; the remainder are queued behind them because most touch Phase 3, whose shape depends on decision 1.**

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `uvicorn.Config(host=)` takes one string, so SC-1's "phone on NetBird **and** dashboard on loopback" is unsatisfiable while D2 rejects `0.0.0.0` | Escalated — decision 1 |
| 2 | High | Remoteness derived from the attacker-controlled `Host` header lets a peer send `Host: 127.0.0.1` and skip both allowlist and cookie | Queued — must derive from ASGI `scope["client"]` |
| 3 | High | `/static` is a `Mount` whose `matches` admits websocket scopes, so `ws://<ip>/static/x` passes neither middleware nor `ws_acp` — a third bypass D7 does not cover | Queued — replace two enforcement points with one raw-ASGI middleware seeing both scope types |
| 4 | High | Sweeper takes no claim before its first await, reopening the ungated-tool window `acp.py:2528-2540` closes | Queued — claim `closing` in a synchronous prefix, discard in `finally` |
| 5 | High | Sweeper omits `_registry.loading`; a session mid-load has zero subscribers by construction (`acp.py:812-825`) and satisfies all four sweep conditions | Queued — add `sid not in _registry.loading` as a fifth condition |
| 6 | High | `last_used` never initialised at either session constructor, so the sweeper `KeyError`s on a never-prompted session | Queued — stamp at `acp.py:1795-1798` and `:1846-1849` |
| 7 | High | `last_used` stamped inside branch dispatch misses the fall-through at `acp.py:1712`; a turn emitting only unhandled update kinds is judged silent and cancelled | Queued — stamp above the dispatch for any notification with a resolvable session id |
| 8 | High | Bind failure on the configured address is unguarded (`__main__.py:322` retries only on port-in-use), so a NetBird hiccup makes the app exit 1 | Queued — fall back to loopback and log loudly, never exit |
| 9 | High | Secret-exchange GET/POST are absent from the allowlist they must pass, so no remote device can ever authenticate | Queued — enumerate them as the only cookie-exempt remote routes |
| 10 | High | Phase 4 reads loop-owned `_supervisor.sessions` from a worker thread, contradicting the plan's own D9 | Queued — snapshot on the loop, pass the snapshot to the thread |
| 11 | High | Missing/empty `remote-secret` behaviour unspecified; an empty value with `compare_digest` fails open | Queued — load once at startup, fail closed, reuse `_acp_token_ok`'s constant-time pattern |
| 12 | Medium | Cookie derivation, lifetime, rotation and revocation all unspecified | Escalated — decision 2 |
| 13 | Medium | `port = 0` default gives an ephemeral port; a phone cannot bookmark the instance | Escalated — decision 3 |
| 14 | Medium | A backgrounded phone drops its socket, so a long task's session is swept 30 min later and its replay buffer dropped | Escalated — decision 4 |
| 15 | Medium | Dropping the skew ceiling makes our own agent's orphaned locks read live indefinitely (`acp.py:1006-1011`), where today they self-heal after 120 s | Escalated — decision 5 |
| 16 | Medium | Phase 3 gates Phases 4 and 5, so an externally-administered NetBird policy blocks all UI work | Escalated — decision 6 |
| 17 | Medium | `_ALLOWED_HOSTS` is an import-time `frozenset`; the plan never says how the configured IP reaches it, and both obvious options are traps | Queued — startup setter mirroring D15, exit criterion forbidding a per-request config read |
| 18 | Medium | `acp.shutdown()` is synchronous and cannot await task cancellation, so D14's "stopped by the existing shutdown" is impossible | Queued — cancel-and-await in `lifespan` alongside `_background_refresh` |
| 19 | Medium | Unguarded `acp.start_sweeper()` in `lifespan` turns an `acp` import failure from "/acp disabled" into "app will not start" | Queued — guard with `if acp is not None:` as `web.py:526` does |
| 20 | Medium | `asyncio.wait_for` cancels the future on expiry, so a naive re-wait loop destroys the pending future | Queued — `asyncio.shield(fut)` re-established each iteration |
| 21 | Medium | ~30 of 40 `_request` test doubles use the literal signature with no `**kwargs`; any new parameter raises `TypeError` in all of them | Queued — preserve the signature, reuse the `timeout` slot with a sentinel |
| 22 | Medium | `tests/test_web.py:2658` asserts the exact `_request` tuple including `PROMPT_TIMEOUT_SECONDS` — a third by-design test edit the plan does not name | Queued — add to Phase 2's edit list and correct the risk row |
| 23 | Medium | The `last_used` write idiom is unspecified; direct assignment `KeyError`s after close, `setdefault` resurrects a dead record forever | Queued — mandate the `.get()`-then-return idiom `record()` already models |
| 24 | Medium | The skew fix is provider-agnostic but D10's rationale is kiro-specific; it silently widens Claude Code matching | Queued — scope to `provider == "kiro-cli"` or add a Claude preservation constraint |
| 25 | Medium | Phase 3's WebSocket snippet is dead code — its path argument is an allowlist member by construction | Queued — replace with the cookie check, state ordering against `_acp_token_ok` |
| 26 | Medium | The risk row "no remote-reachable write routes" is false; `/acp` is documented as state-changing and `/ws/acp` is execution by design | Queued — narrow to "no remote-reachable route writes `config.toml`" |
| 27 | Medium | D14's coupling budget is wrong: `web → acp` goes 3→6 plus a new `__main__ → acp` edge | Queued — correct the count; the preserved invariant is acp's import list, not the caller count |
| 28 | Medium | `_SETTING_TYPES` keys read only at startup make a silent no-op settings UI, and it has no range-validation mechanism | Queued — state restart-to-apply, design the range extension |
| 29 | Medium | `remote_bind_address` has no write path and no validator; generic `str` validation accepts `0.0.0.0`, which D2 rejects | Queued — IP-literal validator rejecting `0.0.0.0`, `::` and hostnames |
| 30 | Medium | No rate limit, attempt cap or failure logging on the secret exchange, so policy drift is unobservable | Queued — WARNING per failure with peer address, per-peer backoff |
| 31 | Medium | The cookie is host-scoped and port-agnostic, so any other service on the NetBird interface is same-site for `SameSite=Strict` | Queued — apply `_acp_navigation_ok`'s rule to every remote-allowlisted GET |
| 32 | Medium | `MAX_SESSIONS` injection shape unspecified; nine test sites read `acp_mod.MAX_SESSIONS` | Queued — keep it a module-level name rewritten at startup |
| 33 | Medium | Phase 2's file scope omits `__main__.py`; Phase 1's omits `tests/test_data.py`, which reaches `_dir_listing_cache` | Queued — add both |
| 34 | Low | `__main__.py:345` is a third loopback-pinned site feeding `peek.py:267` and `tray.py:47`, both listed "Unchanged" | Queued — add all three to the change list |
| 35 | Low | Plan claims an external stylesheet violates the CSP; `_acp_csp` has no `style-src`, so a same-origin stylesheet is permitted | Queued — correct the sentence |
| 36 | Low | Phase 6's README citations are wrong — the dashboard line is `:52`, not `:30`; no feature list at `:32-53` | Queued — re-derive; doc-impact scan gives `:52` and `:54-75` |
| 37 | Low | Doc table misses `plans/tests/260701_POWERATLAS.md` (6 sites), `plans/CLOSED_INVESTIGATIONS.md`, `memory/MEMORY.md`, and 7 further ROADMAP sites incl. `:200` — the very item this plan ships | Queued — `memory/MEMORY.md:82-86` mandates the first at planning time |
| 38 | Low | Leaving `_SIDECAR_SKEW_S` and `_dir_listing_cache` unused contradicts the global no-dead-code rule | Queued — delete them and update `tests/test_data.py:1482` |
| 39 | Low | Six exit criteria are markable without evidence (disjunctive Phase 0 gate; "shows"; multi-assertion checkboxes; unautomatable soft-keyboard check) | Queued — split and give each a method |
| 40 | Low | No exit criterion anywhere exercises 8 concurrent sessions, the new default | Queued — add one, or lower the default until measured |
| 41 | Low | SC-2's "byte-identical" is unachievable — Phases 1, 2 and 5 change loopback behaviour by design | Queued — scope SC-2 to the listening surface and Host allowlist |
| 42 | Low | `acp.py:339-342` claims `fromisoformat` cannot parse kiro's 9-digit fraction; empirically stale on Python 3.13.13 | Queued — add to the doc table |

**Corrected during verification, not carried as findings**: the Architect's claim that dropping the skew ceiling admits *recycled-pid* false-lives is wrong — pid exclusivity means a dead writer held the pid before the live process existed, so the `-5 s` lower bound still rejects it (D10 stands). The real regression is finding 15, which the Reliability reviewer identified precisely. Also verified as non-issues: `cache_listing=False` has no other consumers, and its ~19 ms cost is off-loop behind a 3 s TTL at all five call sites.

## Harness Improvement Opportunities

- The mandatory Step 1.5 dispatch gate fired correctly, but the load-bearing unknown both sub-agents flagged (`started_at` semantics) was resolvable in ~90 seconds with a runtime probe the sub-agents could not run — cost: two agents each spent a paragraph hedging an answer a probe settled outright — suggested change: let the trio return a "decidable by probe" list the orchestrator runs before the interview, rather than folding those into `[unverified]` prose.
- `/qexplore` Step 3's filename spec says `{YYMMDD}_{NAME}.md` while this repo's archive convention is `{YYMMDD}-{HHMM}_{NAME}.md`, with the `-HHMM` added at `/qclose` time — cost: one commit-log check to confirm which applies at creation — suggested change: note in the skill that the time component may be archive-time only, so the slug stays stable across the lifecycle.
- `/qplan`'s parallel-annotation rule keys on file-scope overlap, but a single-test-file repo makes every phase overlap on `tests/test_web.py`, so no phase is ever `[P:N]`-eligible however independent its production code is — cost: parallel dispatch unavailable for three genuinely independent phases — suggested change: let the rule consider production-file scope and test-file scope separately, or allow `[P:N]` when only the test file overlaps and phases append disjoint test classes.
