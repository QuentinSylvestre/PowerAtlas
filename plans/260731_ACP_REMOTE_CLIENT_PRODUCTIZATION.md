# ACP Remote Client Productization

> **Date**: 2026-07-31
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Promote the throwaway `/acp` prototype into a NetBird-reachable remote client that dispatches, drives and resumes kiro-cli sessions PowerAtlas creates, with a Zed-style session browser, an idle sweeper, and a security model that survives leaving loopback.

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

1. With the remote bind enabled, a phone on NetBird can load `/acp`, authenticate once, and drive a session — create, prompt, stream, cancel, close — with the laptop's dashboard untouched on loopback.
2. With the remote bind disabled (the default), behaviour is byte-identical to today: loopback only, no new listening surface.
3. A request arriving on the NetBird address for any path outside the remote allowlist is refused, **including WebSocket upgrades**, verified by a test that exercises the socket path separately from the HTTP path.
4. `/api/launchers`, `/api/settings` and `GET /` are unreachable from the NetBird address; reaching them requires loopback.
5. A device without the remote secret is refused on both HTTP and the WebSocket, even when it reaches the NetBird address.
6. The session browser lists workspaces with their sessions, defaulting to 10 groups expanded with 3 sessions each, both independently paginated, with a per-session availability indicator; sessions locked by a live foreign process are greyed and not loadable.
7. An ACP-owned session shows a live status dot in the dashboard for its whole lifetime, including more than 120 seconds after the agent process started and while idle between turns for more than 5 minutes.
8. A turn that streams for longer than the old 600 s ceiling completes rather than timing out; a turn that goes silent for the configured window fails and cancels agent-side.
9. A session idle beyond the TTL with no attached subscriber, no in-flight turn and no in-flight close is terminated and its `.lock` removed; a session with an attached tab or a running turn is never swept regardless of age.
10. `MAX_SESSIONS` is read from configuration rather than a module constant, defaults to 8, and is not read from disk on the event loop.
11. The full suite passes: `pytest` green, and `node tests/acp_page.test.mjs` green against the reworked page.
12. README's three deliberately-incomplete sites are completed, per `AGENTS.md:7`'s exemption ending on promotion.

### Scope boundaries & non-goals

**In scope**: `/acp` page rework including the session browser; a purpose-built read-only listing endpoint; the remote bind and its allowlist; the device secret and cookie; the presence skew and listing-cache fixes; `last_used` plus the idle sweeper; the inactivity ceiling replacing the wall-clock prompt timeout; `MAX_SESSIONS` config plumbing; README.

**Explicitly out of scope**:

- **The permission/policy engine.** Ships with `-a`, knowingly. Reopens if unattended scheduling without a human is wanted.
- **Attaching to sessions live in a terminal.** Structurally impossible over ACP — `kiro-cli chat` is itself an ACP wrapper and its grandchild holds the lock.
- **The Claude Code ACP half.** Never started; needs a separate npm bridge.
- **Redacting or encrypting `custom_launchers[].env`.** Knowingly declined (Q4) so values stay readable in the WebUI; the loopback split is the sole protection.
- **TLS.** WireGuard already encrypts the transport. Reopens only if a real LAN interface is bound alongside NetBird.
- **`tests/conftest.py` and the 18 known config-leaking tests.** Pre-existing hazard (`memory/MEMORY.md:95-97`), recorded not fixed.
- **The `session-tab-title.md` steering rework.** Deprioritised; session titles will render as raw first prompts.
- **v3 and classic session stores.** 23 + 3 sessions stay invisible; ACP is v2-only.

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

**Step 1.5 dispatched the code-tracing trio** — in-scope files were predominantly `.py` and `.html` source. All three returned; findings distilled below.

**The `web → acp` coupling is three call sites and no shared state.** A guarded import (`web.py:41-49`, failure degrades to `acp = None`), `acp.shutdown()` in `lifespan`'s nested finally (`web.py:526-530`), and `acp.serve_socket(ws)` on an already-accepted socket (`web.py:902-909`). `acp → web` is nil. `acp.py` imports exactly two package names — `config.CONFIG_DIR` (`:70`) and `launcher._SESSION_ID_RE` (`:71`) — declared as a module-docstring invariant (`acp.py:15-28`) with a prototype exit criterion grepping for it. This is why the presence fix must not couple the modules.

**The middleware cannot see WebSocket upgrades.** `BaseHTTPMiddleware.__call__` returns early on non-`http` scope (`web.py:752-755`), which is why `_ws_origin_ok` is documented as "the mandatory first line of *every* WebSocket route in this module". Any new gate — the remote allowlist, the cookie — must be applied in both places.

**Security is positional, not authenticated.** Zero `Cookie`/`Authorization`/`HTTPBasic`/`ssl_` references in `src/`. `_ALLOWED_HOSTS` is loopback-only (`web.py:558`), enforced at three points (`:712` all methods, `:855` inline on `/acp`, `:782` on the socket). uvicorn is pinned to `127.0.0.1` at **two** sites (`__main__.py:308` and `:328` — the second is the random-port fallback). `_ACP_TOKEN`'s own comment (`web.py:727-731`) states it "is not a boundary".

**`_ALLOWED_HOSTS` must be widened to an IP, never a hostname.** `web.py:553-557`: a single-label name "is **not** safe to allowlist merely because it has no public DNS record: whoever wins LLMNR, NBT-NS or mDNS on the local network answers for it."

**No config value reaches `acp.py` today.** `MAX_SESSIONS` (`acp.py:260`), `ACP_ARGS`, the token and every timeout are module constants. `load_config()` has **no cache** — a full TOML parse plus sanitisation on every call (`config.py:139-268`), called synchronously on the event loop from ~16 routes. `at_capacity()` runs on the loop, so it must not read config there.

**Templates: two-level, no build step, one CSP.** `base.html` is the only skeleton and already carries the viewport meta (`:5`). CSP with a per-response nonce applies to `/acp` **only** (`web.py:811-840`); the page has exactly one `<script>` and a strict no-`innerHTML` rule, both enforced by `tests/acp_page.test.mjs`. `style.css` has exactly one `@media` rule and it is `prefers-reduced-motion` — **zero width breakpoints anywhere**. `html, body { height: 100%; overflow: hidden }` (`style.css:2`) makes this an app shell, which suits a two-pane/drill-down design but needs care with mobile browser chrome.

**Test suite shape.** `tests/test_web.py` is 8592 lines, 496 definitions collecting to 684 tests; `TestAcp*` is 40 classes / 189 definitions / **250 collected**. They are **white-box with no seam**: 108 references to `_Supervisor`, 98 to `_supervisor`, 59 to `_registry`, 32 to `_Connection`, and the six `_handle_*` coroutines called directly. ~153 of the host/WebSocket security tests cover `web.py` helpers and survive any `acp.py` change — but are exactly what a widened allowlist breaks. No `conftest.py`, no `pytest.ini`, no ruff config, **no CI**. Verified green at exploration time: `pytest -k "Acp or Host or WsOrigin or SameOrigin"` → 404 passed; `node tests/acp_page.test.mjs` → 15/15.

**Store shape, measured 2026-07-31.** 5,941 `.json`; 4,734 sub-agent sessions correctly filtered by the four `parent_session_id` guards; **1,207 user-facing sessions across 62 workspaces**; median 2 per workspace, max 208, top six ≈80%; 24 workspaces hold exactly one. **841 sessions carry a `.lock`**, nearly all stale.

**Protocol facts, measured on kiro-cli 2.16.0.** Permission round trip works both ways but is unused under `-a`. `_kiro.dev/session/terminate` is the only close method (`session/close`, `session/terminate`, `_kiro.dev/session/close` all `-32601`), works, is per-session, frees ~3 procs / ~172 MB, removes the `.lock` and leaves the `.json` — so a terminated session stays resumable. `--agent` binds to every session on the process and silently accepts invalid names. Cost is ~306 + 151N MB with MCP kept.

### 5. Risks & mitigations

| # | Risk | Evidence | Mitigation |
|---|---|---|---|
| R1 | The remote allowlist applied only in middleware leaves `/ws/acp` — the route reaching `-a` — exempt | `web.py:752-755` | Enforce in both the middleware and the `/ws/acp` handler; SC-3 tests the socket path separately |
| R2 | The NetBird account has 17 peers; reachability is not authorization | `netbird status` on this machine | NetBird ACL restricted to the user's device group **plus** the independent cookie secret; policy verification is a gating step, not an assumption |
| R3 | `-a` behind a remote surface is arbitrary command execution as the user | `acp.py:249`; `plans/ROADMAP.md` | Accepted knowingly; cookie secret is the layer that survives an ACL misconfiguration |
| R4 | The random-port fallback silently reverts to loopback if only `__main__.py:308` is changed | `__main__.py:320-336` | Both sites read the same config value |
| R5 | `custom_launchers[].env` holds real credentials and is returned by three read paths | `web.py:2170`, `:1581`, `:803` | Loopback-only routing is the sole protection (Q4, knowing override) |
| R6 | ACP sessions read `closed` in the dashboard — two independent mechanisms | `presence.py:466-468`; `:142-182` + `:121-123`; **measured** | Drop the skew upper bound; stop caching the lock-dir listing |
| R7 | `_kiro.dev/session/terminate` is an undocumented private extension with no fallback | `acp.py:222-235` | Sweeper failure is non-fatal and logged; degrade to memory growth, never a crashed task |
| R8 | Widening `_ALLOWED_HOSTS` to a hostname re-opens LLMNR/mDNS hijack | `web.py:553-557` | Allowlist the NetBird **IP**, not the FQDN |
| R9 | 18 tests read the developer's real `config.toml` containing real credentials | `memory/MEMORY.md:95-97`; no `conftest.py`; 185 config call sites, 0 monkeypatched | Keep the secret **out** of `config.toml` so this work adds no credential to the blast radius |
| R10 | kiro-cli self-updates and has regressed a measured behaviour before | `plans/ROADMAP.md` | Re-verify terminate and the load path at implementation time |
| R11 | Every mutating route is a lost-update race (`load_config` → mutate → `save_config` rewrites whole file) | `config.py:271-290`; ~11 routes | Pre-existing; a second concurrent client makes it likelier — do not add remote-reachable write routes |
| R12 | Clock skew stamping a lock in the future is no longer caught once the upper bound is dropped | Derived from `presence.py:467` | Accepted; consequence is a stale lock reading as live |

**Sub-agent conflicts**: none on facts. The subsystem and directed agents differed on `web.py` line count and test counts; the directed agent's figures were re-derived at HEAD and are used here.

### 6. Resolved decisions

- Q1: Keep and harden `src/power_atlas/acp.py`, or rebuild it? — A: keep and harden — Decision: keep. The module a rebuild would discard is the one needing least change; the 250 white-box ACP tests have no seam to re-point; the "throwaway" verdict rested on a rationale (wrong question answered) that the product decision retired.
- Q2: What replaces the security model when the app leaves loopback? — A: NetBird for transport, but the account is shared with ~17 company peers and only the user's devices should reach the instance — Decision: NetBird provides confidentiality, reachability and a stable non-single-label address; it does **not** provide authorization, so a device-level layer is required. TLS declined — WireGuard already encrypts; reopens only if a real LAN interface is bound.
- Q3: Is the NetBird policy the only authorization layer, or is there an independent app-level secret? — A: ok with the long-lived cookie — Decision: both. NetBird ACL primary; a persisted secret exchanged once per device for a long-lived cookie as the layer that survives a drifted or misconfigured policy.
- Q4: env storage — redact, redact + encrypt at rest, or rely on the loopback split? — A: (c), "I want to be able to read in the webUI" — Decision: no redaction, no at-rest encryption; the loopback routing rule is the sole protection. **Override recorded**: recommended (a) redact-from-read-paths; chosen (c). Rationale: masked values would prevent reading own tokens from the dashboard. Measured input: PyCharm stores run-config env vars in plain text (`.idea/workspace.xml` on this machine), so "PyCharm's level" was already the status quo.
- Q5: What exactly can the phone reach? — A: (b), plus fold in a rework of `/acp` to browse and resume existing sessions, Zed-style — Decision: default-deny allowlist — `/acp`, `/ws/acp`, `/static/*`, and one purpose-built read-only listing endpoint. `/acp` is reworked into a two-pane browser + conversation. `kiro-ui` (Apache 2.0) is a design reference only; its Express/React/Vite/Electron stack is incompatible with PowerAtlas's zero-build-step Jinja + htmx frontend.
- Q6: What does the left rail show, and how is "already open" resolved? — A: grouped by workspace, 10 groups default with show-more, groups expanded showing 3 sessions with per-group paging, indicator on all sessions, unavailable greyed — Decision: as specified. Three states, not two: **available**, **held by PowerAtlas** (in `_supervisor.sessions`, free to compute, a switch-to affordance), **locked elsewhere** (live foreign pid, greyed). Resolution is lazy per visible row (~30 rows, not 1,207), runs off-loop, and **fails open** — unknown resolves to available, because a wrongly-greyed session is unreachable while a wrongly-available one gets the typed in-use refusal.
- Q7: Where does the presence fix go? — A: ok — Decision: fix `presence.py`'s own heuristic, no coupling in either direction, preserving `acp.py`'s import invariant. (1) Drop the upper bound at `presence.py:467`, keep the `-5 s` lower bound, which alone rejects recycled pids. (2) Stop caching the lock-directory listing, whose write-once premise `session/load` falsifies. Change (1) also closes the 300 s recency-gate divergence for free.
- Q8: Sweeper TTL, ownership, and close path? — A: 30 min, but the TTL must only count when the agent is not working, so long tasks run unattended — Decision: 30 min configurable; `acp.py` owns the task, started/stopped from `web.py`'s `lifespan` (coupling 3 → 4 call sites); the sweeper calls `_supervisor.close_session` directly and broadcasts `session_closed` itself rather than relaxing `_handle_close`'s `not_subscribed` guard; sweeps only when idle beyond TTL **and** no subscriber **and** not `inflight` **and** not `closing`; failure is non-fatal and logged.
- Q8a (follow-on): the 10-minute wall-clock prompt ceiling blocks long unattended tasks and compounds into false-idle sweeping — Decision: replace `PROMPT_TIMEOUT_SECONDS`'s wall-clock bound with an **inactivity** ceiling of **15 minutes of silence**, reset by each `session/update`; stamp `last_used` from the same notification stream (alongside the existing `_note_context` write at `acp.py:1969`) so a working session is never idle by construction; send `session/cancel` on timeout so the agent stops rather than working orphaned.
- Q9: Where does the remote-access secret live? — A: ok with the recommendation — Decision: its own file, `CONFIG_DIR/remote-secret`, generated on first enable, never user-edited — keeping it out of `config.toml`'s sanitise-and-rewrite path and out of the blast radius of the 18 config-leaking tests. Remote bind defaults **off**. No new dependency for delivery: URL and secret shown as copyable text; QR deferred.

### 7. Open items

- **Verify the NetBird access policy restricts `100.78.142.124` to the user's own devices before the first remote-reachable bind.** Execution-contingent and external — the policy lives in the NetBird management console, unreadable from the repo, and it is the primary authorization layer. Gating step, not an assumption.
- **Re-verify `_kiro.dev/session/terminate` on the kiro-cli version present at implementation time.** Deterministic; the binary self-updates and has regressed a measured behaviour before.
- **Decide the remote listing endpoint's pagination contract** against the measured skew (62 workspaces, max 208 sessions). Deterministic — resolvable by reading `data.get_all_sessions_paginated` (`data.py:347`), noting that all four existing filters set `has_more = False`.
- **Confirm mobile soft-keyboard behaviour for the Enter-to-send handler** (`acp.html:707-714`). Execution-contingent; not measurable without a device.
- **`plans/ROADMAP.md:62` is factually stale** — it still describes `same_origin_guard` as POST-only. Deterministic, one-line correction.

### 8. Recommended approach

Sequence by dependency, not by visibility. The security work is the prerequisite for anything reachable, and the presence fix is independent enough to land early and de-risk the dashboard.

1. **Presence fixes** (`presence.py`) — smallest, independently verifiable, fixes a measured live defect. No dependency on anything else.
2. **Session lifecycle in `acp.py`** — `last_used` on notifications, the inactivity ceiling replacing the wall-clock one, `session/cancel` on timeout, the sweeper, `MAX_SESSIONS` from config read off the loop. Includes the two by-design test edits.
3. **Remote access** — bind config (both `__main__.py` sites), `_ALLOWED_HOSTS` widened to the NetBird IP, the default-deny allowlist in **both** the middleware and the `/ws/acp` handler, the `CONFIG_DIR/remote-secret` file, and the cookie exchange. Re-decide the ~153 host/WS tests here. Gated on the NetBird policy verification.
4. **The listing endpoint** — purpose-built, read-only, no env, no actions, on the remote allowlist.
5. **The `/acp` rework** — two-pane browser plus conversation; responsive drill-down on narrow viewports; update `tests/acp_page.test.mjs` alongside, since it is the only thing pinning the page's XSS and turn-state behaviour and it is outside pytest and CI.
6. **README** — the three sites `AGENTS.md:7` makes required on promotion.

### 9. QA environment

- **Run**: `.venv-PowerAtlas\Scripts\python -m pytest` (no CI, no `pytest.ini`, no `conftest.py`). Template behaviour: `node tests/acp_page.test.mjs` — **manual, outside pytest**, per `AGENTS.md:9`.
- **Known-flaky**: `tests/test_data.py` has ~8 timing-sensitive tests (`memory/MEMORY.md:89-93`) — re-run standalone before attributing a failure.
- **Config hazard**: 18 tests read the real `%LOCALAPPDATA%\power-atlas\config.toml`, which holds real credentials, so some outcomes depend on machine state.
- **Live surfaces**: `http://127.0.0.1:<port>/` dashboard and `/acp`; NetBird address `100.78.142.124` / `ps-us-p-2505-142-124.netbird.cloud` once the remote bind exists. Real kiro-cli 2.16.0 is installed and drivable.
- **Probe harness**: the standalone ACP probes written during the spikes live in the session scratchpad (`acp_spike/`) and drive `kiro-cli acp` headlessly without the web app — reusable for re-verifying terminate, lock semantics and load behaviour. They create real sessions in the store; clean up by matching cwd.
- **Store caution**: every `session/new` writes a permanent `.json`/`.jsonl`/`.lock` into a store of 5,941 entries. Test dispatches should use a scratch cwd so they are identifiable and removable.

### Assumptions (unconfirmed)

Resolved by model assumption rather than a user answer, surfaced in chat and un-vetoed:

- **Migration & rollout** — no migration; the feature is additive and the remote bind defaults off, so the existing loopback workflow is untouched.
- **Testing strategy** — extend `tests/test_web.py` rather than adding files (`AGENTS.md:8`); update `tests/acp_page.test.mjs` for the reworked page.
- **Test churn is re-decision, not re-pointing** — the ~153 host/WS tests encode loopback deliberately (`_HOSTILE_HOSTS` includes single-label names; both client fixtures pin loopback base URLs), so widening the allowlist requires re-deciding each.
- **Two tests change by design** — `TestAcpSessionRecordHoldsNoDeadState` (exact key set) and `TestAcpSessionCapMessage` (literal `"254 mb"`, superseded by ~150 MB).
- **Docs** — README's three deliberately-incomplete sites are in scope; the `AGENTS.md:7` throwaway exemption ends on promotion.
- **Session titles render as raw first prompts** roughly 10 times in 11 (measured); accepted, since Zed's own example shows prompt-shaped titles.
- **v3 (23) and classic (3) sessions stay invisible** — ACP is v2-only, matching `data_kiro`'s existing glob.
- **No new runtime dependencies or bundled artifacts**; no cost impact beyond local compute.
- **UI design detail is delegated** and refinable later, per the user's explicit instruction.

## Harness Improvement Opportunities

- The mandatory Step 1.5 dispatch gate fired correctly, but the load-bearing unknown both sub-agents flagged (`started_at` semantics) was resolvable in ~90 seconds with a runtime probe the sub-agents could not run — cost: two agents each spent a paragraph hedging an answer a probe settled outright — suggested change: let the trio return a "decidable by probe" list the orchestrator runs before the interview, rather than folding those into `[unverified]` prose.
- `/qexplore` Step 3's filename spec says `{YYMMDD}_{NAME}.md` while this repo's archive convention is `{YYMMDD}-{HHMM}_{NAME}.md`, with the `-HHMM` added at `/qclose` time — cost: one commit-log check to confirm which applies at creation — suggested change: note in the skill that the time component may be archive-time only, so the slug stays stable across the lifecycle.
