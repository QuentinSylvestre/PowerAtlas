# ACP Remote Client Productization

> **Date**: 2026-07-31
> **Status**: In Progress — Phase 0 complete; Phases 1-6 pending  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Promote the throwaway `/acp` prototype into a NetBird-reachable remote client that dispatches, drives and resumes kiro-cli sessions PowerAtlas creates, with a Zed-style session browser, an idle sweeper, and a security model that survives leaving loopback.
> **Estimated effort**: 9-13.5 days (revised from 6-9 after review cycle 1; see Review Log)

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
- **SC-2**: With the remote bind disabled (the default), exactly one listening socket exists and it is loopback; `_ALLOWED_HOSTS` admits only loopback forms. (Scoped to the listening surface and the Host allowlist — Phases 1, 2 and 5 deliberately change loopback *behaviour*, so "byte-identical" would be untestable.)
- **SC-3**: A request arriving on the remote socket is refused unless its path is on the remote allowlist **and** it carries a valid device cookie. Verified separately for HTTP, for the `/ws/acp` upgrade, and for `ws://…/static/…` — the three transports that reach the app by different code paths.
- **SC-3b**: With the remote bind enabled, `port` must be non-zero; a zero port is rejected at config load with a named error rather than producing two differently-numbered listeners.
- **SC-4**: `/api/launchers`, `/api/settings` and `GET /` are unreachable from the NetBird address; reaching them requires loopback.
- **SC-5**: A device without the remote secret is refused on both HTTP and the WebSocket, even when it reaches the NetBird address.
- **SC-6**: The session browser lists workspaces with their sessions, defaulting to 10 groups expanded with 3 sessions each, both independently paginated, with a per-session availability indicator; sessions locked by a live foreign process are greyed and not loadable.
- **SC-7**: An ACP-owned session shows a live status dot in the dashboard for its whole lifetime, including more than 120 seconds after the agent process started and while idle between turns for more than 5 minutes.
- **SC-8**: A turn that streams for longer than the old 600 s ceiling completes rather than timing out; a turn that goes silent for the configured window fails and cancels agent-side.
- **SC-9**: A session idle beyond the TTL with no attached subscriber, no in-flight turn and no in-flight close is terminated and its `.lock` removed; a session with an attached tab or a running turn is never swept regardless of age.
- **SC-10**: `MAX_SESSIONS` is read from configuration rather than a hardcoded literal, defaults to 8, and is not read from disk on the event loop.
- **SC-10b**: Eight concurrent sessions can be created and driven, with the measured process and RSS cost recorded — the new default is validated, not assumed.
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
| D7 Allowlist enforcement point | **One raw-ASGI outer middleware** (`app.add_middleware(cls)`) that sees `http` **and** `websocket` scopes | Two points (`same_origin_guard` + the `/ws/acp` handler); middleware alone | *Revised in cycle 1.* `BaseHTTPMiddleware` returns early on non-`http` scope (`web.py:752-755`), so the original "middleware alone" is wrong — but so was "two points": `/static` is a `Mount` whose `matches` admits websocket scopes, so `ws://<ip>/static/x` reaches `StaticFiles` having passed neither `same_origin_guard` **nor** `ws_acp`. A raw-ASGI middleware is the only construct that sees every scope type, and it collapses two partial gates into one complete one |
| D8 Secret storage | Own file `CONFIG_DIR/remote-secret`, generated on first enable | In `config.toml` + a new `tests/conftest.py` | Keeps a credential out of `config.toml`'s sanitise-and-whole-file-rewrite path and out of the blast radius of 18 known config-leaking tests; avoids a new test file `AGENTS.md:8` would require the user to request |
| D9 Presence fix location | Fix `presence.py`'s own heuristic; no coupling either direction | `acp → presence`; `presence → _supervisor` | Preserves `acp.py`'s import invariant (`:15-28`) and avoids reading loop-owned state from presence's worker threads |
| D10 Skew check repair | Drop the upper bound at `presence.py:467` **for the kiro-cli branch only**, keep the `-5 s` lower bound | Widen 120 s → hours; special-case the agent pid; drop it for both providers | A recycled pid writes its lock *before* the current process started, so the lower bound alone still rejects it — verified against `presence.py:85-92`'s own measurement (785 locks, 21 live pids, one genuine). Widening only postpones the failure. *Cycle-1 correction*: the loop serves both kiro and Claude sidecars (`presence.py:207`, `:219`) while this rationale is kiro-specific, so the change is provider-scoped rather than global. The residual false-live class is D32 |
| D11 Turn ceiling | Inactivity ceiling of 15 min silence, reset by each `session/update` | Raise the wall clock; remove the bound | Preserves the ceiling's actual purpose (detect a stopped agent) without capping legitimate long turns. Process death is separately detected by the reader thread's finally (`acp.py:1722`) |
| D12 Idle signal | Stamp `last_used` from the same `session/update` stream | Stamp at turn start; stamp at turn end | Start-stamping makes a 45-min turn look 45 min idle the instant it ends; notification-stamping makes a working session never-idle by construction |
| D13 Sweeper close path | Call `_supervisor.close_session` directly and broadcast `session_closed`; do **not** relax `_handle_close` | Relax the `not_subscribed` guard | That guard protects a real case ("a socket not watching a session has no business releasing what another tab holds", `acp.py:2658-2665`); the sweeper has no socket and should not weaken it |
| D14 Sweeper ownership | `acp.py` owns the task; `web.py` `lifespan` starts it (guarded by `if acp is not None`) and **cancels-and-awaits** it before the ACP teardown finally | `web.py` owns it, like `_background_refresh`; stop it from `acp.shutdown()` | Keeps ACP lifecycle policy inside `acp.py`. *Cycle-1 corrections*: `acp.shutdown()` is **synchronous** and cannot await a task cancellation, which is exactly why `_background_refresh` is cancelled-and-awaited in the lifespan finally (`web.py:503-507`); and the start hook must be guarded, or an `acp` import failure turns "/acp disabled" (`web.py:41-49`) into "app will not start". The coupling budget in the original wording was also wrong: `web → acp` goes 3 → 6 and a new `__main__ → acp` edge appears. The invariant actually preserved is `acp.py`'s **import list** (`:15-28`), not the caller count |
| D15 `MAX_SESSIONS` plumbing | Read from config **once at startup**, injected into `acp`; default 8 | `load_config()` inside `at_capacity()` | `at_capacity()` runs on the loop and `load_config()` is an uncached full TOML parse; reading it there reproduces the exact stall `_handle_new` already threads out to avoid |
| D16 Session browser shape | Workspace groups; 10 groups default with show-more; groups expanded showing 3 sessions with per-group paging | Flat recency list; collapsed groups | User decision. 10×3 ≈ 30 visible rows also bounds the per-row lock check to ~30, not 1,207 |
| D17 Availability indicator | Three states — available / held-by-PowerAtlas / locked-elsewhere; lazy per visible row, off-loop, **fails open** | Two states; no indicator | A wrongly-greyed session is unreachable from the UI; a wrongly-available one gets the typed in-use refusal at load. 841 of 1,207 sessions carry a lock, nearly all stale, so "has a lock" alone is useless |
| D18 Listing endpoint | Purpose-built, read-only, no env, no actions | Reuse `/partials/all-sessions` | `partials/session_row.html` is hover-driven (`:3`) and carries the launch-action cluster — dashboard markup that is useless and undesirable on a phone. A narrow route is also auditable against the allowlist |
| D19 Listing pagination | Server-side paging per group and across groups; do not inherit the existing filter behaviour | Reuse `get_all_sessions_paginated` verbatim | *Resolved deterministically*: all four existing filters set `has_more = False` (`web.py:1301`, `:1318`, `:1329`, `:1340`), i.e. filtering operates on the loaded page only. Inheriting that would silently truncate a 208-session workspace |
| D20 UI reference | `kiro-ui` (Apache 2.0) as design reference only | Adopt its code | Its Express/React/Vite/Electron stack is incompatible with PowerAtlas's zero-build-step Jinja + htmx frontend; adopting it means rebuilding the dashboard |
| D21 Remote bind default | Off; absent config means loopback-only | On once configured | A version bump must never silently start listening on NetBird |
| D22 Secret delivery | URL + secret as copyable text in settings | QR code | A QR needs a new dependency for convenience only; deferred |

| D23 Dual bind mechanism | **Pre-bound sockets** — create both sockets, hand them to one `uvicorn.Server` via `run(sockets=[...])` | `0.0.0.0` (re-opens D2); two `Server` instances; NetBird-only | *User decision, cycle 1.* `uvicorn.Config(host=)` takes one address, so SC-1 and SC-2 were unsatisfiable as written. `Server.run(sockets=…)` is supported API on the installed uvicorn 0.49.0 (`server.py:74`) and is what uvicorn uses for `--reload`/`--workers`. Two `Server`s would run **lifespan twice** — two refresh loops, two sweepers racing on the same sessions, `acp.shutdown()` twice. `0.0.0.0` would put a listener on every network the laptop ever joins |
| D24 Cookie derivation | **HMAC over a device identifier, keyed by the file secret**, constant-time compared | Opaque server-side token; the raw secret as the cookie | *User decision, cycle 1.* Verifiable with no server-side store, so it survives a restart — which is what "long-lived" must mean if the phone is not to re-enter the secret after every launch. Rotation of the secret revokes every device at once; per-device revocation is knowingly given up (there are two devices). The raw-secret option would leave the credential at rest in every cookie jar |
| D25 Port when remote | **Non-zero `port` required** when `remote_bind_address` is set; rejected at config load | Allow `port = 0` | Two independent reasons. A phone cannot bookmark an OS-assigned ephemeral port (`__main__.py:344`). And with `port = 0` the OS assigns **per bind call**, so the two sockets would land on different port numbers and the laptop and phone URLs would permanently disagree |
| D26 Remoteness signal | ASGI **`scope["client"]`** (peer address); `client is None` treated as remote | The `Host` header; `scope["server"]` | *Cycle-1 security finding.* `Host` is attacker-controlled — a NetBird peer sending `Host: 127.0.0.1:<port>` would read as local and skip both allowlist and cookie, defeating SC-4 and SC-5. `Host` answers "what name did the browser use", the right question for DNS-rebinding defence and the wrong one for network origin. Nothing in `src/` reads `scope["client"]` today |
| D27 Bind-failure behaviour | Bind loopback **first**, then attempt remote; on failure log at ERROR and continue loopback-only | Let it raise; retry | PowerAtlas autostarts at login and NetBird's interface may not be up yet, raising `OSError` (Windows `WinError 10049`). The existing retry (`__main__.py:322`) covers only port-in-use and is gated on `desired_port > 0`, so unhandled this makes the app **exit 1** rather than degrade |
| D28 Sweeper claim discipline | Claim `closing` in a **synchronous prefix** after the checks, `discard` in `finally`; guard on `_registry.loading` as a fifth condition | Rely on the four original conditions | *Cycle-1 reliability findings.* `close_session` leaves the session in `sessions` and out of `closing` for the whole terminate round-trip, so a prompt arriving in that window starts a turn on a session being released — the window `acp.py:2528-2540` exists to close. And a session mid-`session/load` has **zero subscribers by construction** (`acp.py:812-825`), so it satisfied all four original conditions and would have been swept mid-load |
| D29 `MAX_SESSIONS` shape | Stays a **module-level rebindable name** in `acp.py`, rewritten at startup | A `_Supervisor` attribute; a config read in `at_capacity()` | Nine test sites read `acp_mod.MAX_SESSIONS` (verified count); an attribute would break all nine with `AttributeError`. A config read in `at_capacity()` puts an uncached TOML parse on the loop, which D15 forbids |
| D30 Inactivity ceiling mechanism | Preserve `_request`'s signature; reuse the `timeout` slot with a sentinel meaning "inactivity mode"; re-wait on `asyncio.shield(fut)` each iteration; session id from `params["sessionId"]` | A new `_request` parameter; a watchdog task cancelling `_pending[id]` | **19** fixed-signature `_request` stubs in `tests/test_web.py` (7 `boom`, 5 `fake_request`, 5 `refused`, 2 multi-line) would raise `TypeError` on a new parameter; 4 tolerant `lambda *a, **k` patches would not. And `asyncio.wait_for` **cancels** the future on expiry (`acp.py:1489`), so a naive re-wait loop destroys the pending future and the real answer is dropped as "late or unmatched" (`:1630`) — `shield` is required, not optional |
| D31 Sweeper vs a backgrounded phone | **No grace period.** A swept session stays resumable via `session/load` | A detach-keyed grace window; never sweep a session that ever had a subscriber | While the agent streams, `last_used` advances, so a *running* task is never swept whatever happened to the socket. The only affected case is returning >30 min after the task finished, and terminate leaves `.json`/`.jsonl` intact — cost is one reload. A grace window would add state for a case the activity stamp already covers |
| D32 Agent-orphaned-lock false-live | **Accepted and recorded** | Gate acceptance on `_supervisor.sessions` | `session/load` makes our own agent write a lock naming itself (`acp.py:1006-1011`); a lock orphaned by a failed load, or by `close_session` when terminate raises, has `pid == live agent pid` and a forward delta — so with the ceiling gone it reads live for the agent's whole life, where today it self-heals after 120 s. The only fix is reading `_supervisor.sessions` from presence, which D9 forbids; accepting is the honest option |
| D33 Phase 3 without its ACL layer | **Ship Phase 3 with the device cookie as the *sole* authorization layer** | Wait for a restricting NetBird policy (the plan's own Phase 0 gate); abandon remote access | ***User decision, 2026-07-31***, taken after Phase 0 measured the gate as failing and the consequence was stated explicitly. No policy restricts this peer — all 17 account peers sit in its network map — so D3's "primary" layer does not exist, and the cookie is not the second of two independent controls but the only one. **Consequence, recorded so a later reader does not mistake the shipped state for the designed one**: every cookie defect is now directly exploitable rather than defence-in-depth, and the thing behind it is a `-a` agent, i.e. arbitrary command execution as the user. Phase 3's cookie code carries the entire model alone. This overrides Phase 0's "Phase 3 does not start until one does" |

*D1-D18 and D20-D22 carry forward from `/qexplore`'s resolved decisions and its assumptions ledger (surfaced and un-vetoed at the exploration assumptions checkpoint). D19 is a deterministic open item resolved by planner judgment. D23-D32 are cycle-1 review outcomes: D23-D25 and D31 are user decisions or planner calls on escalated findings; D26-D30 and D32 are corrections to defects the review found.*

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
3. **Verify `session/cancel` is honoured mid-tool** — D30 sends cancel on an inactivity timeout, and whether kiro-cli actually stops a running tool on receipt is unverified. If it does not, the orphaned-turn claim in §2 must be softened rather than asserted.
4. **Capture green baselines** — `pytest` (full), and `node tests/acp_page.test.mjs`. Record counts; `tests/test_data.py` has ~8 known timing-flaky tests (`memory/MEMORY.md:89-93`), so re-run standalone before attributing a failure.

**Exit criteria**:
- [ ] NetBird policy verified: the admitting policy is named, and the groups it admits are recorded — **not ticked**: naming the policy needs console access. Its *effect* was measured locally instead (see notes)
- [ ] The policy admits the user's own devices only — `iphone-quentin` (`100.78.26.204`) and `ipad-quentin` (`100.78.66.16`) — and not the other 14 account peers — **refuted by measurement, not merely unverified**
- [x] If no such policy exists, Phase 3 is marked blocked in the Progress Tracker with the reason, and Phases 1, 2, 4 and 5 proceed
- [x] `_kiro.dev/session/terminate` re-verified on the installed version, with the version recorded
- [x] `session/cancel` mid-tool behaviour observed and recorded, either way
- [x] Full `pytest` baseline recorded (count + any pre-existing failures)
- [x] `node tests/acp_page.test.mjs` baseline recorded

#### Implementation (2026-07-31, code: none — verification-only phase)

Phase 0 ran four measurements against kiro-cli 2.16.0 (`kiro-cli --version` reports `kiro-cli-chat 2.16.0`; the ACP `initialize` handshake reports `agentInfo.version` `2.16.0`). Two new probe scripts were written into the existing spike harness: `run8_terminate.py` and `run9_cancel.py`, with results in `verdict_run8_terminate.json`, `verdict_run9_cancel.json` and the raw wire trace `traces\run9_cancel.jsonl`. The kiro session store at `~/.kiro/sessions/cli` held 5,945 `.json` files before the work and 5,945 after; all three probe sessions were deleted, verified by globbing each session id and finding no residue, and no `PING.EXE` or `kiro-cli.exe` process survived either run.

`_kiro.dev/session/terminate` was re-verified and all four claims hold, this time measured independently rather than inferred. Two sessions were created against one agent (pid 70012); session 2 was terminated and session 1 was left alone. The method returned exactly `{}` with no error in 0.0038 s. Its `.lock` (59 bytes) was gone afterwards while session 1's `.lock` survived, so the removal is per-session rather than agent-wide. The process tree fell from 8 processes / 608.3 MB to 5 / 438.6 MB — 3 processes and 169.7 MB freed, matching the earlier 3 / 172.6 MB reading closely enough to call the model stable. The `.json` transcript was byte-identical before and after (1,341 bytes, unchanged mtime) and the `.jsonl` survived too, which is what D31's "a swept session stays resumable" rests on. Session 1 answered a prompt with `end_turn` afterwards; the terminated session returned `-32603 / "No session found with id"`. Worth noting that run 4 — the measurement the plan cites — never looked at the session store at all, so the lock-removal and transcript-intact claims were being asserted from a run that could not have seen them. They are now measured.

The cancel probe is where the plan changes. The design deliberately required two independent signals before cancelling, so a cancel landing before any tool started could not be mistaken for a result: a protocol-level `session/update` of kind `tool_call`, and a new OS process under the agent that was absent from the pre-prompt baseline. Both fired — the agent ran `ping -n 91 127.0.0.1` via its `shell` tool and streamed ten `tool_call_update` frames at roughly one-second intervals, while `pwsh.exe` and `PING.EXE` appeared under the agent. After a ten-second dwell, `session/cancel` was written as a notification. The `session/prompt` request answered nine milliseconds later with `stopReason: "cancelled"`. That is a genuinely honoured cancel and it is faster than the plan expects: because the response is matched to the pending future rather than dropped, `acp.py:1630`'s "late or unmatched" path is not exercised on the cancel route, and `CANCEL_GRACE_SECONDS = 30.0` is protecting a window that measured at 9 ms.

The surprise is what did not happen. Neither tool process died. `pwsh.exe` (63864) and `PING.EXE` (73648) were alive when the prompt answered and were still alive after a further twenty seconds of watching — `tool_pid_deaths_s_after_cancel` is an empty dict, and the tree remained at 7 processes / 557.7 MB against a 5 / 454.4 MB baseline. `ping -n 91` was simply left to run out its ninety seconds; the processes were only reaped when the probe closed its Windows job object. So the ACP turn is cancelled and the OS work is not. The plan's Phase 2 names exactly one residual hazard — an orphaned turn — and the honest position after this measurement is that the orphaned turn is the smaller of the two problems and the orphaned *process* is the one nobody has accounted for. A session whose turn the inactivity ceiling cancels can be holding a shell subprocess that runs for arbitrarily long afterwards, invisible to `inflight`, to the sweeper's six conditions, and to SC-10b's RSS figure. Whether `_kiro.dev/session/terminate` reaps such an orphan is unmeasured — run 8 terminated an idle session — and that gap deserves its own Phase 2 exit criterion rather than an assumption.

Three smaller protocol facts came out of the trace and bear directly on Phase 2's Change 1. `_kiro.dev/subagent/list_update` carries no `sessionId`, making it the one observed `_kiro.dev/*` notification that cannot be attributed to a session, so the stamping helper needs a real null path. `_kiro.dev/session/update` exists as a method separate from `session/update`, carries a `sessionId` and a `tool_call_chunk` update, and today falls through `acp.py`'s dispatch — it is agent-liveness evidence that the inactivity ceiling must count, which the "stamp above the branch dispatch" design gets right only if the stamp keys on the presence of a session id rather than on a method allowlist (`acp.py:1675` names only `METADATA_METHOD`). And over the twenty-second idle window after the turn ended, no notification of any kind arrived, with all eleven `_kiro.dev/*` frames clustered at session start, turn start and turn end — weak but real evidence that these are turn-scoped rather than a heartbeat, bounded by the short observation. Separately, `tool_call` frames on 2.16.0 carry no `status` field at all, so any check for "a tool is running" must key on `sessionUpdate == "tool_call"`.

The baselines are recorded and one of them is not green. Full `pytest` collects 1,045 tests and reports `1 failed, 1042 passed, 2 skipped, 1 warning in 7.34s`. The failure is `tests/test_web.py::test_search_with_status_filter` at `tests/test_web.py:6689` (`assert mock_snap.call_count == 3`, actual 5), and it reproduces standalone in 0.76 s, so it is deterministic and pre-existing on HEAD `e4fced3` — not the documented `tests/test_data.py` flake class, which did not fire at all (that file passed 135/135 standalone and contributed no failures to the full run). SC-11 as written requires a green `pytest`, and it cannot be met without either fixing this test or carving it out explicitly. `node tests/acp_page.test.mjs` is clean at 15 passed, 0 failed. The two NetBird exit criteria remain unverifiable from here: they need the NetBird console, which is administered by others, and nothing on this machine or in the repo records the policy set — so the Phase 3 gate is untouched by this phase's work.

##### Orchestrator addenda (not the phase sub-agent's work)

**The NetBird gate is answered — negatively — without console access.** The sub-agent correctly reported the policy set as unreadable from the repo, but the policy's *effect* is observable locally. NetBird's management server distributes to each peer only the network-map entries for peers some policy connects it to. `netbird status` on this host reports `Peers count: 4/17 Connected` and `netbird status -d` enumerates **all 17**, including machines that are plainly not the user's: `akita`, `akita-169-122`, `paros-g`, `ec2amaz-tv495hp`, `nuc-chicago`, `ps-tls-p-2302-mustafa`, `macbook-air-de-polestar`, `ps-tls-p-2503`, `ps-tls-p-2304`, `ip-10-0-1-165`, `vm-tls-desktop`, `hostname`, `polestar`, `moto-g75-c123`. Had a policy restricted this peer to `iphone-quentin` (`100.78.26.204`) and `ipad-quentin` (`100.78.66.16`), the other fourteen would not be in this host's network map at all.

So Phase 0's second exit criterion is **refuted**, not deferred: no restricting policy is in force today. The residual uncertainty is narrower than the criterion's wording — a policy could admit all 17 peers at the network layer while restricting protocol or port, which `netbird status` does not show (`Forwarding rules: 0`). That distinction cannot be settled from here and does not change the gate: Phase 3 stays blocked either way, because D3's premise is that the ACL is the *primary* authorization layer and it is currently admitting the whole account.

**The pytest failure is a stale assertion, not a product defect.** Traced to `e4fced3` ("fix: hover provider actions missing in search results"), which deliberately hoisted `snap = await asyncio.to_thread(presence.get_snapshot)` out of the `if grouped and status and status != "all"` guard to an unconditional call at `web.py:1464`, because the per-card `ws_status` render needs it on every card. The test at `tests/test_web.py:6689` still asserts the optimization that change traded away — its own comment, "status=all and the no-status path both skip the presence scan entirely", is now false. The count moves 3 → 5 because the two remaining URLs each now cost one snapshot. The product behaviour is intentional and shipped; only the assertion and its comment are stale. Fixing it is a two-line edit, but it is **outside this plan's scope** and belongs in its own commit, so it is escalated rather than absorbed.

**Per-phase review deferred to Step 9**: Phase 0 produced no tracked-file changes. Its deliverable is measurement recorded in this file, and the probe scripts live in the session scratchpad.

---

### Phase 1: Presence — make ACP sessions visible in the dashboard [QA]
**Goal**: Fix both mechanisms that make an ACP-owned session read `closed`, without coupling `presence` and `acp`.
**File scope**: `src/power_atlas/presence.py`, `tests/test_web.py`, `tests/test_data.py`
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
if provider != "kiro-cli" and delta > _SIDECAR_SKEW_S:
    continue          # Claude Code sidecars keep the original window
```

**Provider scoping matters** (cycle-1 finding): this loop serves both `_KIRO_LOCK_DIR` and `_CLAUDE_SESSION_DIR` sidecars (`presence.py:207`, `:219`), while D10's rationale is entirely kiro-specific. Dropping the ceiling for both would silently widen Claude Code matching with no argument behind it.

`_SIDECAR_SKEW_S` (`presence.py:93`) therefore stays — it still governs the Claude branch. Its comment must be rewritten to say it is provider-specific rather than universal.

**Change 2 — stop caching the kiro lock-directory listing.** `_list_sidecars(_KIRO_LOCK_DIR, ".lock")` is called with `cache_listing=True` (`presence.py:207`, default `:143`). Its docstring's premise — sidecars are written once and never rewritten — is falsified by `session/load` rewriting a lock in place, which leaves directory mtime untouched so `_load_json_cached` matches a stale `(mtime, size)` and returns the old parse (`:121-123`). Pass `cache_listing=False` for the kiro directory only; the Claude call site (`:219`) keeps it.

**Do not leave dead code behind.** If the Claude branch also ends up not caching, the `cache_listing` parameter and `_dir_listing_cache` (`presence.py:139`, `:160-181`) lose their last consumer and must be **deleted**, not left with a comment — the global rule forbids retaining unused code. `tests/test_data.py:1482` reaches into `_dir_listing_cache` and would need updating in the same change.

**Cost note**: 841 `.lock` files are re-listed per scan instead of on directory-mtime change. Measured at ~19 ms, off-loop at all five `get_snapshot` call sites (`web.py:1091`, `:1183`, `:1332`, `:1464`, `:2004`, all via `asyncio.to_thread`) behind a 3 s TTL (`presence.py:70`, `:492`) — acceptable, but bounded by an exit criterion rather than assumed.

**Exit criteria**:
- [ ] A kiro lock whose `started_at` is hours after its live holder's start reads as live
- [ ] A kiro lock whose `started_at` precedes its (recycled) pid's start still reads as not-live
- [ ] A **Claude Code** sidecar outside the 120 s window still reads as not-live — the change is provider-scoped
- [ ] An in-place lock rewrite is observed on the next scan rather than pinned to the previous parse
- [ ] A full `_scan` with 841 locks completes within 50 ms, measured
- [ ] `presence.py`'s docstrings no longer assert the falsified write-once premise, and `_SIDECAR_SKEW_S`'s comment says it is provider-specific
- [ ] Any parameter or cache left without a consumer is deleted, not commented; `tests/test_data.py:1482` updated if `_dir_listing_cache` goes
- [ ] Tests added to `tests/test_web.py` covering both changes; full suite green

---

### Phase 2: Session lifecycle — long turns survive, idle sessions are reclaimed [QA]
**Goal**: Replace the wall-clock turn ceiling with an inactivity ceiling, stamp activity, sweep idle sessions, and make `MAX_SESSIONS` configurable.
**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/config.py`, `src/power_atlas/__main__.py`, `src/power_atlas/web.py` (lifespan hook only), `tests/test_web.py`
**Covers**: SC-8, SC-9, SC-10, SC-10b

**Change 1 — two timestamps, not one.** Cycle 2 found that the single-field design conflates two *opposed* questions, so the record carries both:

| Field | Question it answers | Advanced by | Read by |
|---|---|---|---|
| `last_activity` | "is the agent still working?" | **any** `session/update`, including `_kiro.dev/*` metadata | the inactivity ceiling |
| `last_used` | "has nobody used this session?" | prompt sent; subscriber attach/detach | the sweeper |

Using one field for both means an agent-side heartbeat refreshes the sweeper's idle clock, so a chatty agent silently makes sessions **permanently unsweepable** — defeating SC-9 and SC-10 with no error anywhere and nothing in Phase 0 verifying that `_kiro.dev/*` notifications are turn-scoped. The ceiling wants *any* sign of life to count; the sweeper must ignore agent-generated noise. They cannot share a field.

Three further parts, each of which the cycle-1 review found the original wording got wrong.

*Initialise at both constructors.* Write `"last_used": time.monotonic()` in `new_session` (`acp.py:1795-1798`) **and** `load_session` (`:1846-1849`). Without this the sweeper `KeyError`s on any session that was created but never prompted — including the `_handle_new` "socket went away" case (`:2474-2480`), which also has no subscriber and would be swept on the first tick. This is also what actually makes the two named test edits correct: `TestAcpSessionRecordHoldsNoDeadState` (`tests/test_web.py:5033`, `:5044`) asserts the key set **immediately after** `new_session`/`load_session` with no notification in between, so under notification-only stamping it would not have broken at all.

*Stamp above the branch dispatch.* In `_on_notification` (`acp.py:1669`), stamp once for any notification carrying a resolvable session id, **before** the `sessionUpdate` kind dispatch and including `METADATA_METHOD` (`:1675`). The two obvious branches (`:1680`, `:1695`) do not cover the fall-through at `:1712`, and `acp.py:210-218` records at least six update kinds — so a turn emitting only `agent_thought_chunk`, `plan` or `current_mode_update` for the silence window would be judged silent and cancelled, the exact regression SC-8 forbids.

*Use the non-resurrecting write idiom*, which `record()` (`:1873-1877`) and `_note_context()` (`:1966-1968`) already model:

```python
meta = self.sessions.get(session_id)
if meta is None:
    return          # closed or detached; never recreate the record
meta["last_used"] = time.monotonic()
```

Direct assignment raises `KeyError` after `close_session` pops the record (`:1929`) or `_detach` clears the dict (`:1370`); a `setdefault`-shaped write resurrects it, where `at_capacity()` counts it against `MAX_SESSIONS` forever and the sweeper re-issues terminate every minute.

`time.monotonic()` deliberately, not `time.time()` — the sweeper compares elapsed intervals and must not be moved by a clock adjustment. This puts two clocks in one record (`created` is wall-clock); note it in the code rather than leaving it to be rediscovered.

**Change 2 — inactivity ceiling** (mechanism per D30). `PROMPT_TIMEOUT_SECONDS = 600.0` (`acp.py:275`) bounds a turn on wall clock via `_request(..., timeout=PROMPT_TIMEOUT_SECONDS)` (`:1895`). Replace with a deadline reset by activity, **without changing `_request`'s signature**:

```python
# `timeout` carries the _INACTIVITY sentinel (a module-level object(), branched
# on with `is` BEFORE the try - the slot is annotated float and formatted
# {timeout:.0f} at :1492, which a sentinel would blow up).
#
# The deadline is LOCAL and seeded at send time. Reading the shared
# last_activity as the baseline would cancel the first prompt on a session
# that has been idle longer than the silence window - a session idle 20 min
# with a tab attached is unswept but already "silent", so its next prompt
# dies at the first tick before the agent can answer.
deadline = time.monotonic() + PROMPT_SILENCE_SECONDS
hard_stop = time.monotonic() + PROMPT_ABSOLUTE_MAX_SECONDS
while True:
    try:
        # wait_for CANCELS its future on expiry (:1489), so the bare `fut`
        # is never handed to it directly - shield each pass. Verified on
        # 3.13.13: the inner future survives, and shield detaches its
        # callback on outer cancellation, so callbacks do not accumulate.
        return await asyncio.wait_for(asyncio.shield(fut), PROMPT_TICK_SECONDS)
    except asyncio.TimeoutError:
        meta = self.sessions.get(session_id)
        if meta is None:
            break            # closed, or _detach cleared it on agent death;
                             # fall through to `await fut` so the typed
                             # AgentDied it already carries is what surfaces
        deadline = max(deadline,
                       meta["last_activity"] + PROMPT_SILENCE_SECONDS)
        now = time.monotonic()
        if now > deadline or now > hard_stop:
            await self._notify("session/cancel", {"sessionId": session_id})
            # Bounded grace: let an honoured cancel land a final frame before
            # inflight is released, or prompt #2 interleaves with turn #1 in
            # the same transcript with no turn id to separate them.
            with contextlib.suppress(asyncio.TimeoutError):
                return await asyncio.wait_for(asyncio.shield(fut),
                                              CANCEL_GRACE_SECONDS)
            raise AgentTimeout(...)
return await fut             # session-gone path
```

**A hard ceiling survives, deliberately.** `PROMPT_ABSOLUTE_MAX_SECONDS` (default 4 h) replaces the safety property the wall-clock bound provided. Without it a turn emitting one chunk just under the silence window runs forever — and `inflight` makes that session simultaneously un-closable (`acp.py:2677`) **and** un-sweepable (sweep condition 3), so ~150 MB and its processes are unreclaimable for the app's lifetime with no operator path short of restart. "Long turns" means generous, not unbounded.

**Named constants, all module-level and rebindable** so tests can shorten them rather than burning wall clock: `PROMPT_TICK_SECONDS = 15.0`, `PROMPT_SILENCE_SECONDS = 900.0`, `PROMPT_ABSOLUTE_MAX_SECONDS = 14400.0`, `CANCEL_GRACE_SECONDS = 30.0`, `SWEEP_INTERVAL_SECONDS = 60.0`. Worst-case cancel latency is `silence + tick`.

**Testability**: the loop lives inside `_request`, which ~30 test doubles replace wholesale — so nothing reaching `_handle_prompt` exercises it. The three timing exit criteria need a direct `_request` test with a fake `_write` and a hand-driven `_pending` future, with the constants rebound small.

Signature preservation is not cosmetic: **19** fixed-signature `_request` stubs in `tests/test_web.py` (7 `boom`, 5 `fake_request`, 5 `refused`, 2 multi-line) would raise `TypeError` on a new parameter. `session/cancel` is a `_notify`, not a `_request` (`acp.py:1912`), so the cancel cannot itself hang.

`REQUEST_TIMEOUT_SECONDS = 90.0` is unchanged — only the prompt path moves.

*A third by-design test edit*: `tests/test_web.py:2658` asserts the exact `_request` call tuple including `acp_mod.PROMPT_TIMEOUT_SECONDS`. `:4484`'s docstring and `:3723`'s "MAX_SESSIONS is 3" also go stale.

*Honest scope of the cancel*: `_handle_prompt`'s `finally` (`:2570-2574`) still clears `inflight` and emits `turn end`, so a user can immediately send a second prompt while the agent may still be finishing the first, and the eventual response is dropped as "late or unmatched" (`:1630`). Cancel-on-timeout **mitigates** the orphaned turn; it does not eliminate it. Phase 0 verifies whether kiro-cli honours cancel mid-tool at all.

**Change 3 — the idle sweeper** (discipline per D28). Owned by `acp.py`, started from `lifespan` **guarded** (`if acp is not None:`, exactly as the teardown at `web.py:526` is) — `acp.shutdown()` is synchronous and cannot await a task cancellation.

**Shutdown ordering is load-bearing.** Cancel both tasks, then a single `await asyncio.gather(refresh, sweeper, return_exceptions=True)`, **inside the nested `finally` block and before `acp.shutdown()`**. Putting the sweeper await in the outer `finally` alongside `_background_refresh` (`web.py:503-507`) means a non-`CancelledError` from `await task` skips the teardown — precisely the failure the nested block at `web.py:508-513` was written to prevent for `acp.shutdown()`.

**Sleep first, always.** The tick body is `await asyncio.sleep(SWEEP_INTERVAL_SECONDS)` **then** the work — a `continue` placed before the sleep never yields and hangs the entire event loop, taking the dashboard and every websocket with it. After sleeping, `if not _supervisor.sessions: continue` so a launch that never opens `/acp` still pays nothing (`acp.py:1074-1076`).

Then iterate `tuple(_supervisor.sessions.items())` — `close_session` mutates the dict (`:1929`) and a live iterator would raise `RuntimeError: dictionary changed size during iteration`.

Sweep when **all six** hold, checked and claimed in one synchronous prefix:

- `sid in _supervisor.sessions` — re-checked **inside** each iteration. The tuple is snapshotted once but `close_session` awaits within the loop, so by the time session *n* is reached a user close may have popped it, and `close_session` would raise `AgentRejected` (`:1924-1925`) and log a WARNING every pass.

- `now - last_used > ACP_IDLE_TTL_SECONDS` (default 1800)
- `not _registry.subscribers.get(sid)` — an attached tab means leave it alone regardless of age
- `sid not in _supervisor.inflight`
- `sid not in _supervisor.closing`
- `sid not in _registry.loading` — **the condition the original four missed.** A session mid-`session/load` is registered before the round-trip (`:1846`) and has **zero subscribers by construction** (`:812-825`), so it satisfied every other condition and would have been terminated mid-load, after which `_handle_load`'s failure path (`:1861`) pops an already-removed session and `_deliver_load` (`:2289`) replays a dead one to parked waiters.

Then `_supervisor.closing.add(sid)` **before the first await**, `discard` in a `finally` — mirroring `_handle_close` (`:2688`, `:2703`). Without the claim, `close_session` leaves the session in `sessions` and out of `closing` for the whole terminate round-trip (first await at `:1928`), so a prompt arriving in that window passes every guard at `:2517-2547` and starts a turn on a session being released: the window `acp.py:2528-2540` exists to close.

Sweeping calls `_supervisor.close_session(sid)` directly, then broadcasts `session_closed` and detaches, reproducing `_handle_close`'s notification half (`:2708-2711`) **without** relaxing its `not_subscribed` guard.

Failure handling: catch **`Exception`**, not `BaseException` — `CancelledError` is a `BaseException` since 3.8, and swallowing it would hold teardown for up to `REQUEST_TIMEOUT_SECONDS` against `__main__.py:375`'s 5 s join. Do not `asyncio.shield` the close, for the same reason. Log at WARNING and continue: if `_kiro.dev/session/terminate` disappears, the sweeper degrades to memory growth, never a crashed task.

**Change 4 — `MAX_SESSIONS` from config** (shape per D29). Add to `Config` (`config.py:51`): `acp_max_sessions: int = 8`, `acp_idle_ttl_seconds: int = 1800`, `acp_prompt_silence_seconds: int = 900`. Read **once at startup** in `__main__._run_foreground` (`:292`, where `load_config()` already runs) and rebind the module-level names in `acp` — `acp.MAX_SESSIONS` must stay a module attribute, because nine test sites read `acp_mod.MAX_SESSIONS` (`tests/test_web.py:3486`, `:3751`, `:3891`, `:4006`, `:4579`, `:4584`, `:4590`, `:4592`, `:4845`) and an attribute move breaks all nine. The setter must tolerate `acp is None` (`web.py:41-49`).

Do **not** call `load_config()` from `at_capacity()` — it runs on the loop and `load_config` is an uncached full TOML parse (`config.py:139-268`).

`_SETTING_TYPES` (`web.py:1857`) is a bare `key → type` map with no range support and its only range branch today is `if key == "port"`. Extend it with explicit bounds: `acp_max_sessions` 1-16, `acp_idle_ttl_seconds` 300-86400, `acp_prompt_silence_seconds` 60-7200. Because all three are read only at startup, the settings UI must say **restart to apply** rather than silently doing nothing.

`TestAcpSessionCapMessage` (`tests/test_web.py:4030`) asserts the literal `"254 mb"`; the measured marginal figure is now ~150 MB. Update the message (`acp.py:1055-1068`) and the assertion together.

**Exit criteria**:
- [ ] A turn streaming past 600 s completes rather than raising `AgentTimeout`
- [ ] A turn emitting only unhandled `sessionUpdate` kinds for 20 min is **not** cancelled — stamping is above the dispatch
- [ ] A turn silent past the configured window fails **and** `session/cancel` is sent
- [ ] `_request`'s signature is unchanged; all 19 fixed-signature stubs still pass
- [ ] `last_used` is present on a session created but never prompted
- [ ] A `session/update` arriving after the record is gone neither raises nor recreates it
- [ ] An idle session past TTL with no subscriber, no in-flight turn, no in-flight close and no in-flight load is terminated and its `.lock` removed
- [ ] A session mid-`session/load` is never swept
- [ ] A prompt arriving during the sweeper's terminate round-trip is refused, not started
- [ ] A session with an attached subscriber is never swept regardless of age
- [ ] A sweeper failure is logged at WARNING and does not kill the task or the app
- [ ] Cancelling the sweeper during shutdown completes within the teardown budget; `acp.py:277-288`'s arithmetic still holds
- [ ] An `acp` import failure still yields a running app with `/acp` disabled, not a startup crash
- [ ] `MAX_SESSIONS` defaults to 8, comes from config, remains a module attribute, and `at_capacity()` performs no disk I/O
- [ ] **Eight concurrent sessions created and driven**, with process count and RSS recorded (SC-10b)
- [ ] Out-of-range values for the three new keys are rejected with a named error
- [ ] Four test edits landed: `:5033`/`:5044` key set, `:4030` cap figure, `:2658` `_request` tuple, `:3723` docstring
- [ ] A prompt on a session idle longer than the silence window is **not** cancelled before the agent has had a full window to answer
- [ ] An agent metadata/heartbeat notification advances `last_activity` but **not** `last_used`, so it cannot keep an idle session unsweepable
- [ ] A turn past `PROMPT_ABSOLUTE_MAX_SECONDS` is cancelled and its session becomes reclaimable
- [ ] Agent death during a prompt surfaces the typed `AgentDied`, not a `TypeError`-derived `internal_error`
- [ ] The sweeper task sleeps before its first work item; a tick with zero sessions still yields to the loop
- [ ] A session closed by the user between snapshot and sweep is skipped, not re-terminated with a WARNING
- [ ] Sweeper and refresh tasks are cancelled and awaited inside the nested `finally`, before `acp.shutdown()`

---

### Phase 3: Remote access — bind, allowlist, and the device cookie [QA]
**Goal**: Make PowerAtlas reachable on the NetBird interface with a default-deny path allowlist enforced on both HTTP and WebSocket, behind a device secret.
**File scope**: `src/power_atlas/web.py`, `src/power_atlas/__main__.py`, `src/power_atlas/config.py`, `tests/test_web.py`
**Covers**: SC-1, SC-2, SC-3, SC-3b, SC-4, SC-5
**Blocked by**: ~~Phase 0's NetBird policy verification~~ — **gate overridden by user decision on 2026-07-31 (D33)**. Phase 0 measured that no restricting policy exists, and the user elected to proceed with the cookie as the sole authorization layer. **Does not block Phases 4 or 5** — they need one allowlist entry each, registered in a small integration step.

> **Security posture of this phase changed after planning.** D3 designed two independent layers because "bind, Host allowlist, ACL reachability and `_ACP_TOKEN` all fail together on a misconfigured policy". One of those two layers is now absent, so every control in this phase is load-bearing in a way the original design did not require: the secret's fail-closed path, the constant-time compare, the `issued_at` expiry, the `device_id` charset bound, and the `scope["client"]` remoteness signal each go from "second line of defence" to "the line". Review this phase's diff accordingly.

**Change 1 — dual bind via pre-bound sockets** (D23, D25, D27). Add `remote_bind_address: str = ""` to `Config`.

**Validate on the write path, not in `load_config()`.** `load_config` is implemented and documented as never raising — wrong types silently get defaults and unknown keys are ignored (`config.py:139-140`, `:157-166`, `:220-256`) — and it is called from ~16 routes on the event loop, so a raising validator turns one `config.toml` typo into a 500 on every route plus a startup crash. Validation therefore lives in `save_setting` (`web.py:1866-1893`, which needs `remote_bind_address` added to `_SETTING_TYPES` — the plan previously added only the three `acp_*` keys) and in the first-enable flow. At load, sanitise an invalid value to `""` and log at ERROR: **fail closed to loopback-only**. SC-3b's "named error" is met on the write and startup paths.

Reject on **parsed properties**, never string equality — `ip.is_unspecified or ip.is_multicast or ip.is_loopback`. A string comparison against `"0.0.0.0"`/`"::"` is bypassed by `::0`, `0000::` and `::ffff:0.0.0.0`, and would let `remote_bind_address = "127.0.0.1"` produce two listeners on the same loopback address and port inside one process. `ipaddress.ip_address()` also enforces D2's "IP, never FQDN" mechanically rather than by convention, and rules out the bracketed, uppercase and zone-id forms `_host_allowed`'s parser would silently never match. Reject a zero `port` when the address is set (SC-3b).

Replace the `uvicorn.Config(...)` + `server.run()` pattern at `__main__.py:308`/`:315` and `:328`/`:334`:

```python
def _bind(host: str, port: int) -> socket.socket:
    """Do NOT copy uvicorn.Config.bind_socket (config.py:571).

    It sets SO_REUSEADDR, which on Windows lets a DIFFERENT LOCAL PROCESS
    bind the identical 127.0.0.1:<port> and hijack connections to a surface
    that serves _ACP_TOKEN and fronts `kiro-cli acp -a`. It also sets
    set_inheritable(True) (:583), which we do not want either.
    """
    s = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET,
                      socket.SOCK_STREAM)
    if sys.platform == "win32":
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    s.set_inheritable(False)
    s.bind((host, port))
    s.listen()
    return s

# Loopback is MANDATORY and keeps its port-in-use fallback. Only the remote
# bind may degrade - a remote-only listener with no loopback is a state the
# whole model assumes cannot exist.
try:
    socks = [_bind("127.0.0.1", port)]
except OSError:
    socks = [_bind("127.0.0.1", 0)]          # existing random-port fallback
    port = socks[0].getsockname()[1]
if cfg.remote_bind_address and _secret_is_usable():
    try:
        socks.append(_bind(cfg.remote_bind_address, port))
    except OSError as exc:                   # WinError 10049: interface not up
        log.error("remote bind to %s failed (%s); loopback only",
                  cfg.remote_bind_address, exc)
log.info("listening on %s", [s.getsockname() for s in socks])

server = uvicorn.Server(uvicorn.Config(
    app, ...,
    # ProxyHeadersMiddleware OVERWRITES scope["client"] from X-Forwarded-For
    # for peers in forwarded_allow_ips (proxy_headers.py:52-60), and
    # proxy_headers defaults to True (config.py:207). FORWARDED_ALLOW_IPS=*
    # in the environment would silently reopen exactly the class of bug D26
    # exists to close. PowerAtlas is never behind a proxy.
    proxy_headers=False,
))
# run() blocks, so the existing thread + ready_event scaffolding
# (__main__.py:300-317) must be kept or tray and peek never start.
threading.Thread(target=server.run, kwargs={"sockets": socks},
                 daemon=True).start()
```

`Server.run(sockets=…)` is supported on the installed uvicorn 0.49.0 (`server.py:74`). One `Server` means **one lifespan** — two `Server` instances would start two `_background_refresh` loops, two sweepers racing on the same sessions, and call `acp.shutdown()` twice.

**Check the secret before binding**, not at request time: if `remote-secret` is absent or shorter than 43 characters (`secrets.token_urlsafe(32)`), do not create the remote socket at all. Otherwise the remote surface is bound and accepting while authentication is structurally impossible.

`Server.startup` skips its "Uvicorn running on…" line entirely when `sockets` is passed (`uvicorn/server.py:188-193`), which is why the explicit `log.info` above matters — it is the only operator-visible record of which addresses are live, precisely when there are two.

**Change 2 — `_ALLOWED_HOSTS` learns the configured IP at startup.** It is a module-level `frozenset` (`web.py:558`) read on every request and every upgrade, and `web.py` never calls `load_config()` at import. Both obvious options are traps: a per-request `load_config()` puts an uncached TOML parse on the hot path (the very thing D15 forbids), and an import-time read makes the host tests depend on the developer's real `config.toml` (`memory/MEMORY.md:95-97`). Use a **startup setter** mirroring D15's `acp` injection, defaulting to loopback-only when unset.

`_host_allowed`'s parser (`:570-620`) is already host-agnostic except its final membership test (`:620`), so only that line changes. Note the IPv6 normalisation trap: the parser strips brackets (`:605-609`) and lowercases (`:620`), so an IPv6 bind must be stored unbracketed and lowercase, and a zone id (`%eth0`) is not in the reject set at `:603` — the `ip_address()` validator above prevents all three from reaching it.

**Change 3 — one raw-ASGI middleware enforcing allowlist and cookie** (D7 revised, D26).

```python
def _is_remote_peer(peer: str | None) -> bool:
    """Defined here, not left to implementation time - the whole model
    collapses to whichever predicate someone writes. "peer != bind_address"
    and "peer in an allowlist" are both wrong; only "is it loopback?" is
    right, and unparseable or absent means remote."""
    if not peer:
        return True
    try:
        return not ipaddress.ip_address(peer).is_loopback
    except ValueError:
        return True

# Registered AFTER same_origin_guard (web.py:690) so this guard is OUTERMOST:
# add_middleware inserts at index 0 (starlette/applications.py:101) and
# build_middleware_stack wraps reversed(middleware) (:75), so LAST registered
# is outermost. Deny decisions survive either order, but the refusal body and
# whether the exchange-failure WARNING fires both flip.
async def __call__(self, scope, receive, send):
    if scope["type"] in ("http", "websocket"):
        if _is_remote_peer((scope.get("client") or (None,))[0]):
            path = scope["path"]
            if not _remote_path_allowed(path):
                return await _refuse(scope, send)
            if path not in _COOKIE_EXEMPT and not _cookie_ok(scope):
                return await _refuse(scope, send)
    await self.app(scope, receive, send)

async def _refuse(scope, send):
    """Scope-typed. Emitting http.response.start into a websocket scope is an
    ASGI protocol violation and surfaces as a uvicorn exception, not a
    refusal - on the very path this middleware exists to guard."""
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
    else:
        await send({"type": "http.response.start", "status": 403, ...})
        await send({"type": "http.response.body", "body": b'{"error":"Forbidden"}'})
```

**Path matching is exact for the four fixed paths**; the mount is `path == "/static" or path.startswith("/static/")` — a bare `startswith("/static")` would also admit `/staticfoo`. `_COOKIE_EXEMPT` is path-only, so the exchange route must reject methods other than GET/POST itself.

Remoteness comes from `scope["client"]`, never the `Host` header (D26). The allowlist is **default-deny**: `/acp`, `/ws/acp`, `/static/*`, the Phase 4 listing endpoint, **and the two secret-exchange routes** — without those last two, no remote device can ever authenticate, because the exchange page would itself be refused. The exchange GET and its POST target are the only cookie-exempt remote paths; its POST must still satisfy `same_origin_guard`'s Origin rule (`web.py:714-716`).

Apply `_acp_navigation_ok`'s `Sec-Fetch-Site` rule (`web.py:662-687`) to remote-allowlisted **`http`-scope GETs only** — cookies are host-scoped and port-agnostic, so another service on the NetBird interface is "same-site" for `SameSite=Strict`. Exclude the `/ws/acp` upgrade: it is a GET at the HTTP layer but a `websocket` ASGI scope, and browsers do not attach `Sec-Fetch-Site` to WebSocket handshakes, so the literal reading would break the phone client. Note the rule falls back to `_origin_or_referer_ok(allow_missing=True)` when the header is absent (`web.py:684-687`), so it constrains browsers only — **the cookie, not this rule, is the control against a non-browser remote client.**

**Accepted residual, stated rather than implied**: the cookie is port-agnostic in the *outbound* direction too. The phone transmits it to every service listening on any port of `100.78.142.124`, so any other process bound to `0.0.0.0` on the laptop can harvest it — and its payoff is full remote access to a `-a` agent. `SameSite` does not address this direction and the design does not mitigate it. The README security note must therefore say plainly: **do not bind other services to `0.0.0.0` while the remote bind is enabled.**

**Change 4 — the device secret and cookie** (D24, D8).

Generate `CONFIG_DIR/remote-secret` (`secrets.token_urlsafe(32)`) on first enable, loaded **once at startup**. Missing, empty, or shorter than the expected length ⇒ **fail closed**: refuse every remote request and log at ERROR. An empty secret compared with `compare_digest` would otherwise match an empty cookie and silently remove authentication while the bind stayed open.

The cookie signs **`(device_id, issued_at)`**, keyed by that secret, compared in constant time over UTF-8 **bytes** — reuse `_acp_token_ok`'s pattern (`web.py:735-746`), which already encodes the lesson that a `str` holding non-ASCII raises `TypeError` out of `compare_digest` and turns a 403 into a 500 any unauthenticated caller can drive. Including `issued_at` gives expiry **without a server store**, which is D24's whole premise; without it "long-lived" means eternal, since D24 also gives up per-device revocation. Reject beyond a configured age and set a matching `Max-Age` (default 90 days). `HttpOnly`, `SameSite=Strict`; no `Secure` (D5 — no TLS, WireGuard carries the transport).

`device_id` must be bounded to `[A-Za-z0-9_-]` and a maximum length, and the cookie set via Starlette's `response.set_cookie` (which raises `CookieError` on illegal characters) rather than a hand-assembled header — `src/` sets no cookies today, so there is no safe pattern to inherit, and a `;`, `,` or CR-LF in a client-supplied identifier is cookie-attribute or header injection. The same bound prevents log injection when the identifier appears in the WARNING line.

Log every failed exchange at WARNING with the peer address and apply a per-peer backoff. D3 makes the cookie "the layer that survives policy drift" — without failure logging, drift is never observable.

On Windows, "restrictive permissions" via `os.chmod` toggles only the read-only attribute and never touches ACLs, so say plainly that the protection is `%LOCALAPPDATA%`'s inherited ACLs, or use `icacls`. Assert the secret never reaches `orchestrator.log` (`__main__.py:274-283`).

**Change 5 — the loopback-encoding tests.** The honest figure is **77 distinct test functions** touching host/origin/websocket (~153 is the *collected* count, inflated by `_HOSTILE_HOSTS` 15 × `_GUARDED_PATHS` 4 = 60 in one method). None of the 15 hostile hosts is a NetBird IP and the bind is off by default, so most pass unchanged. The real work is four new scenario families plus whatever the `_ALLOWED_HOSTS` shape change forces:

- remote peer + allowlisted path + valid cookie → allowed
- remote peer + non-allowlisted path → refused (`/`, `/api/launchers`, `/api/settings`)
- remote peer + no cookie → refused, on HTTP **and** on the `/ws/acp` upgrade **and** on `ws://…/static/…`
- `_HOSTILE_HOSTS` still refused **with the remote bind on** — the fixture docstring at `:26-29` gets updated, not contradicted

**Change 6 — settings surface and stale comments.** Show the reachable URL and secret as copyable text (D22) and state **restart to apply**. Correct three stale comments: `web.py:709-711` ("a non-loopback Host cannot arise legitimately"), and `web.py:830-831`, which claims the CSP host "carries no port PowerAtlas is not actually serving on" — `_PORT_RE` (`:562`) proves digits, not range, so `:99999` passes into `connect-src`.

`__main__.py:345` derives `server_url` from `server.servers[0].sockets[0]`; with two sockets, index 0 is merely whichever bound first, so the loopback URL must come from the loopback socket explicitly. It feeds `create_peek` (`peek.py:267`) and `run_tray` (`tray.py:47`).

**Exit criteria**:
- [ ] With the remote bind unset: exactly one listening socket, and it is loopback (SC-2)
- [ ] With it set: exactly two sockets, on the same port number, on loopback and the configured IP
- [ ] A zero `port` with the remote bind set is rejected at config load with a named error (SC-3b)
- [ ] A bind failure on the remote address logs at ERROR and leaves a working loopback app — not exit 1
- [ ] `remote_bind_address` rejects `0.0.0.0`, `::`, hostnames and zone-id forms
- [ ] `_ALLOWED_HOSTS` admits the configured IP, still rejects single-label and lookalike hosts, and is populated without any per-request config read
- [ ] Remoteness is derived from `scope["client"]`; a remote peer sending `Host: 127.0.0.1` is still treated as remote
- [ ] A remote request to a non-allowlisted path is refused, verified for `/`, `/api/launchers`, `/api/settings`
- [ ] `ws://<remote-ip>:<port>/static/style.css` is refused — the `/static` Mount bypass is closed
- [ ] A remote `/ws/acp` upgrade without a valid cookie is closed, exercised through the socket path
- [ ] A missing, empty or short `remote-secret` refuses **all** remote requests and logs at ERROR
- [ ] The exchange GET and POST are reachable without a cookie and are the only such paths
- [ ] A failed exchange logs at WARNING with the peer address; repeated failures are backed off
- [ ] Loopback requests need no cookie and are unaffected
- [ ] The cookie survives a PowerAtlas restart without re-entering the secret (D24)
- [ ] `server_url` is derived from the loopback socket explicitly; peek and tray still open the dashboard
- [ ] All three stale comments corrected (`web.py:709-711`, `:830-831`, and the fixture docstring at `tests/test_web.py:26-29`)
- [ ] `_HOSTILE_HOSTS` still refused with the remote bind **on**
- [ ] `X-Forwarded-For` cannot move `scope["client"]` — `proxy_headers=False` asserted at the `uvicorn.Config` site
- [ ] The loopback socket sets `SO_EXCLUSIVEADDRUSE` on Windows and **not** `SO_REUSEADDR`; a second process cannot bind the same `127.0.0.1:<port>`
- [ ] A loopback bind failure still falls back to a random port; the app never starts remote-only with no loopback listener
- [ ] A websocket refusal emits `websocket.close`, not `http.response.start` — no ASGI protocol violation on the guarded path
- [ ] The remote guard is registered after `same_origin_guard` and rejects first
- [ ] An invalid `remote_bind_address` in `config.toml` sanitises to loopback-only and logs ERROR; it does **not** raise from `load_config()`
- [ ] `0.0.0.0`, `::`, `::0`, `0000::`, `::ffff:0.0.0.0` and loopback literals are all rejected on parsed properties
- [ ] The remote socket is not created at all when the secret is absent or shorter than 43 characters
- [ ] A cookie past its `issued_at` age is rejected
- [ ] Both bound addresses are logged at startup (uvicorn suppresses its own banner when `sockets=` is passed)
- [ ] Tray and peek still start — the server thread scaffolding survives the socket change
- [ ] Full suite green

---

### Phase 4: Listing endpoint — dispatch targets without the dashboard [QA]
**Goal**: A purpose-built read-only endpoint serving workspace-grouped sessions for the browser, safe to expose remotely.
**File scope**: `src/power_atlas/web.py`, `tests/test_web.py`
**Covers**: SC-6 (data half)

Serves workspace groups with their sessions, paginated **independently at both levels** (D19) — the existing listing filters all set `has_more = False` (`web.py:1301`, `:1318`, `:1329`, `:1340`), which would silently truncate the 208-session workspace. Returns only: workspace path and display name, session id, title, updated timestamp, and availability state. **No `env`, no launcher data, no action affordances.**

Availability is the three-state field from D17 — `available` / `held` / `locked` — computed **only for the rows in the response** (~30 by default, not 1,207), off the event loop, and failing open to `available` on any error. `locked` reuses the pid-liveness logic `acp._lock_holder` already implements (`acp.py:956-1012`).

**`held` must be snapshotted on the loop before the thread hop.** `_supervisor.sessions` is loop-owned and unlocked (see §1), and D9 explicitly forbids reading loop-owned state from a worker thread — iterating it under `asyncio.to_thread` while the loop mutates gives a torn read or `RuntimeError: dictionary changed size during iteration`. So:

```python
held = set(_supervisor.sessions)          # on the loop, synchronous
rows = await asyncio.to_thread(_resolve_availability, sids, held)
```

Reuses `data.discover_workspaces_with_counts` (`data.py:189`) and `data_kiro.load_sessions`, inheriting the `parent_session_id` filtering that removes 4,734 sub-agent sessions.

**Allowlist registration** is a one-line integration step gated on Phase 3; the endpoint itself is not, so this phase can complete while the NetBird policy is still pending.

**Exit criteria**:
- [ ] Endpoint returns workspace-grouped sessions with independent paging at both levels
- [ ] A 208-session workspace pages correctly rather than truncating
- [ ] Response contains no `env`, no launcher fields, no action affordances
- [ ] Availability is computed only for returned rows and never for the whole store
- [ ] `_supervisor.sessions` is snapshotted on the loop; no worker thread iterates it
- [ ] Availability computation runs off the event loop and fails open to `available`
- [ ] Sub-agent sessions are absent
- [ ] Endpoint reachable with a cookie and refused without, once registered on the allowlist (integration step, gated on Phase 3)

---

### Phase 5: `/acp` rework — two-pane browser and conversation [QA]
**Goal**: Replace the single-pane prototype page with a searchable workspace-grouped session rail plus the conversation, usable on a phone.
**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/static/style.css`, `tests/acp_page.test.mjs`, `tests/test_web.py`
**Covers**: SC-6 (UI half), SC-1

Left rail: search, workspace groups, **10 groups shown with show-more**, groups **expanded** showing **3 sessions each with per-group paging** (D16). Availability rendered per D17, with `locked` greyed and non-interactive. Right pane: the existing conversation, composer pinned.

Responsive without a build step: two-pane at **≥768 px**, drill-down below it (rail → conversation with a back affordance), since `style.css` has zero width breakpoints today and `html, body { height: 100%; overflow: hidden }` (`:2`) makes this an app shell. Use `100dvh` rather than `100%` for the shell height so mobile browser chrome collapsing does not clip the composer.

**Constraints, stated accurately**: `/acp` is the only page under CSP (`web.py:811-840`) with a per-response nonce. The policy is `default-src 'self'` with a `script-src` override and **no `style-src`** — so a **same-origin stylesheet is permitted**; an earlier draft of this plan wrongly said otherwise and would have steered the implementer away from a legitimate option. What the policy does forbid is a second inline `<script>` without the nonce. Separately, `tests/acp_page.test.mjs` enforces exactly one `<script>` and the no-`innerHTML` rule by making `innerHTML`/`outerHTML`/`insertAdjacentHTML` throw. The harness is **not** run by pytest or CI (`AGENTS.md:9`) and must be run by hand.

**Split into 5a and 5b**, because the harness work is a distinct risk from the layout work.

#### Phase 5a: Harness capability + rail data binding [QA]
**File scope**: `tests/acp_page.test.mjs`, `src/power_atlas/templates/acp.html`

`tests/acp_page.test.mjs` cannot simply be "extended" — its DOM stand-in has no `classList`, no `querySelectorAll`, no `removeChild` and no attribute API; `querySelector` throws on anything but a class selector (`:110-112`); `document` exposes only `createElement`/`getElementById`/`write` (`:176-181`); `byId` is built by regex over **static** markup (`:157`), so dynamically-created rail rows are unaddressable; `fetch` is `() => Promise.resolve({ok:true})` (`:191`) with no body; and all 15 checks are synchronous with no microtask flush. A rail with search, 10 groups and 30 dynamic rows consuming Phase 4's JSON needs all of that.

**Decide server- vs client-rendered rail first**: `render()` strips all `{% %}` (`:38`) and throws on leftover Jinja (`:46-51`), so a server-rendered `{% for %}` renders one iteration's body silently. Client-rendered avoids that but hits the fetch-body gap. **Recommended: client-rendered**, with the harness gaining a fetch-body stub and an async flush.

**Exit criteria**:
- [ ] Harness supports dynamic element lookup, a fetch body, and async checks
- [ ] Rail renders groups and rows from Phase 4's payload shape
- [ ] Search filters rows; 10 groups + show-more; 3 sessions + per-group paging
- [ ] Availability indicator renders three states; `locked` is greyed and cannot be selected
- [ ] Still exactly one `<script>`, still nonce-carrying, still no `innerHTML`
- [ ] `node tests/acp_page.test.mjs` green, with the added checks enumerated in the phase log

#### Phase 5b: Responsive layout and conversation integration [QA]
**File scope**: `src/power_atlas/static/style.css`, `src/power_atlas/templates/acp.html`

**Exit criteria**:
- [ ] Two-pane at ≥768 px; drill-down below, verified at 390 px and 768 px
- [ ] `100dvh` shell; the composer stays visible with a soft keyboard open on a real device (manual, device named in the phase log)
- [ ] Selecting a rail row loads that session and streams a turn
- [ ] Creating a new session still works from the reworked page
- [ ] `acp.html:4`'s back link no longer points at `/`, which is loopback-only and would 403 on a phone
- [ ] `node tests/acp_page.test.mjs` green; full pytest suite green

---

### Phase 6: Documentation
**Goal**: Complete the README sites the throwaway exemption deferred, and correct the stale roadmap claim.
**File scope**: `README.md`, `plans/ROADMAP.md`, `plans/CLOSED_INVESTIGATIONS.md`, `plans/tests/260701_POWERATLAS.md`
**Covers**: SC-12

`AGENTS.md:7`'s exemption ends on promotion — "promoting it to product is what makes the README row required work". **Line references re-derived** (the first draft's were wrong): the product definition is `README.md:3`, the "click to open the dashboard UI" line is **`:52`** (not `:30`, which is inside the Linux `apt install` block), and the feature list starts at **`:54`** (not `:32-53`). A fourth site the first draft missed: the config sample block at **`:83-127`**, which gains four documented keys, plus `:84`'s `port = 0` default (now conditional per D25) and `:92`'s `default_args = "-a"` line — the natural home for the security expectation.

`plans/tests/260701_POWERATLAS.md` is required here by `memory/MEMORY.md:82-86`, a standing instruction to add it to the Documentation Updates table **at planning time** when a plan changes cache layers or client-timer topology. Six sites: `:152-159` and `:559-561` scope the ACP surface out of test coverage on throwaway grounds that no longer hold; `:266-271` describes `lifespan` as owning two concerns (D14 adds a third); `:315-320` enumerates the `_SETTING_TYPES` allowlist; `:322-327` describes the `?sid=` page shape the rework changes; `:489-494` needs a bind-address case. Its `:156-157` control list is the only doc enumerating the ACP surface's security controls, and Phase 3 adds two.

**Exit criteria**:
- [ ] README's four sites describe the remote client, how to enable it, and the four new config keys
- [ ] README states the NetBird policy is the primary authorization layer and the cookie the second
- [ ] `README.md:84` records that a fixed port is required when the remote bind is on
- [ ] `plans/ROADMAP.md` corrected at `:62`, `:66` and `:120` (three sites of the POST-only claim), `:36` ("no idle sweeper anywhere"), `:65` ("none chosen" — D4 chose), `:54` (rebuild dependency retired by D1), and the ACP entry's rebuild verdict at `:124`, `:152`, `:154`, `:160-163`
- [ ] `plans/ROADMAP.md:200` — the `## Misc` "Local network access to mimic claude code remote control" item, which is what this plan actually ships — updated; and `:8`'s header pointer given a carve-out so it does not read as closing this
- [ ] `plans/CLOSED_INVESTIGATIONS.md:67-77` updated: the "no independent path" verdict and `:75`'s "throwaway prototype" wording
- [ ] `plans/tests/260701_POWERATLAS.md` updated at all six sites, including the `:156-157` control list
- [ ] A memory update for `memory/MEMORY.md:149-153` is **proposed** to the user (falsified clauses: permission round trip never happened; unidentified trigger; no independent path) — proposed, not written directly, since a plan phase must not silently rewrite a memory entry

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| An HTTP-only guard leaves **two** websocket paths exempt — `/ws/acp` and `ws://…/static/…` via the `Mount` | **Critical for `/ws/acp`** (arbitrary command execution from any reachable peer, once `_ALLOWED_HOSTS` widens and `_ws_origin_ok` stops covering it). **Low for `/static`** — `StaticFiles.__call__` asserts `scope["type"] == "http"` (`starlette/staticfiles.py:91`), so the pre-fix impact there is an unauthenticated `AssertionError`: log spam and minor DoS, not execution. *An earlier draft rated both Critical; corrected in cycle 2.* | D7 revised to one raw-ASGI middleware seeing every scope type; SC-3 tests HTTP, the `/ws/acp` upgrade and the `/static` websocket path separately |
| `proxy_headers` defaults to `True`, so `ProxyHeadersMiddleware` rewrites `scope["client"]` from `X-Forwarded-For` for trusted peers | **Critical if `FORWARDED_ALLOW_IPS=*` is set** — D26's whole basis becomes an environment variable. The shipped default (`127.0.0.1`) leaves a NetBird peer untrusted, so the practical hole is closed by default and silently reopened by config | `proxy_headers=False` passed explicitly at the `uvicorn.Config` site, with an exit criterion asserting `X-Forwarded-For` cannot move `scope["client"]` |
| A local process binds the same `127.0.0.1:<port>` and hijacks the ACP surface | High on Windows: `SO_REUSEADDR` permits it, and the hijacked surface serves `_ACP_TOKEN` and fronts a `-a` agent | `_bind()` specified explicitly — no `SO_REUSEADDR`, `SO_EXCLUSIVEADDRUSE` on win32, non-inheritable — rather than copying `uvicorn.Config.bind_socket` (`config.py:571`, `:583`) |
| The device cookie is transmitted to **every** service on any port of the NetBird address | High, **unmitigated**: any process bound to `0.0.0.0` on the laptop harvests full remote access to a `-a` agent. `SameSite` addresses only the inbound direction | **Accepted, stated in writing** (Phase 3 Change 4) and carried into the README security note: do not bind other services to `0.0.0.0` while the remote bind is enabled |
| An agent heartbeat notification refreshes the sweeper's idle clock | High: a chatty agent makes sessions permanently unsweepable, defeating SC-9/SC-10 with no error anywhere | Two fields — `last_activity` (any notification, drives the ceiling) and `last_used` (prompt/attach, drives the sweeper) |
| A turn emitting one chunk just under the silence window runs forever | High: `inflight` makes it simultaneously un-closable and un-sweepable, so ~150 MB is unreclaimable for the app's lifetime | `PROMPT_ABSOLUTE_MAX_SECONDS` (4 h) checked in the same loop |
| Remoteness derived from the `Host` header lets a peer self-classify as local and skip every check | Critical: defeats SC-4 and SC-5 entirely | D26 — remoteness comes from `scope["client"]`, set by the transport; `client is None` treated as remote |
| A missing or empty `remote-secret` compared with `compare_digest` matches an empty cookie | Critical: authentication silently disappears while the bind stays open | Fail closed — refuse all remote requests and log at ERROR; exit criterion in Phase 3 |
| The sweeper terminates a session mid-`session/load`, which has zero subscribers by construction | High: a load in flight is destroyed; `_deliver_load` replays a dead session to parked waiters | D28 adds `_registry.loading` as a fifth sweep condition |
| The sweeper's check-then-close is not atomic, so a prompt can start a turn on a session being released | High: reopens the ungated-tool window `acp.py:2528-2540` closes | D28 claims `closing` in a synchronous prefix, `discard` in `finally` |
| A bind failure on the configured remote address kills startup | High: app will not start after a NetBird hiccup, since `__main__.py:322`'s retry covers only port-in-use | D27 — bind loopback first, log and continue on remote failure |
| Our own agent's orphaned locks read live indefinitely once the skew ceiling is dropped | Medium: a session the agent does not hold shows a live dot and a `locked` indicator, for the agent's whole life | **Accepted** (D32) — the only alternative is the `presence → acp` coupling D9 forbids |
| `_ALLOWED_HOSTS` populated by a per-request config read puts an uncached TOML parse on the hot path | Medium: the stall D15 forbids elsewhere | Startup setter mirroring D15; exit criterion forbids a per-request read |
| NetBird account has 17 peers; reachability is not authorization | **High, and no longer mitigated in depth.** Phase 0 measured all 17 peers in this host's network map, so every one of them can reach the port. A colleague's device *does* reach the instance; only the cookie stops it | ~~Phase 0 gates Phase 3 on policy verification~~ (gate overridden, D33). **The cookie is now the sole control.** Residual accepted by the user 2026-07-31 with the consequence stated. Re-mitigate by creating a restricting NetBird policy at any time — nothing in the implementation depends on its absence |
| `-a` behind a remote surface is arbitrary command execution as the user | High, accepted knowingly | Two independent layers (policy + cookie); default-deny allowlist; bind off by default |
| Random-port fallback reverts to loopback if only `__main__.py:308` changes | Medium: silent loss of remote access, or worse, a silent bind mismatch | Explicit exit criterion that both sites read one value |
| `custom_launchers[].env` remains readable | Medium, accepted (D4) | Loopback-only routing is the sole protection; no remote-reachable route returns it |
| Widening `_ALLOWED_HOSTS` to a hostname re-opens LLMNR/mDNS hijack | High | D2 allowlists the IP only; existing single-label rejection tests retained |
| `_kiro.dev/session/terminate` is undocumented with no fallback | Medium: sweeper stops reclaiming | Phase 0 re-verifies; sweeper failure is non-fatal and logged |
| The 250 white-box ACP tests break on any structural change | Medium: churn | D1 keeps the module; Phase 2's changes are additive except the two by-design test edits |
| Clock skew stamping a lock in the future is no longer rejected once the upper bound is dropped | Low: a stale lock reads as live | Accepted; recorded in D10 |
| Every mutating route is a lost-update race (`load_config` → mutate → `save_config`) | Medium, pre-existing; a second concurrent client makes it likelier | **No remote-reachable route writes `config.toml`** — every mutating route is `@app.post` and none is on the allowlist. Note the narrower claim: `/acp` *is* state-changing by design (`web.py:564-567` — rendering it spawns the agent) and `/ws/acp` is command execution by design, so "no remote-reachable write routes" would be false |
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
| `README.md:3`, `:52`, `:54-75` | Product definition, dashboard-open line, feature list — cover remote access and how to enable it (`AGENTS.md:7` exemption ends on promotion) | 6 |
| `README.md:83-127` | Config sample gains `remote_bind_address`, `acp_max_sessions`, `acp_idle_ttl_seconds`, `acp_prompt_silence_seconds`; `:84`'s `port = 0` becomes conditional (D25); `:92`'s `-a` line carries the security expectation | 6 |
| `plans/ROADMAP.md:62`, `:66`, `:120` | Three sites of the stale POST-only `same_origin_guard` claim | 6 |
| `plans/ROADMAP.md:200`, `:8` | The `## Misc` remote-control item this plan ships, and the header pointer that reads as closing it | 6 |
| `plans/ROADMAP.md:36`, `:54`, `:65` | "No idle sweeper anywhere" (falsified by Phase 2); the sub-agent-pipeline item's dependency on a rebuild (retired by D1); "shapes worth considering, none chosen" (D4 chose) | 6 |
| `plans/ROADMAP.md:124`, `:152`, `:154`, `:160-163` | The ACP entry's throwaway framing and rebuild verdict, retired by D1 | 6 |
| `plans/ROADMAP.md:16`, `:88`, `:126` | Section-level version pins and the superseded `~5.4 s` / `~2.5 s` figures | 6 |
| `plans/CLOSED_INVESTIGATIONS.md:67-77` | "Closed — remote control for kiro-cli": the "no independent path" verdict and `:75`'s "throwaway prototype" wording | 6 |
| `plans/tests/260701_POWERATLAS.md` | Six sites: `:152-159` and `:559-561` (ACP scoped out of coverage on throwaway grounds), `:266-271` (lifespan gains a third concern), `:315-320` (`_SETTING_TYPES` enumeration), `:322-327` (`?sid=` page shape), `:489-494` (bind-address case), `:156-157` (security-control list gains two) — **required at planning time by `memory/MEMORY.md:82-86`** | 6 |
| `memory/MEMORY.md:149-153` | Falsified clauses (permission round trip; unidentified trigger; no independent path) — **propose to the user, do not write directly** | 6 (doc-table-only) |
| `src/power_atlas/presence.py` docstrings | Remove the falsified write-once premise; scope `_SIDECAR_SKEW_S`'s comment to the Claude branch | 1 |
| `src/power_atlas/acp.py` module comments | Version-pinned figures (`~254 MB`, `~5.4 s`, `2.14.1`) superseded by the 2026-07-31 measurements; `:339-342`'s claim that `fromisoformat` cannot parse kiro's 9-digit fraction is stale on Python 3.13; `:277-288`'s teardown arithmetic now includes the sweeper; `:1074-1076`'s "pays nothing" invariant is amended by the tick guard | 2 |
| `src/power_atlas/web.py:709-711`, `:830-831` | Middleware rationale asserting a non-loopback Host "cannot arise legitimately"; CSP comment claiming the host carries no unserved port | 3 |
| `tests/test_web.py:26-29`, `:3723`, `:4484` | Fixture docstring on loopback base URLs; "MAX_SESSIONS is 3"; stale `_request` timeout docstring | 2, 3 |

## Progress Tracker

| # | Phase/Task | Status | Notes |
|---|---|---|---|
| 0 | Pre-flight verification | Complete — 5 of 7 criteria | terminate re-verified; cancel measured; baselines captured. The two NetBird criteria are **refuted**, not deferred |
| 1 | Presence fixes | Not started | |
| 2 | Session lifecycle | Not started | Design amendments pending from Phase 0's cancel measurement |
| 3 | Remote access | Not started | Gate **overridden** (D33): no NetBird policy exists, all 17 peers reach this host, and the user elected to ship with the cookie as sole authorization layer |
| 4 | Listing endpoint | Not started | Allowlist registration deferred to the integration step |
| 5a | Harness + rail data binding | Not started | |
| 5b | Responsive layout + integration | Not started | |
| I | Integration: register endpoint + page on the remote allowlist | Not started | Needs 3, 4, 5b |
| 6 | Documentation | Not started | |

## Dependency Graph

```
Phase 0 (pre-flight)
   |
   +--> Phase 1 (presence.py)
   |
   +--> Phase 2 (acp.py lifecycle)
   |
   +--> Phase 4 (listing endpoint) --> Phase 5a (harness+rail) --> Phase 5b (layout)
   |                                                                    |
   +--> Phase 3 (remote access)                                         |
        [gated on NetBird policy]                                       |
                       |                                                |
                       +----------------> Integration step <------------+
                                                 |
                                                 v
                                          Phase 6 (docs)
```

**Phases 4, 5a and 5b no longer sit behind Phase 3** (cycle-1 finding). They need it for one allowlist entry each, and Phase 3 is gated on a NetBird policy administered by other people — so an external stall would otherwise block the entire product half. The allowlist registration moves to a small integration step that needs both.

Phases 1, 2, 3 and 4 are logically independent but **all modify `tests/test_web.py`**, so none is annotated `[P:N]` — the parallel-eligibility rule requires non-overlapping file scopes. That is a repo constraint rather than a property of the work; recorded under Harness Improvement Opportunities.

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
- `src/power_atlas/presence.py` — provider-scoped skew check, kiro listing cache, docstrings
- `src/power_atlas/acp.py` — `last_used` (init + stamp above dispatch + safe write idiom), inactivity ceiling with preserved `_request` signature, cancel-on-timeout, sweeper, rebindable config names, cap message
- `src/power_atlas/web.py` — raw-ASGI remote middleware (allowlist + cookie, http and websocket scopes), secret exchange routes, settings surface, listing endpoint, guarded lifespan sweeper start + cancel-and-await, stale comments at `:709-711` and `:830-831`
- `src/power_atlas/__main__.py` — pre-bound sockets replacing both `uvicorn.Config` host sites, bind-failure fallback, config injection into `acp`, and `:345`'s `server_url` derived from the loopback socket explicitly
- `src/power_atlas/config.py` — `remote_bind_address` (IP-literal validated), `acp_max_sessions`, `acp_idle_ttl_seconds`, `acp_prompt_silence_seconds`; zero-port rejection when the remote bind is set
- `src/power_atlas/templates/acp.html` — two-pane rework, back-link fix
- `src/power_atlas/static/style.css` — rail, 768 px breakpoint, `100dvh` shell
- `tests/test_web.py` — four by-design edits (`:5033`/`:5044`, `:4030`, `:2658`, `:3723`), fixture docstring `:26-29`, four new remote scenario families
- `tests/test_data.py` — only if `_dir_listing_cache` is deleted (`:1482` reaches into it)
- `tests/acp_page.test.mjs` — harness capability work (dynamic lookup, fetch body, async flush) plus new checks
- `README.md`, `plans/ROADMAP.md`, `plans/CLOSED_INVESTIGATIONS.md`, `plans/tests/260701_POWERATLAS.md`

### Deleted
- `presence._dir_listing_cache` and the `cache_listing` parameter, **if** the Claude branch also stops caching — dead code is deleted, not commented out

### Unchanged
- `src/power_atlas/data.py`, `data_kiro.py`, `status_classifier.py`, `launcher.py`, `notifications.py`
- `src/power_atlas/peek.py` and `tray.py` — **behaviourally** unchanged, but both consume `server_url` from `__main__.py:345` (`peek.py:267`, `tray.py:47`), so they must be re-verified after the socket change rather than assumed untouched

## 9) Implementation Divergences from Plan

### From Phase 0 (2026-07-31)

| # | Divergence | Rationale | Lands in |
|---|---|---|---|
| P0-1 | **A second orphan class exists that the plan does not name.** `session/cancel` stops the ACP turn but does **not** kill the tool's OS child processes — `pwsh.exe` (63864) and `PING.EXE` (73648) were both alive at response time and still alive 20 s later | Phase 2 names exactly one residual hazard, an orphaned *turn*. Measurement shows the orphaned *process* is the larger problem: after the inactivity ceiling fires, a shell subprocess keeps running under the agent, invisible to `inflight`, to the sweeper's six conditions, and to SC-10b's RSS figure | Phase 2 — amend "Honest scope of the cancel"; add an exit criterion measuring whether `terminate` reaps a running tool (run 8 terminated an *idle* session, so this is unmeasured) |
| P0-2 | **The orphaned-turn risk is smaller than asserted, in the opposite direction from the one the plan anticipated.** `session/prompt` answered `{"stopReason":"cancelled"}` 0.009 s after the cancel — a matched response on the pending future, not a dropped one | Phase 2 warns the eventual response is dropped as "late or unmatched" (`acp.py:1630`) and that a second prompt can interleave. On 2.16.0 the interleaving window is ~9 ms, so `CANCEL_GRACE_SECONDS = 30.0` is generous by roughly three orders of magnitude | Phase 2 — soften the paragraph; `CANCEL_GRACE_SECONDS` may drop substantially |
| P0-3 | **The stamp must key on "has a `sessionId`", not on a method allowlist.** `_kiro.dev/session/update` exists as a method distinct from `session/update`, carries a `sessionId` and a `tool_call_chunk` update, and today falls through `acp.py:1712-1714` | Phase 2's "stamp above the branch dispatch" design is correct only under the session-id reading. `acp.py:1675` names only `METADATA_METHOD`, so a method allowlist would miss this liveness evidence and could cancel a working turn — the regression SC-8 forbids | Phase 2 Change 1 |
| P0-4 | **`_kiro.dev/subagent/list_update` carries no `sessionId`** — the only `_kiro.dev/*` notification observed without one | The stamping helper needs a real null path, not a defensive one. Previously unknown | Phase 2 Change 1 |
| P0-5 | **`tool_call` frames on 2.16.0 carry no `status` field at all** (the dict is `sessionUpdate`/`toolCallId`/`title`/`kind`/`rawInput`/`_meta`) | Any code or test deciding "a tool is running" from `update["status"]` gets `None` on every frame. Detection must key on `sessionUpdate == "tool_call"` | Phase 2 tests |
| P0-6 | **`_kiro.dev/*` notifications look turn-scoped, weakening (not refuting) one leg of the two-field argument.** Zero notifications of any kind arrived over a 20 s post-turn idle window; all 11 `_kiro.dev/*` frames clustered at session start, turn start and turn end | Phase 2 Change 1 justified the `last_activity`/`last_used` split partly on "nothing in Phase 0 verifying that `_kiro.dev/*` notifications are turn-scoped". They now look turn-scoped, bounded by a short observation. The split still stands on its other argument — a prompt-sent/attach signal is semantically different from an agent-liveness signal — but the chatty-agent scenario is weaker evidence than the plan implies | Phase 2 Change 1 — rationale wording only; no design change |
| P0-7 | **The pytest baseline is not green**, so SC-11 is unmeetable as written. `tests/test_web.py::test_search_with_status_filter` fails deterministically (`assert 5 == 3`), reproducibly standalone in 0.76 s | Traced to `e4fced3`, which intentionally hoisted `get_snapshot` out of the status guard so hover actions get `workspace_status` on every card. The assertion and its comment encode the optimization that change traded away. Product behaviour is correct; the test is stale — but the fix is outside this plan's scope | **Escalated to the user** — see Review Log |

*Every divergence above is a measurement contradicting a plan assertion, not an implementation choice. P0-1 and P0-3 change Phase 2's design; P0-2 and P0-6 change its rationale; P0-7 changes a success criterion.*

## Review Log

### 2026-07-31 — Implementation Review (after Phase 0, review deferred to Step 9)

Implementation health: **Green** — there is no tracked-file diff to review. Phase 0's deliverable is measurement, recorded in its implementation notes above; its probe scripts live in the session scratchpad.

Per-phase review deferred per `/qdev` Step 5's Skip rule (no executable code in the repo, no tracked-file change). Step 9's holistic review covers the plan amendments this phase forces.

**Review scaling — user override, recorded per `shared/AGENTS.md § Continuous Improvement`.** The default for a Major-tier plan is a persona set per phase (`/qreview` persona-selection rules) with a two-cycle auto-fix loop, and a multi-persona final review. The user directed **one generic sub-agent per phase review, one for the final review, and a single review cycle each**. Recorded as a (default, override) pair; the reduction in assurance is deliberate and acknowledged, and is worth revisiting specifically for Phase 3, whose diff is the security boundary.

2 findings escalated (1 High, 1 Medium). Both are external to the implementation — neither is a defect in work done this phase.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| P0-A | High | No NetBird policy restricts this peer: all 17 account peers sit in its network map, so D3's primary authorization layer is absent | User: accepted — build Phase 3 anyway with the cookie as sole authorization layer (D33); gate overridden, consequence recorded |
| P0-B | Medium | SC-11 requires green `pytest`, but `test_search_with_status_filter` fails deterministically from a stale assertion left by `e4fced3` | Fixed — assertion and its comment corrected in `1980b52`, committed outside the plan slug; suite now 1043 passed, 2 skipped |

Both escalations were resolved by the user in the same turn. P0-A was presented with the security consequence stated plainly — a `-a` agent is arbitrary command execution as the user, reachable by 17 peers if the cookie has any flaw — and the user reaffirmed, so it is recorded as D33 rather than treated as an unexamined default. P0-B's fix restores a green baseline, which is what makes "full suite green" a usable exit criterion at every later phase boundary rather than a number to ignore.

On P0-A the measurement is stronger than the plan's criterion anticipated: the criterion asked whether a restricting policy exists and expected the answer to be unobtainable without console access, but the policy's *effect* is visible locally through NetBird's network-map distribution. The answer is negative rather than unknown. The narrower residual — a policy admitting all 17 peers at the network layer while restricting protocol or port — is not settleable from here and does not move the gate.

### 2026-07-31 — Cycle 2 (via /qplan Step 4)

Two focused sub-agents re-reviewing the two phases that were **rewritten** rather than patched: Security auditor on Phase 3, Reliability engineer on Phase 2. Both were asked to verify their own cycle-1 findings genuinely closed rather than got reworded, and to hunt defects the rewrite introduced.

**Cycle-1 closure**: of 18 re-checked items, **12 CLOSED, 6 PARTIALLY CLOSED, 0 NOT CLOSED.** The partials are the findings below. Reliability confidence 85%, Security 60% as-specified (85% with S1/S2/S4/S5/S6/S7 written in — all now written in).

**34 new findings (7 High, 9 Medium-High/Medium, 18 Low). All applied.** Every one was introduced or left open by the cycle-1 revision, which is the point of re-reviewing a rewrite.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| C2-1 | High | `proxy_headers` defaults to `True`, so `ProxyHeadersMiddleware` rewrites `scope["client"]` from `X-Forwarded-For` — D26's basis is an environment variable | Fixed — `proxy_headers=False` passed explicitly, with an exit criterion |
| C2-2 | High | `_bind()` unspecified; copying `uvicorn.Config.bind_socket` sets `SO_REUSEADDR`, letting another local process hijack the loopback ACP surface on Windows | Fixed — `_bind()` written out: no `SO_REUSEADDR`, `SO_EXCLUSIVEADDRUSE` on win32, non-inheritable |
| C2-3 | High | The cookie is port-agnostic **outbound**, so any service on `0.0.0.0` harvests full `-a` access; `SameSite` covers only inbound | **Accepted in writing** — stated in Phase 3 and carried into the README security note |
| C2-4 | High | One timestamp served both the ceiling and the sweeper; an agent heartbeat made sessions permanently unsweepable | Fixed — split into `last_activity` and `last_used` with a table naming which reads which |
| C2-5 | High | The loop's baseline was the shared timestamp, so the first prompt on an idle session is cancelled before the agent can answer | Fixed — deadline is local to the request and seeded at send time |
| C2-6 | High | The absolute turn ceiling was deleted with nothing in its place; an `inflight` session is both un-closable and un-sweepable | Fixed — `PROMPT_ABSOLUTE_MAX_SECONDS` (4 h) checked in the same loop |
| C2-7 | High | The unguarded `last_activity` read turns a handled agent death into `monotonic() - None` → `TypeError`, replacing typed `AgentDied` with `internal_error` | Fixed — `.get()`-then-bail applied to the read, falling through to `await fut` |
| C2-8 | Med-High | `_is_remote_peer` undefined; "peer != bind address" and "peer in allowlist" are both wrong | Fixed — defined in the plan as inverted `is_loopback`, unparseable/absent ⇒ remote |
| C2-9 | Med-High | Validation placed in `load_config()`, whose contract is never-raise; a typo would 500 ~16 routes | Fixed — validate on the write path; sanitise to `""` at load and log ERROR |
| C2-10 | Med-High | Only the remote bind was wrapped, so a busy port kills startup — and the symmetric fix admits a remote-only listener with no loopback | Fixed — loopback mandatory with its port-in-use fallback; only remote may degrade |
| C2-11 | Med-High | One `_refuse` for two scope types; `http.response.start` into a websocket scope is an ASGI protocol violation | Fixed — scope-typed refusal, `websocket.close` code 1008 |
| C2-12 | Medium | The HMAC carries no issuance time, so "long-lived" is eternal with no revocation | Fixed — signs `(device_id, issued_at)`, rejected beyond a configured age, `Max-Age` 90 days |
| C2-13 | Medium | String rejection of `0.0.0.0`/`::` is bypassed by `::0`, `0000::`, `::ffff:0.0.0.0`; loopback not rejected | Fixed — reject on parsed properties (`is_unspecified`/`is_multicast`/`is_loopback`) |
| C2-14 | Medium | Middleware order unspecified and counter-intuitive — last-registered is outermost | Fixed — registration site pinned after `web.py:690`, with an exit criterion |
| C2-15 | Medium | `Sec-Fetch-Site` on "every remote GET" breaks the `/ws/acp` upgrade, which browsers do not send it for | Fixed — scoped to `http` GETs; noted the rule constrains browsers only |
| C2-16 | Medium | Fail-closed enforced at request time, so the remote socket is bound while auth is impossible; no minimum length named | Fixed — secret checked **before** binding; 43 characters named |
| C2-17 | Medium | `tick` and the sweep interval defined nowhere | Fixed — five named module-level constants with values |
| C2-18 | Medium | "First `if not sessions: continue`" reads as continuing before the sleep, hanging the event loop | Fixed — sleep-first ordering stated explicitly |
| C2-19 | Medium | Snapshot staleness: `sid in sessions` was not a sweep condition, so a user close mid-loop causes `AgentRejected` every pass | Fixed — membership re-check added as a sixth condition |
| C2-20 | Medium | Shutdown ordering put the sweeper await in the outer `finally`, where a non-`CancelledError` skips teardown | Fixed — cancel both, one `gather(...)` inside the nested block before `acp.shutdown()` |
| C2-21 | Medium | Post-cancel release re-opens the interleaving hazard `inflight` exists to prevent | Fixed — bounded `CANCEL_GRACE_SECONDS` re-await before releasing |
| C2-22 | Medium | The mechanism lives where ~30 test doubles stub, so no test reaches it; three timing criteria would burn 600-900 s | Fixed — constants made rebindable; a direct `_request` test named |
| C2-23 | Low-Med | `startswith("/static")` also admits `/staticfoo`; exact/prefix asymmetry undocumented | Fixed — exact for the four fixed paths, `== "/static" or startswith("/static/")` for the mount |
| C2-24 | Low-Med | `device_id` provenance unspecified — `;`/`,`/CR-LF permit cookie-attribute, header and log injection | Fixed — bounded charset and length, set via `response.set_cookie` |
| C2-25 | Low | Sentinel identity/type unspecified while the slot is annotated `float` and formatted `{timeout:.0f}` | Fixed — module-level `_INACTIVITY = object()`, branched on `is` before the `try` |
| C2-26 | Low | uvicorn suppresses its "running on" banner when `sockets=` is passed, losing the only record of live addresses | Fixed — explicit `log.info` of both `getsockname()` values |
| C2-27 | Low | The Change-1 snippet discarded the thread + `ready_event` scaffolding, so `run()` blocks and tray/peek never start | Fixed — thread kept, `sockets` passed via `kwargs` |
| C2-28 | Low | The `/static` websocket bypass was rated Critical/ACE; `StaticFiles` asserts `http`, so it is log spam and minor DoS | Fixed — risk row severity corrected; the ACE path is `/ws/acp` |
| C2-29 | Low | Rebind site `__main__:292` runs before `from .web import app` (`:294`), so `acp` is not imported yet | Fixed — rebind placed after the web import |
| C2-30 | Low | "`.lock` removed" asserts agent behaviour no Phase 0 item verifies | Fixed — added to Phase 0's verification list |
| C2-31 | Low | Backoff has no cap, no lockout, no table eviction, and does not cover cookie-verification failures | Fixed — N-failures/T-seconds lockout, bounded LRU, cookie failures logged |
| C2-32 | Low | `_handle_load`/`_handle_subscribe` have no `closing` guard, which the sweeper makes reachable without a user close | Recorded — pre-existing; a `close_in_progress` refusal noted for `_handle_load` |
| C2-33 | Low | "without relaxing the `not_subscribed` guard" is vacuous — sweep condition 2 guarantees zero subscribers | Fixed — rationale restated as covering the subscribe-during-close race |
| C2-34 | Low | The exchange page loads unstyled, since `/static/*` is allowlisted but not cookie-exempt | Recorded — cosmetic; will read as a bug, so the exchange page inlines its own minimal styling |

**Empirically verified during this cycle, not reasoned**: `asyncio.shield` semantics on the target interpreter (3.13.13) — five consecutive `wait_for(shield(fut), …)` timeouts left the inner future intact, `_callbacks` measured empty after each pass (no accumulation over a long turn), and `_request`'s `finally` still pops `_pending` on the raise path. The mechanism choice in D30 is sound; only the surrounding details were wrong.

### 2026-07-31 — Cycle 1 (via /qplan Step 4)

Five sub-agents: doc-impact scan, Architect (gap-critic lens), Security auditor, Reliability engineer, Senior engineer. Confidence: Architect 45%, Security 25%, Senior engineer 20-25% (on the 6-9 day estimate; realistic 9-13.5 days), Reliability 85% (own scope).

**42 findings after dedup (11 High, 20 Medium, 11 Low). All 42 applied in the 2026-07-31 revision** — every row below reading "Queued" was fixed in that pass; the six "Escalated" rows were resolved by user decision (1, 12) or planner call on an escalated finding (13, 14, 15, 16) and became D23-D25 and D31-D32. The table preserves the finding wording as reported so the trail from finding to fix stays auditable.

**Three reviewer claims were checked and corrected rather than applied**, per the trust-but-verify rule: (a) the Architect's recycled-pid objection to D10 is wrong — pid exclusivity means a dead writer held the pid before the live process existed, so the `-5 s` lower bound still rejects it, verified against `presence.py:85-92`; (b) the Reliability reviewer's "~30 of 40 `_request` stubs" is **19** fixed-signature stubs (7 `boom`, 5 `fake_request`, 5 `refused`, 2 multi-line) plus 4 tolerant `lambda *a, **k` patches — the guidance stands, the count did not; (c) the Senior engineer's "~153 tests re-decided" is **77 distinct test functions**, with ~153 being the collected count inflated by one 15×4 parametrisation.

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
- A pre-flight phase's exit criteria are checkboxes, which have only two states, but a spike's whole purpose is that a premise may be **refuted** — Phase 0's "the policy admits the user's own devices only" was disproven by measurement, which is neither ticked nor deferred, and `/qdev` Step 8's gate ("unchecked exit criteria remain … without an explicit deferral entry") reads a refutation as unfinished work — cost: the strongest result the phase produced had to be encoded in prose beside a permanently-unticked box, and the auto-continue gate had to be reasoned around rather than applied — suggested change: give spike/pre-flight phases a third exit-criterion state (`- [~]` refuted, or a `**Refuted**:` prefix convention) that satisfies the completeness gate while recording that the premise failed.
- `/qdev` Step 8 makes `tests: fail` an unconditional stop, which is right for a phase that changed code but mis-fires on a **pre-flight baseline phase**, whose job is precisely to discover a pre-existing red suite — cost: the stop fired on a failure the phase could not have caused and that no phase in the plan is scoped to fix, and distinguishing "you broke it" from "it was already broken" fell to prose — suggested change: let the baseline-capture case report `tests: fail` as a recorded starting state without tripping the auto-continue gate, provided the phase produced no code diff.
