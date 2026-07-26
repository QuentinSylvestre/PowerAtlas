# kiro-cli ACP Client Prototype

> **Date**: 2026-07-25
> **Status**: In Progress — phases 1-2 complete, ACP prototype (phases 3-6) not started  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Throwaway prototype of a WebSocket-backed kiro-cli ACP client on a new `/acp` page, validating transport, process supervision and the session model before a from-scratch rebuild
> **Estimated effort**: ~7-10 days (revised up after review — see §6)

---

## Intent

### Problem statement & desired outcomes

PowerAtlas is a read-only observer. It infers what agent sessions are doing by scanning the
process table (`presence.py`) and tailing JSONL transcripts (`status_classifier.py`). Every
item under **Automation & Workflows** in `plans/ROADMAP.md` — dispatch no-interactive tasks,
open a session with a specific prompt or skill, template prompts, scheduled tasks, chained
launches — needs one capability PowerAtlas does not have: **a way to send a prompt to an agent
without a terminal.** ACP is that capability.

This project builds a deliberately throwaway prototype to learn what an ACP client costs and
what it yields, before committing to a design. The deliverable is knowledge and a validated
architecture, not reusable code — the intent is to rebuild from scratch afterwards.

Two constraints, both established by prior spikes, define the reachable shape:

- **ACP can resume any session that has exited, including ones the user's terminal created**,
  but it can **never** attach to a session currently running in a terminal. Concurrent attach
  is hard-refused in 0.73–0.84 s (`-32603 … "Session is active in another process (PID n)"`),
  because `kiro-cli chat` is itself a wrapper around `kiro-cli acp` and the grandchild holds
  the lock. A "live window onto my running sessions" product is not available.
- **Attaching or creating mutates the user's real session store.** Verified during this
  exploration: `session/new` writes `<sid>.json`, `<sid>.jsonl` (0 bytes) and `<sid>.lock`
  into `~/.kiro/sessions/cli/` at creation time, before any prompt. Sessions created by the
  prototype are permanent artifacts among the 13,227 already there.

Desired outcome: a working chat surface at `/acp` that proves the transport, the supervised
subprocess, and the server-side session registry, so the automation items become follow-on
work against a known-good substrate rather than an unexplored one.

### Success criteria

1. From `/acp`, create a new kiro-cli session against a chosen directory and see response text
   arrive incrementally as it is generated.
2. Resume an exited session — including one created in a terminal — and see its prior history
   rendered before the new turn begins.
3. A session survives a page reload: reopening `/acp` reconnects and replays the conversation
   from the server-side event log, without creating a second session.
4. An in-flight turn can be cancelled (`session/cancel`), and the UI reflects the cancellation.
5. Tool calls render as discrete, identifiable items rather than as undifferentiated text.
6. Live context-window usage percentage is displayed for the active session.
7. Quitting PowerAtlas terminates the ACP process **and its MCP grandchildren** — no orphaned
   process tree survives. (Measured: one session is 1 parent + 5 descendants.)
8. The WebSocket handler rejects connections whose `Origin` does not match the bound address,
   and this is demonstrated rather than assumed.
9. Typing in the dashboard search box with a status filter active no longer returns 500.
10. Every code line-reference in `plans/ROADMAP.md` and `plans/CLOSED_INVESTIGATIONS.md` either
    resolves to the construct it claims to cite, or is rewritten to a stable anchor that does
    not depend on a line number.

### Scope boundaries & non-goals

**In scope**

- A new `/acp` page route and a WebSocket route, both in `web.py`.
- A new ACP module: NDJSON codec, JSON-RPC correlation, one supervised `kiro-cli acp` process,
  a session registry with a bounded per-session event log, and tree-kill teardown.
- `websockets` added to `pyproject.toml` (uvicorn's preferred implementation; currently
  `AutoWebSocketsProtocol is None`, so WebSocket support does not exist in this environment).
- Entry from the existing dashboard: a session-row action opening `/acp`, plus a "new session"
  affordance on the page itself.
- The one-argument fix at `web.py:989`.
- Correcting the stale code line-references in `plans/ROADMAP.md` and
  `plans/CLOSED_INVESTIGATIONS.md` (8 stale sites enumerated in §1).

**Explicit non-goals**

- **Tests — for the ACP prototype only.** Phases 3-6 ship untested; they are throwaway.
  **Phase 2 is explicitly excluded from the waiver**: it fixes shipped code that outlives the
  prototype, and `AGENTS.md:8` already permits a regression test for a bug fix. It gets one. The
  waiver therefore never needs to stretch to cover it, and the proposed durable amendment below
  stays scoped to throwaway work exactly as drafted.
- **Linux.** Windows only for this prototype.
- **Claude Code.** Its ACP path requires the `@zed-industries/claude-code-acp` npm bridge — a
  separate bootstrap — despite the roadmap item naming both providers.
- **Permission prompts.** Sessions are created with `-a` (trust-all-tools), matching the
  existing `provider_settings.kiro-cli` default. Reopens when driven unattended or when this
  stops being a prototype.
- **`presence.py` changes and dashboard status integration.** ACP sessions will read as closed
  in the main dashboard after the ACP process's first two minutes (see Risks). `/acp` shows
  their true state; that is sufficient here.
- **README updates and new config keys.** Agent, model and effort are hardcoded.
- **A session browser on `/acp`.** Entry is from the dashboard, which already lists sessions.
- **The automation features themselves** — scheduled, templated and chained launches are
  what this unblocks, not what it delivers.

### Bug fix scope — `web.py:989`

Included as an independent phase so it lands separately from the ACP work.

`_workspace_status(snapshot, cwd, providers)` is declared with three parameters at
`web.py:244-245`; line 989 passes four, having retained a `g["latest_updated"]` argument that
commit `51fc500` removed from the other two call sites (`web.py:722`, `:754`). Reachable from
the UI by typing in search while a status filter is active, which 500s; `index.html:333` does
not check `r.ok`, so the error page body is injected into `#workspace-cards`.

**Invariants** — must hold after the fix:

- `/search` with `q` set and `status` in `("", "all")` continues to behave exactly as today.
- `/search` with `q` and a real status filter returns filtered workspace cards, not a 500.
- The status filter's meaning stays identical to `partials_workspaces` — same
  `_status_matches` / `_workspace_status` pairing, no divergent filtering logic.
- No change to `_workspace_status`'s signature or behaviour; this is a call-site fix only.

### Proposed durable governance amendment

Requested by the user during exploration; contradicts `AGENTS.md:8` as written, so recorded
here for an accept/drop decision at `/qclose` rather than applied silently.

> Add to `## Doc & Test Guidelines`:
> - Work explicitly scoped as a throwaway prototype (to be rebuilt rather than extended) is
>   exempt from the test and README requirements above. Record the exemption in the project
>   file. The exemption does not carry over to the rebuild.

---

## 1) Current State

### PowerAtlas is an observer with no outbound channel

- **No streaming transport of any kind.** Zero occurrences of `StreamingResponse`, `EventSource`,
  `WebSocket` or `text/event-stream` across `src/` and `tests/`. All updates are client polls on a
  single 5 s scheduler (`templates/index.html:342-386`). Verified 2026-07-25:
  `uvicorn.protocols.websockets.auto.AutoWebSocketsProtocol is None` and neither `websockets` nor
  `wsproto` is installed, so uvicorn currently answers any upgrade with
  `"Unsupported upgrade request."` (`uvicorn/protocols/http/h11_impl.py:152-157`).
- **No code in this repo has ever owned a child process.** All eleven `subprocess.Popen` call sites
  discard the handle — `launcher.py:166`, `:192`, `:413`, `:432`, `:571`; `notifications.py:114`,
  `:131`; `tray.py:60`, `:72`; `web.py:483`; `__main__.py:145`. A repo-wide grep for `.wait()`, `.poll()`,
  `.communicate()`, `.stdin`, `.stdout` over `src/power_atlas/*.py` returns zero hits — no pipe is
  ever created. There is no tree-kill helper.
- **Shutdown skips all finalizers.** `__main__.py:320` calls `os._exit(0)`; the four daemon threads
  die with the process. Nothing would reap an ACP subprocess.
- **The event loop performs synchronous disk I/O during renders.** `_session_status` and
  `_workspace_status` are called directly inside async route bodies (`web.py:722`, `:754`, `:850`,
  `:1474`, and the `/api/session-status` loop at `:583-614`), not via `asyncio.to_thread`. They do
  full workspace loads plus transcript tail reads up to 2 MB (`status_classifier.py:138`). This is
  why the ACP reader must not share the loop.
- **`same_origin_guard` cannot protect a WebSocket.** Verified from installed source:
  `starlette.middleware.base.BaseHTTPMiddleware.__call__` returns early on
  `scope["type"] != "http"`. The guard at `web.py:415-438` — including the `_ALLOWED_HOSTS`
  DNS-rebinding defense at `:412` — never runs for an upgrade request.
- **Unlocked caches are safe only by accident of caller placement.**
  `status_classifier._status_cache` (`:397`, LRU via `move_to_end`/`_evict_oldest` at `:437-456`)
  and `notifications._session_states` (`:31`, `move_to_end` at `:59`, `popitem` at `:64-65`) are
  plain `OrderedDict`s with no lock. Every current caller is on the event loop. The notifications
  path additionally has **no exception guard at its call site** (`web.py:232-236`), so a race there
  surfaces as a 500 rather than degrading quietly.

### ACP behaviour, measured on this machine 2026-07-25

Probed directly during exploration against kiro-cli 2.14.1 (`initialize` + `session/new`, one
throwaway session since removed):

- `initialize` returns in **0.94 s** with `authMethods: []`; `agentCapabilities` advertises
  `auth`, `loadSession`, `mcpCapabilities`, `promptCapabilities`, `sessionCapabilities`.
- `session/new` took **5.84 s** (the earlier spike measured ~3.2 s) and returned
  `{sessionId, models, modes}`.
- **It persists immediately, before any prompt**: `<sid>.json` (1,037 B), `<sid>.jsonl` (**0 B**)
  and `<sid>.lock` (59 B) all appear in `~/.kiro/sessions/cli/` at creation.
- The metadata carries **no `parent_session_id`**, so such sessions pass the sub-agent filters in
  `data_kiro.py` untouched and appear in the dashboard — with `"title": null` and an empty
  transcript. It also records `"session_created_reason": "subagent"` and a `permissions` block
  (`trusted_tools: []`, `allowed_commands: []`, `allowed_read_paths` scoped to the session cwd).
- Process tree measured exactly **1 parent + 5 descendants** per session, confirming the
  `~1 + 5N` model. `stderr` was **0 bytes** for the whole session.

### The `/search` arity bug

`_workspace_status(snapshot, cwd, providers)` is declared with three parameters at
`web.py:244-245`. `web.py:989` passes four, retaining a `g["latest_updated"]` argument that commit
`51fc500` removed from the other two call sites (`web.py:722`, `:754`). Reachable from the UI by
typing in search while a status filter is active — `index.html:333` does not check `r.ok`, so the
500's body is injected into `#workspace-cards` via `innerHTML`. No test covers `/search` with
`status=` set.

### Stale line-references in the roadmap docs

Enumerated and checked against current source 2026-07-25 (23 `file.ext:NNN` references parsed;
bare `:NNN` continuation forms such as ``presence.py:416`/`:211`/`:223`` are **not** captured by a
simple pattern and must be handled by hand). **8 stale sites:**

| Doc site | Cites | Actual location now |
|---|---|---|
| `ROADMAP.md:26` | `data_kiro.py:63,113` (sub-agent filter) | `data_kiro.py:110`, `:137`, `:187`, `:347` |
| `ROADMAP.md:34` | `status_classifier.py:55-63` (v3 read path) | `status_classifier.py:107-117` |
| `ROADMAP.md:43` | `web.py:176-181` (`None` → working fallback) | `web.py:224-229` |
| `ROADMAP.md:52` | `status_classifier.py:66-70` (JSONL name keying, `_get_project_folder` cwd-mangling) | `status_classifier.py:118-122` — the **claude-code** branch (`:119` `folder = _get_project_folder(cwd)`). Not `:102-117`, which is the kiro-cli branch |
| `ROADMAP.md:71` | `web.py:371-383` (`same_origin_guard`) | `web.py:415-438` |
| `ROADMAP.md:94` | `status_classifier.py:55-63` (v3 read path) | `status_classifier.py:107-117` |
| `CLOSED_INVESTIGATIONS.md:26` | `data_kiro.py:63,113` | as `ROADMAP.md:26` |
| `CLOSED_INVESTIGATIONS.md:27` | `data_kiro.py:76-89` (sqlite cwd merge) | `data_kiro.py:150-163` |

Verified **accurate**, leave untouched: `presence.py:65`, `data_kiro.py:16-25`, `config.py:53`,
`data_kiro_ide.py:24` (both sites), `presence.py:416`, `presence.py:481`, `test_data.py:1533-1674`,
`test_web.py:3774-4003`, `session_row.html:1`, `index.html:166`.

Two judgement calls for the implementer: `CLOSED_INVESTIGATIONS.md:90` cites `web.py:176-197` for
the cwd+recency gate, and `:176` is the comment that opens that region — arguably still correct,
narrower range would be `:186-200`. And `ROADMAP.md:80` cites `acp-agent.js:777`, a file in the
external Zed bridge package, not this repo — **leave it alone**.

## 2) Goal

Ship a throwaway `/acp` page backed by a supervised `kiro-cli acp` subprocess and a WebSocket
stream, proving the transport, process supervision and server-side session model well enough that
the roadmap's Automation & Workflows items can be planned against a known substrate — and fix two
unrelated defects found while exploring.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| What the window is for | Control substrate; chat UI as its proof | Chat client as the product; owned-sessions-only driver | The automation items all need one missing capability — prompting without a terminal. Building the substrate makes them follow-ons rather than fresh spikes. (Q1) |
| Window vs page | A page at `/acp`, not a second native window | Second pywebview window | A second window leaves `BrowserView.instances` non-empty, so `winforms.py:397-398` never reaches `Application.Exit()`, `webview.start()` never returns, and `__main__.py:304-320` never runs — hanging quit with the mutex held. A page has none of that. (Q1) |
| Process ownership | One supervised process, lazily started, holding N sessions; explicit session close | One process per session; session tied to the page | Verified: one process holds concurrent sessions without cross-talk. Memory cost is per *session* (~306 MB MCP), not per process, so session close is the lever that matters. (Q2) |
| Transport | WebSocket (`/ws/acp`), new `websockets` dependency | SSE (no new dependency); fast polling | User choice. Bidirectional, so prompts and future permission responses share one channel. (Q3) |
| Reader placement | Dedicated OS thread, bridged with `loop.call_soon_threadsafe` | asyncio task on the uvicorn loop | The loop already blocks for hundreds of ms on synchronous disk I/O during renders (§1); an asyncio reader would stall mid-stream. Matches the repo's existing off-loop discipline. (Q3) |
| WS origin protection | Re-implemented inside the handler | Inherit middleware | Middleware is structurally unreachable for `scope["type"] == "websocket"`. With `-a` and a fixed `port = 4915`, this check is the only control between a visited page and an ungated agent. (Q3/Q4) |
| Tool permissions | `-a` (trust-all-tools), prototype-scoped | No trust flags + permission prompts; `--trust-tools` allowlist | User choice for the prototype. **Corrected after review** — the original rationale ("matches the existing `provider_settings.kiro-cli` default") was wrong as a security argument. `CLOSED_INVESTIGATIONS.md:72` records that `kiro-cli chat` already spawns `kiro-cli acp --trust-all-tools`, and that "the permission gate in normal kiro-cli use lives in the TUI layer, not the agent". `/acp` **replaces that TUI**, so `-a` here removes the only gate that exists rather than matching a default. Accepted knowingly for a prototype driven by a human watching it; must be re-decided before the rebuild. (Q4) |
| Client→agent request handling | Catch-all responder + per-request timeout | Assume `-a` suppresses all agent→client requests | The probe only ran `initialize` + `session/new`, never a tool-using prompt, so nothing rules out `session/request_permission`, `fs/read_text_file` or `terminal/*` arriving. An unanswered request hangs the turn indistinguishably from ordinary latency. Fail fast instead. |
| Agent-output rendering | `textContent` / `_escHtml`, never `innerHTML` | Follow the repo's prevailing `innerHTML` idiom | Agent output is attacker-influenced (repo files, fetched pages, commit messages). With `-a`, an XSS in the app's own origin can drive `/ws/acp` itself. The repo idiom (`index.html:200`, `:311`, `:422`) points the wrong way here, so the exception is stated explicitly. |
| WS message envelope | `{type, sessionId, payload}`, opaque router in `web.py` | Leave the protocol implicit | Three reviewers independently flagged the missing wire contract as the likeliest source of two incompatible implementations. Specifying it in Phase 3a also lets Phases 4-6 add message types without touching `web.py`, which is what their file scopes assume. |
| Session entry point | Dashboard row action + "new session" button on `/acp` | Session picker on `/acp`; both | Strictly less code — no second session-listing UI, no pagination over 13,227 entries — and reuses the existing session-id plumbing. (Q5) |
| Status for owned sessions | Existing pipeline, no special handling | ACP registry as authoritative source; hide them from the dashboard | Zero integration work for a prototype. Accepts known wrongness right after creation, when the transcript is empty. (Q6) |
| `presence.py` skew collision | Accept — no change to `presence.py` | Exempt PowerAtlas's own ACP pid from the skew check | Originally chosen, then **superseded** once the work was scoped throwaway: editing a shipped, tested, load-bearing module so a disposable prototype renders better in a panel that is not its point is poor value. `/acp` shows true state. (Q7, superseded by Q9) |
| Reconnect behaviour | Bounded per-session in-memory event log, replayed on connect | Rebuild from `<sid>.jsonl`; live events only | The only option that makes server-side session lifetime pay off. The transcript is lossier than the wire format and is being concurrently written. (Q8) |
| Tests | None | New `tests/test_acp.py`; fold into `test_web.py`; split by layer | User decision — prototype to be rebuilt. Waives `AGENTS.md:8`; durable amendment proposed in Intent. (Q9) |
| Footprint outside the ACP module | Minimal — no config keys, agent/model/effort hardcoded | Add config keys for agent/model/effort | Throwaway. Config is cheap to add later if tuning turns out to need edit-restart cycles. (Q10) |
| ACP teardown | Windows Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) as the guarantee; `lifespan` tree-kill as a fast path | `lifespan` alone; pid-file + startup reaper; narrow SC 7 to the tray path | `lifespan` teardown was *measured* working on the tray route (t+0.13 s idle, t+0.21 s with a live socket) but is structurally unreachable from `--stop`/`--restart`, which `TerminateProcess` at `__main__.py:90` — and `memory/MEMORY.md:94-98` says those are the paths dev iteration uses most. The job also covers crashes and Task Manager. `pywin32` is already a Windows dependency; `win32job` verified available. With tests waived, an OS guarantee beats code that has to run. |
| WebSocket authentication | Per-process token (`secrets.token_urlsafe(32)`) plus the origin check | Origin check alone | Origin stops a browser; it does nothing against a local process, which under `-a` means arbitrary command execution. The "already unauthenticated" counter-argument does not hold — existing surfaces open a folder or run a user-saved launcher, not agent-driven arbitrary commands. Residual risk (token readable via `GET /acp`) recorded rather than hidden. |
| Test scope | ACP phases untested; **Phase 2 tested** | Waive tests for everything in the plan | The waiver is scoped to throwaway work by its own wording. Phase 2 fixes shipped code that survives the prototype, and `AGENTS.md:8` explicitly permits a regression test for a bug fix — so the exemption never needs to stretch, and the amendment stays narrow. |
| ACP process cwd | Neutral directory (not a workspace) | The session's workspace | One process serves N workspaces, so its cwd cannot be meaningful. A neutral cwd stops it lighting up a real workspace in `presence`'s process scan. Session cwd is recovered from `<sid>.json` regardless (`presence.py:471-476`). |
| Provider scope | kiro-cli only | kiro-cli + Claude Code | Claude Code's ACP path needs the `@zed-industries/claude-code-acp` npm bridge — a separate bootstrap. Surfaced as an assumption at the exploration checkpoint; un-vetoed. |
| Platform scope | Windows only | Windows + Linux | User decision at the exploration checkpoint. `kiro-cli acp` on Linux is unverified. |
| `session/cancel` | In scope — stop button | Defer | Verified working (`stopReason: "cancelled"`); cheap; a chat client without an interrupt is unpleasant. Surfaced as an assumption; un-vetoed. |

## 4) External Dependencies & Costs

### Required external changes

| Category | Change needed | Owner | Status |
|---|---|---|---|
| Third-party services | Add **`websockets<17`** to `pyproject.toml` `dependencies`; reinstall the venv (`pip install -e .`). | Implementer | Pending |

**Pin it, and know why.** uvicorn 0.49's `auto` selector unconditionally imports `websockets_impl`,
which depends on `websockets.legacy` — still shipping in 16.1.1 (verified end-to-end by a reviewer)
but emitting two `DeprecationWarning`s and on a removal clock. A future legacy-free release breaks
server startup, and it breaks *after* the `AutoWebSocketsProtocol is not None` pre-flight would have
reassured the implementer. Two options: pin the range (chosen — no `__main__.py` change), or pass
`ws="websockets-sansio"` to both `uvicorn.Config` calls at `__main__.py:246` and `:264`, which is
one line each but breaches the minimal-footprint decision. Pinning is reversible; take the explicit
selector if the deprecation warnings become noisy.

No CI/CD, IAM, cloud, DNS, secrets, or data-migration changes. There is no CI in this repo
(no `.github/`, no `Makefile`, no `tox.ini`). `pyproject.toml:34` already declares
`package-data = ["static/**", "templates/**"]`, so a new template needs no packaging change.

**Runtime prerequisite** (not a change): `kiro-cli` must be on `PATH`. Verified present at 2.14.1,
and `kiro-cli acp` requires no authentication (`authMethods: []`).

### Cost impact

None in money. Local memory only: **~1 + 5N processes and ~280 + 306N MB** for N concurrent
sessions — three sessions is roughly 1.2 GB. Session close is the only lever; the plan does not
add an idle-timeout sweeper, so sessions accumulate until closed or PowerAtlas exits.

## 5) Implementation Phases

### Phase 1: Correct stale line-references in the roadmap docs [P:2]

**Goal**: Every code line-reference in the two roadmap documents resolves to what it claims.

**File scope**: `plans/ROADMAP.md`, `plans/CLOSED_INVESTIGATIONS.md`

Apply the eight corrections tabulated in §1. Then sweep for the reference forms the enumeration
pattern could not catch — bare `:NNN` continuations after a first reference, e.g.
``pid is captured at `presence.py:416`/`:211`/`:223`, used only as a validation join key at
`:455`/`:462``` in `CLOSED_INVESTIGATIONS.md:89`. Each must be checked individually.

Where a citation names a construct that is stable but whose line will keep drifting, prefer
rewriting to a symbol anchor (e.g. ``the sub-agent filters in `data_kiro.py` (four sites, each
`if not data or data.get("parent_session_id")`)``) over pinning a fresh number that rots again.
This is the durable fix; a re-numbered citation buys one commit of accuracy.

Leave untouched: the ten references verified accurate in §1, and `ROADMAP.md:80`'s
`acp-agent.js:777`, which points into the external Zed bridge package.

**Why this is phase 1**: it is prose-only, shares no file with any other phase, and its evidence
(the §1 table) is freshest now.

**Sequencing hazard — `web.py` references must not be re-numbered here.** Phases 3-6 insert a
`GET /acp` route, a `@app.websocket("/ws/acp")` route, and teardown into `lifespan`, shifting every
subsequent line in `web.py`. Any `web.py:NNN` citation this phase writes as a fresh number is stale
again by Phase 3. (Phase 2 is exempt — it deletes an argument in place, changing no line count.)
So for the three `web.py` sites — `ROADMAP.md:43`, `ROADMAP.md:71`, `CLOSED_INVESTIGATIONS.md:90` —
**convert to symbol anchors rather than new line numbers**, e.g. ``the `None` → `"working"` fallback
at the end of `_session_status` (`web.py`)`` and ``` `same_origin_guard` in `web.py` ```. Phase 6
carries a re-verification criterion as a backstop.

The §1 table's "actual location" column is a starting point, not gospel — an independent scan
disagreed with it by a few lines in four rows (e.g. the fallback as `:226-229` vs `:224-229`, the
sqlite read as `:153` vs `:150-163`). Verify each against source at implementation time. This
disagreement is itself an argument for symbol anchors.

**Exit criteria**:
- [x] All 8 stale sites in the §1 table corrected or converted to symbol anchors
- [x] The three `web.py` citations converted to **symbol anchors**, not re-numbered
- [x] Bare `:NNN` continuation forms in `CLOSED_INVESTIGATIONS.md:89` individually verified
- [x] Re-run the enumeration and confirm every remaining `file.ext:NNN` reference resolves to the
      construct its surrounding prose describes (spot-check by opening each cited line)
- [x] `ROADMAP.md:80` (`acp-agent.js:777`) left unchanged
- [x] `plans/260725_PARSE_AND_POLL_PERFORMANCE.md` left unchanged — it carries four stale refs of
      its own but is a historical record of a completed analysis, not a live index (out of scope)
- [x] Cold-read check: a reader following any corrected citation lands on code that matches the
      claim the sentence makes about it

#### Implementation (2026-07-25, code: c906b00)

Every code line-reference in `plans/ROADMAP.md` and `plans/CLOSED_INVESTIGATIONS.md` now resolves to
the construct its surrounding prose describes. Thirteen references changed across the two files: the
eight the plan tabulated, plus five the plan's enumeration could not have seen. Each was verified by
opening the cited lines in current source rather than trusting either the §1 table or the "verified
accurate" list, and the enumeration was re-run afterwards.

Source disagreed with the §1 table in several places. The table's own `ROADMAP.md` doc-site line
numbers were stale by +8, because commit `391808b` inserted a roadmap item after §1 was written, so
rows were matched by cited content rather than by line. The plan's suggested anchor text for the
`data_kiro.py` sub-agent filters quoted `if not data or data.get("parent_session_id")` as common to
all four sites; two match that form, one binds `d` rather than `data`, and the fourth is a bare
`if d.get("parent_session_id"):` with no null guard — so the guards are described rather than
quoted. `config.py:271-291` pointed one line past end of file. And `test_web.py:3774-4003`, which
§1 listed as "verified accurate, leave untouched", was being invalidated by Phase 2 as Phase 1 ran:
the same grep issued twice showed the offset growing from +22 to +35 lines, so it was converted to a
symbol anchor rather than renumbered into a moving file. Three further `web.py` citations added by
`391808b` got the same treatment, since they sit below the points Phases 3-6 will insert routes.

A `## Platform` roadmap entry was added proposing that `search` and `partials_workspaces` be
unified, on the strength of this plan's own Phase 2 defect. It went through three drafts and two of
them shipped false claims — a `/search` test count that Phase 2 invalidated within the hour, and
then a fabricated assertion that a provider-filtered search overstates its session count, which is
wrong because `search()` narrows `matched` by provider *before* `_group_workspaces` runs. The entry
was ultimately rewritten to assert only durable facts: a commit SHA, symbol names, the ruff
configuration, and an instruction to diff the two functions. No counts survive in it.

### Phase 2: Fix the `/search` status-filter crash [QA] [P:1]

**Goal**: `/search` with both `q` and a status filter returns cards instead of a 500.

**File scope**: `src/power_atlas/web.py`, `tests/test_web.py`, `plans/tests/260701_POWERATLAS.md`

Delete the surplus argument at `web.py:989`:

```python
# before
grouped = [g for g in grouped if _status_matches(
    status, _workspace_status(snap, g["cwd"], g["latest_updated"], prov_names))]

# after
grouped = [g for g in grouped if _status_matches(
    status, _workspace_status(snap, g["cwd"], prov_names))]
```

This matches the two correct call sites at `web.py:722` and `:754`. No signature change.

**This phase gets a regression test** — it is outside the prototype waiver, which covers throwaway
work only. `AGENTS.md:8` permits a new test for "a regression bug fix", and no new *file* is needed:
`tests/test_web.py:2721`'s `test_search_with_tag_filter` is the exact template — same shape, with
`tag=` swapped for `status=`. Five of the file's `/search` tests exist (`:113`, `:126`, `:2700`,
`:2721`, plus `:1706` for the sessions variant) and none passes `status=`, which is why the bug
reached production.

**Why the test-plan probe gap matters here**: `plans/tests/260701_POWERATLAS.md:243-248` is the
brief for this exact endpoint (§2.14 Search) and its probe list — *"query matching a folder name;
no match…; rapid typing (debounce)"* — **never combines search with a status filter**. That
omission is why the bug shipped undetected. Closing it is the highest-value single edit in the
test corpus and costs one line.

**Exit criteria**:
- [x] `GET /search?q=<term>&status=working` returns 200 with filtered cards
- [x] `GET /search?q=<term>` (no status) behaves exactly as before
- [x] Manual: type in the search box with each of Working / Waiting / Errored selected; no error
      text appears in `#workspace-cards`
- [x] Regression test added to `tests/test_web.py` asserting `GET /search?q=…&status=working`
      returns 200 — modelled on `test_search_with_tag_filter` (`:2721`), no new file
- [x] The new test fails against the pre-fix code and passes after (confirm by stashing the fix)
- [x] Full suite still green — 611 tests currently pass
      <!-- Met in substance, not as worded: the "611 passing" baseline was already false when the
           plan was written (2 tests fail on clean main). See §9. -->
- [x] The four Intent invariants for this fix all hold
      <!-- Invariants 1 and 4 carry user-approved deviations, recorded in §9. -->
- [x] `plans/tests/260701_POWERATLAS.md` §2.14 probe list extended with a search + status-filter
      combination

#### Implementation (2026-07-25, code: 2341d68)

The reported defect is fixed by deleting the surplus `g["latest_updated"]` argument from `search()`'s
`_workspace_status` call, matching the three other call sites. That is the whole of what the plan
scoped. Five further changes were made on explicit user decision during review, each recorded as a
divergence below.

`api_session_status` now derives the workspace dot by calling `_workspace_status` rather than
re-implementing priority aggregation over `_session_status` results, so the server-rendered dot and
the 5-second-polled dot can no longer disagree — they had been computed by two independent code
paths that agreed only by coincidence of similar defaults. That unification initially dropped
claude-code's provider-reported `waiting` signal at workspace level, because `_workspace_status` never
read `snapshot.reported_status`; teaching it to do so restored the signal at both surfaces, and is
the deviation from invariant 4. The mapping is now shared with `_session_status` through
`_map_reported_status`, and priority folding through `_raise_status`, which between them deleted three
hand-written copies of the same "highest priority wins" logic — the duplication class that produced
this bug in the first place. The two functions deliberately continue to weigh a report differently:
`_session_status` answers what one session is doing, where a first-hand report beats a lagging
transcript tail, while `_workspace_status` answers what matters most in a workspace, where
max-priority-wins avoids hiding a signal. That divergence is now explained in the shared docstring
rather than merely noted.

The remaining changes: `/search`'s empty state names the filter that emptied it instead of claiming
the query matched nothing; `r.ok` guards and error handling on both search fetches, so a 5xx no
longer injects markup into the panels; dead `_age_seconds` removed with its test and its sole
`datetime.timezone` import; and a `threading.Lock` on `status_classifier._status_cache`, which was
the only unlocked cache of three and became reachable from a worker thread once the poll moved to
`asyncio.to_thread`.

Verification: 622 tests pass (2 failures pre-date this work on clean `main`). Runtime QA confirmed
the fix and both guards live — see the review log entry below.

**Scope boundary — status dots in `/search` results.** `/search` never passes `workspace_status` to
the card template (`web.py:1002-1009`, `:1027-1032`), while `partials_workspaces` does (`:730`,
`:762`); `workspace_card.html:10` is the only consumer. So after this fix, filtering search results
by "Working" returns the right cards **with no status dots** — visually odd but passing every
criterion above. Treat rendering as **out of scope** for this phase: the invariant "the status
filter's meaning stays identical to `partials_workspaces`" governs *filtering*, not *rendering*.
Recorded so the next reader sees a deliberate boundary rather than an oversight.

### Phase 3: ACP transport — spawn, handshake, create a session, tear down [QA]

**Goal**: From `/acp`, create a kiro-cli session and see its id; quitting PowerAtlas leaves no
orphaned process.

**File scope**: `src/power_atlas/acp.py` (new), `src/power_atlas/templates/acp.html` (new),
`src/power_atlas/web.py`, `src/power_atlas/static/style.css`, `pyproject.toml`

**Pre-flight before anything else** — `memory/MEMORY.md:88-92` records this exact failure mode one
release earlier: *"psutil must be installed in the runtime venv, not just listed in
pyproject.toml… After adding a new dependency, verify it's actually installed with
`pip show <pkg>`."* Adding `websockets` to `pyproject.toml` changes nothing until `pip install -e .`
re-runs, and the failure is **silent** — uvicorn falls back to `AutoWebSocketsProtocol = None` and
answers upgrades with `"Unsupported upgrade request."` rather than erroring at startup.

**The `ws://` URL must be derived, never hardcoded.** `config.py:53` defaults `port` to 0
(OS-assigned); this machine happens to run a fixed 4915, but the page must build its socket URL
from `location.host`. `ROADMAP.md:71` makes the same point for a different consumer.

**Why horizontal — it is not.** This is the first vertical slice: subprocess through protocol
through registry through WebSocket through page, ending in something observable in a browser.

**Split into 3a and 3b if the phase stalls.** Review flagged that this bundles four independent
failure domains — dependency install, WS handshake + origin, subprocess + JSON-RPC, teardown — so a
teardown failure masks whether the transport works. Natural seam: **3a** = dependency + `/acp` page
+ WS route + origin check, observable as "page connects"; **3b** = supervisor + handshake +
`session/new` + teardown. Run as one phase if it goes smoothly; split at the seam on the first
sign of trouble rather than debugging four domains at once.

**Wire contract — decide this before writing any of it.** Three reviewers independently named the
missing browser↔server protocol as the likeliest source of two incompatible implementations, and
it blocks Phases 4-5. Fix it here:

- **Envelope**, both directions: `{"type": <str>, "sessionId": <str|null>, "payload": <object>}`.
- **Client→server types**: `subscribe` (attach this socket to a session and replay its buffer),
  `new` (create a session against a cwd), `prompt`, `cancel`, `close`.
- **Server→client types**: `session` (id + metadata after `new`/`subscribe`), `chunk`, `tool_call`,
  `tool_update`, `meta` (context-window %), `error`, `agent_died`, `history_truncated`.
- **Session identity across a reload**: the page carries `?sid=…` in the URL and re-sends
  `subscribe` on connect. This is what makes SC 3 ("reload reconnects without creating a second
  session") achievable — without it the reconnect has no way to name what to replay.
- **`web.py` stays an opaque router.** The WS handler validates origin, then forwards frames to
  `acp.py` without interpreting `type`. This is what lets Phases 4-6 add message types while
  keeping `web.py` out of their file scope, as those phases assume.

**Correctness details that are silent failures if missed:**

- **Every write is `write(line + "\n")` then `flush()`**, serialized through one lock or one writer
  task. An unflushed request is simply invisible to the agent and presents as a hang with no error.
- **`stderr=subprocess.DEVNULL`** (or a second drain thread). The plan originally opened a `stderr`
  pipe that nothing read — once the ~64 KB Windows pipe buffer fills, the child blocks on write
  forever. The "stderr was 0 bytes" evidence in §1 is a single observation and does not cover
  panic backtraces or MCP startup noise.
- **Loop capture**: `self._loop = asyncio.get_running_loop()` inside the async spawn path, never at
  module import — `uvicorn/server.py:75` creates the loop inside `server.run()` on a non-main
  thread (`__main__.py:251`), so import-time capture gets the wrong loop or none.
- **Reader thread** is `daemon=True`, its body wrapped in `try/except Exception` with a `finally`
  that marks the supervisor dead and notifies sockets. `call_soon_threadsafe` raises `RuntimeError`
  on a closed loop, and `__main__.py:256-270`'s port-fallback path can create a second loop.
- **Pending-request table**: every future carries a wall-clock timeout, and reader-loop EOF rejects
  all pending futures with a typed error plus an `agent_died` frame to every socket. The plan says
  health comes from the JSON-RPC channel; this is what that means concretely.
- **Catch-all for agent→client requests.** Reply to any unrecognized inbound *request* with a
  JSON-RPC error and log the method at WARNING. `session/request_permission`, `fs/read_text_file`
  and `terminal/*` are all plausible; the probe never sent a tool-using prompt, so nothing rules
  them out. Declare `clientCapabilities` explicitly in `initialize` rather than leaving it empty.
- **Log every `tool_call` / `tool_call_update` from here**, not from Phase 6. Execution capability
  arrives in this phase; without logging, three phases run commands with no record anywhere.

New module `acp.py` provides:

- **Supervisor** — lazily spawns `kiro-cli acp -a` with `stdin`/`stdout` pipes and
  **`stderr=subprocess.DEVNULL`** (never an undrained pipe — see the deadlock note above), text mode,
  `encoding="utf-8"`, `errors="replace"` (matching the repo's decode discipline at
  `status_classifier.py:173`), and a **neutral cwd**. Health is judged from the JSON-RPC channel,
  never from exit code or stderr — `_kiro.dev/commands/execute` was observed killing the agent with
  exit 0 and no stderr, and the probe saw 0 stderr bytes across a whole session.
- **NDJSON codec + JSON-RPC correlation** — one JSON object per line; a monotonic id counter; a
  `dict[int, asyncio.Future]` of pending requests; notifications routed by `method`.
- **Reader thread** — blocking `for line in proc.stdout`, bridging to the loop:

  ```python
  def _reader_loop(self) -> None:
      for line in self._proc.stdout:          # blocking, on its own thread
          line = line.strip()
          if not line:
              continue
          try:
              msg = json.loads(line)
          except ValueError:
              continue                         # tolerate non-JSON banner lines
          self._loop.call_soon_threadsafe(self._dispatch, msg)
  ```

- **Windows Job Object — the primary teardown guarantee.** Assign the child to a job with
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` at spawn. Windows then destroys the whole tree whenever the
  last job handle closes — which happens on *any* PowerAtlas death: tray quit, the `TerminateProcess`
  used by `--stop`/`--restart` (`__main__.py:90`), an unhandled crash, or Task Manager. `pywin32` is
  already a Windows dependency and `win32job` was verified available with both
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and `AssignProcessToJobObject`.

  ```python
  def _spawn(self) -> None:
      self._job = win32job.CreateJobObject(None, "")       # unnamed, not inheritable
      info = win32job.QueryInformationJobObject(
          self._job, win32job.JobObjectExtendedLimitInformation)
      info["BasicLimitInformation"]["LimitFlags"] |= (
          win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
      win32job.SetInformationJobObject(
          self._job, win32job.JobObjectExtendedLimitInformation, info)

      self._proc = subprocess.Popen([...], ...)
      handle = win32api.OpenProcess(
          win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, self._proc.pid)
      win32job.AssignProcessToJobObject(self._job, handle)
      # self._job must stay referenced for the process lifetime — if it is
      # garbage-collected the handle closes and the OS kills the agent.
  ```

  This demotes the explicit `shutdown()` below from *the* correctness mechanism to a fast path:
  it makes teardown prompt on the tray route, while the job makes it *certain* on every route.
  Given tests are waived, preferring an OS guarantee over code that has to run is the right trade.

- **Tree-kill teardown** (fast path) — `psutil` is already a dependency and already used at `presence.py:403`:

  ```python
  def shutdown(self) -> None:
      proc = self._proc
      self._proc = None
      if proc is None or proc.poll() is not None:
          return                      # already exited — the pid may be recycled,
                                      # and killing a recycled tree is the exact
                                      # hazard presence.py:462-464 defends against
      try:
          parent = psutil.Process(proc.pid)
          kids = parent.children(recursive=True)   # MCP grandchildren
          parent.kill()                            # parent first, so it cannot
          for child in kids:                       # spawn more mid-teardown
              try: child.kill()
              except psutil.Error: pass
          psutil.wait_procs([parent, *kids], timeout=3)
      except psutil.Error:
          pass
  ```

In `web.py`: a `GET /acp` route rendering `acp.html` (extending `base.html`, which already pulls
in `style.css` and `htmx.min.js`); a `@app.websocket("/ws/acp")` route whose **first action** is
the origin check, reusing the existing `_ALLOWED_HOSTS` frozenset at `web.py:412`:

The WS route requires **two** checks, in order: a per-process token, then the origin.

```python
# Per-process, never persisted, regenerated every launch.
_ACP_TOKEN = secrets.token_urlsafe(32)


def _ws_origin_ok(ws: WebSocket) -> bool:
    """Mandatory first line of every WebSocket route.

    Middleware cannot do this: BaseHTTPMiddleware returns early on
    scope["type"] != "http" (starlette/middleware/base.py), so
    same_origin_guard never sees an upgrade request.

    Both halves are derived from ws.url. Reading the expected origin from
    ws.url.netloc but the allowlist from ws.url.hostname is safe; mixing in
    the raw Host header is NOT — Starlette's URL falls back to
    scope["server"] when Host fails its _HOST_RE (underscores, for one),
    so a rebound host would satisfy the loopback allowlist against
    127.0.0.1 while matching its own attacker-chosen Origin.
    """
    if (ws.url.hostname or "").lower() not in _ALLOWED_HOSTS:
        return False
    expected = f"{'https' if ws.url.scheme == 'wss' else 'http'}://{ws.url.netloc}"
    return ws.headers.get("origin", "") == expected


@app.websocket("/ws/acp")
async def ws_acp(ws: WebSocket) -> None:
    if not secrets.compare_digest(ws.query_params.get("t", ""), _ACP_TOKEN):
        await ws.close(code=1008)   # uvicorn converts a pre-accept close
        return                      # into an HTTP 403 handshake rejection
    if not _ws_origin_ok(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
```

`_ws_origin_ok` is extracted rather than inlined so the rule exists in one place — it now lives in
two (`web.py:415-438` for HTTP, this for WS), and a future `@app.websocket` route could otherwise
ship without it.

**Why a token on top of the origin check.** Origin stops a web page; it does nothing against a local
non-browser process, which can send any header it likes. Under `-a` that means arbitrary command
execution in whatever directory a session was created against. The counter-argument — that
unauthenticated local access already exists here — is true but not equivalent: `web.py:467-489`
opens a folder in a file explorer, and `custom_launchers` runs commands the user saved themselves.
Neither is agent-driven arbitrary execution. The token is `secrets.token_urlsafe(32)`, generated per
launch, rendered into `acp.html`, compared with `compare_digest`.

**Residual risk, stated rather than implied**: the token is delivered inside a page served over
unauthenticated HTTP, so any local process that can fetch `GET /acp` can read it. This raises the
bar from "connect blindly" to "scrape one page first"; it is not a boundary. Closing it properly
means authenticating the page route too, which is out of scope here — recorded so the rebuild
inherits the problem statement rather than the illusion of a fix.

And ACP teardown appended to the existing `lifespan` cleanup after the `yield` (`web.py:381-389`).

**Split executed — 3a complete, 3b pending.** The seam the plan documents was taken proactively
rather than on first trouble: the two installed interpreters run uvicorn versions two minor series
apart (see §9), which makes the transport layer a genuine variable, so 3a was ended at the
observable "page connects" checkpoint before any subprocess existed to confound it. Criteria below
are annotated **[3a]** or **[3b]**.

**Exit criteria**:
- [x] **[3a]** `pip install -e .` re-run, `pip show websockets` confirms installation, and
      `AutoWebSocketsProtocol` is no longer `None`
- [x] **[3a]** `web.py`'s module docstring (`"""FastAPI web application with htmx-powered UI."""`) updated —
      the module now also serves a WebSocket surface, which is neither htmx nor request/response
- [x] **[3a]** The page's WebSocket URL is built from `location.host`, with no hardcoded port
- [x] **[3a]** `GET /acp` renders; the page opens a WebSocket and reports connected
- [x] **[3b]** Clicking "new session" creates one; the returned `sessionId` is displayed
- [x] **[3b]** The new session's `<sid>.json`/`.jsonl`/`.lock` appear in `~/.kiro/sessions/cli/`
- [x] **[3a]** The handshake is **rejected with HTTP 403** for each of five cases: absent or wrong token,
      mismatched `Origin`, absent `Origin`, `Origin: null`, and a mismatched `Host` with a
      matching `Origin`.
      **Not** a 1008 close frame — a pre-`accept()` close is converted by uvicorn into a 403
      handshake rejection and the code is discarded (`websockets_impl.py:278-285`; confirmed by two
      reviewers running it, one observing `InvalidStatus: HTTP 403` with `.code is None`). An
      implementer chasing "1008" would loosen the check to make the criterion pass
- [x] **[3a]** Probe written with `additional_headers=` (renamed from `extra_headers` in websockets 14).
      **Superseded in practice**: the `websockets` client and `httpx` both derive `Host` from the URI
      and silently drop a `Host` passed this way, so every `Host` probe was hand-written raw HTTP.
      Two agents hit the resulting false pass before this was understood — see §9
- [x] **[3b]** Quitting PowerAtlas **from the tray** leaves no surviving `kiro-cli` process from this
      session — `Get-Process kiro-cli` before and after (expect parent + 5 gone).
      **Verified by the user 2026-07-26: 1 → 0.** This is also the only route that exercises the
      `lifespan` teardown this phase added to shipped `web.py`, which had never run before this test
- [x] **[3b]** `power-atlas --restart` and `power-atlas --stop` **also** leave none — covered by the Job
      Object, since these hard-kill via `TerminateProcess` (`__main__.py:86-91`, reached from
      `:341`/`:346`) and never run `lifespan`. `memory/MEMORY.md:94-98` records that dev iteration
      restarts PowerAtlas constantly, making this the most-used path during Phases 3-6.
      **Verified**: `--restart` with 12 pids captured by create-time → 0 survivors; `--stop` with 7
      pids → 0. Both were the Job Object working alone
- [x] **[3b]** Killing PowerAtlas from Task Manager also leaves none — the case only the Job Object covers,
      and the reason it was chosen over a pid-file reaper.
      **Verified 2026-07-26** via `Stop-Process -Force`, which issues the same `TerminateProcess`
      Task Manager's End Task does. The full 7-process tree was captured by pid beforehand
      (1 `kiro-cli`, 2 `cmd`, 2 `conhost`, 2 `node`); after the kill, **zero of the seven survived**
      and the port was released. `lifespan` cannot run on this path, so the entire teardown was the
      Job Object alone — which is the whole reason it was chosen over a pid-file reaper
- [x] **[3b]** The job handle is held for the process lifetime (not garbage-collected), verified by keeping
      a session open across a several-minute idle period and confirming the agent survives.
      **Verified by the user 2026-07-26: 1 → 1 across a five-minute idle.** This is what makes the
      other two teardown results meaningful — without it, a tree dying could be the handle being
      collected rather than teardown working
- [x] **[3b]** Spawning uses a neutral cwd, named explicitly as `CONFIG_DIR / "acp-cwd"` (created on demand)
      — **not** `Path.home()`, which is plausibly a real workspace and would be picked up by
      `presence.py`'s process scan, defeating the purpose
- [x] **[3b]** The ACP process's **own cwd** contributes no `live_cwds` entry. (Distinct from: a *created
      session* may light its real workspace for up to `_SIDECAR_SKEW_S = 120 s` via the independent
      sidecar path at `presence.py:471-476` — that is accepted per Q7, not a failure. The original
      single criterion conflated the two and was unachievable as written.)
      **Verified**: the snapshot shows exactly the predicted two-entry split and no more
- [x] **[3b]** `kiro-cli` resolved via `shutil.which()` and asserted **not** to be a `.cmd`/`.bat` wrapper
      before spawning with pipes — `memory/MEMORY.md:40-44` records that `.cmd` shims need
      `shell=True`, which is incompatible with holding clean stdio. (Verified clear on this machine:
      `where kiro-cli` → `kiro-cli.exe`. The assertion guards other machines and future installs.)
      **Note**: `shutil.which()` returns `kiro-cli.EXE` — uppercase — so the suffix check must
      case-fold or it silently never fires. It does
- [x] **[3b]** Spawned with `creationflags=CREATE_NO_WINDOW` so no console flashes per session
- [x] **[3a]** `grep -n "status_classifier\|notifications" src/power_atlas/acp.py` returns nothing — the
      isolation boundary §6 relies on as a mitigation, enforced by inspection since tests are waived
- [x] **[3b]** Create a session, quit PowerAtlas, restart, and confirm **the session still reopens**.
      Teardown is `kill()`-only by design, which orphans its `<sid>.lock`; this verifies that a
      tree-killed session is not permanently poisoned in the real 13,227-session store.
      **Verified**: `session/load` against a session whose whole tree was killed returned
      immediately — the orphaned `.lock`, which holds `{"pid":…,"started_at":…}`, is treated as
      stale rather than poisoning the session

#### Implementation — 3b (2026-07-26, code: fa71c64, fixes: 68269ca)

3b puts a real process behind the socket 3a defended. A `_Supervisor` lazily spawns one
`kiro-cli acp -a` on the first session request — never at import, never at startup — with piped
stdio, `stderr=subprocess.DEVNULL`, text mode with `errors="replace"`, `CREATE_NO_WINDOW`, and a
cwd of `CONFIG_DIR / "acp-cwd"`. Around it: an NDJSON codec, a monotonic request id, a pending-future
table where every entry carries a wall-clock ceiling, a daemon reader thread owning the blocking
stdout read and bridging to the loop through a `RuntimeError`-guarded `call_soon_threadsafe`, and a
`finally` on that thread that marks the supervisor dead, rejects every pending future and pushes
`agent_died` to every socket. Writes are serialised through one lock and always flushed.
`initialize` declares `clientCapabilities` explicitly; any inbound *request* from the agent gets a
JSON-RPC `-32601` refusal plus a WARNING naming the method; `tool_call` and `tool_call_update` are
logged from this phase, not Phase 6.

**Measured, and it corrects §1**: the tree is **1 parent + 6 descendants for the first session** and
**+5 for each after** — two `conhost`, two `cmd`, two `node` (the MCP servers, ~104-111 MB each).
§1's "1 + 5" is right as a marginal figure but one short in absolute terms; the extra is the parent's
own `conhost` from `CREATE_NO_WINDOW`. Timings: `initialize` 1.09 s, first session 6.83 s end to end,
subsequent sessions ~2 s on the warm process — faster than §1's 5.84 s once the process exists.

**Review found two High defects, both reproduced against mocks and both fixed in `68269ca`.** A
failed `initialize` left the process bound while `alive()` — being `poll()`-based — reported healthy,
so every later call short-circuited and skipped the handshake *permanently*; recovery needed a
PowerAtlas restart. That is precisely the failure the plan's own "health comes from the JSON-RPC
channel, never from exit code" decision exists to prevent, and `alive()` was an exit-code test.
Separately the session cap read `len(sessions)` then awaited twice before recording, so concurrent
`new` frames all passed: **8 concurrent calls against `MAX_SESSIONS=3` produced 8 sessions**, each a
permanent artifact in the real store, defeating the sole mitigation for §6's memory-exhaustion risk.
Both now demonstrated closed — 3 recorded and 5 refused under the same burst, and a failed handshake
now re-spawns.

#### Implementation — 3a (2026-07-25, code: f717b54)

3a delivers a defended WebSocket surface at `/ws/acp` and a page at `/acp` that connects to it, with
nothing behind it yet. `web.py` gained a per-process `_ACP_TOKEN`, an extracted `_ws_origin_ok`, a
`GET /acp` route and a `@app.websocket("/ws/acp")` route that runs the token comparison then the
origin check — both before `accept()` — and then hands the socket to `acp.serve_socket` without ever
reading a frame's `type`. That handoff is the seam: `acp.py` owns the envelope, the routing table,
the connection and subscriber registry, and a per-socket outbound queue drained by a single writer
task, so Phases 4-6 can add message types without `web.py` appearing in their file scope. All five
client types answer a typed `not_implemented` today.

**The phase's largest output was not the transport.** Reviewing it surfaced four Host-validation
defects in shipped code — three predating this plan entirely — each reproduced before being fixed
and each now carrying regression tests. They are enumerated in §9. The plan instructed reusing
`_ALLOWED_HOSTS`; reusing it faithfully is what exposed the first one.

Wire contract fixed as specified, with two deliberate deviations recorded in §9: the connect
acknowledgement overloads the `meta` server type rather than adding a ninth, keeping the type set
closed for Phases 4-6 — so `meta` is a general out-of-band channel, not only context-window
telemetry — and the message-size rejection is delivered as close code 1009 rather than a typed
`error` frame, because the close and the queued frame race and 1009 is the standard code for the
condition.

### Phase 4: Prompt and stream, with reconnect replay [QA]

**Goal**: Send a prompt and watch the response arrive incrementally; reload the page and get the
conversation back.

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/templates/acp.html`,
`src/power_atlas/static/style.css`

Add `session/prompt` dispatch (prompt text arrives from the page over the same socket), and
forward `session/update` notifications — `agent_message_chunk` in particular — to every socket
attached to that session. Add the bounded per-session ring buffer (`collections.deque` with
`maxlen`, a few thousand events) recorded as events are dispatched; on socket connect, replay the
buffer before subscribing to live events. Client-side, append chunks into the transcript pane.

**Agent output is never rendered with `innerHTML`.** This is a security rule, not a style
preference, and it runs against the repo's prevailing idiom (`index.html:200`, `:311`, `:422`) —
which is exactly why it is stated. Agent output carries attacker-influenced content: repository
files, fetched web pages, commit messages. A chunk containing `<img onerror=…>` would execute in
the app's own origin, and with `-a` in force it could then drive `/ws/acp` itself, passing the
origin check because it genuinely *is* same-origin. Use `textContent`, or the existing `_escHtml`
helper at `index.html:337` — noting that helper escapes `&`, `<` and `>` but **not** `"`, so it is
safe for text nodes and unsafe for unquoted attribute interpolation.

Note for the implementer: `templates/index.html`'s hand-rolled DOM work must call
`htmx.process(el)` after any `innerHTML` assignment, because `static/htmx.min.js` is a 56-line
custom implementation that binds handlers only at `DOMContentLoaded`. If `acp.html` builds markup
carrying `hx-*` attributes, the same rule applies; if it uses plain listeners it does not.

**Fan-out needs a per-socket writer and a replay cursor.** The exit criterion below tests two tabs
for "cross-talk or duplication", which is unachievable without both: give each socket its own
`asyncio.Queue` drained by a single writer task, and record the buffer position at replay time so
live events arriving during replay are not delivered twice.

**Exit criteria**:
- [x] A prompt sent from `/acp` produces text that appears progressively, not in one block
- [x] Reloading `/acp` reconnects and replays the prior conversation
- [x] Reload creates **no** second session — `~/.kiro/sessions/cli/` gains no new `<sid>.json`
- [x] Two browser tabs on `/acp` show the same session's stream without cross-talk or duplication
- [x] No agent-derived string reaches `innerHTML` — verified by inspection of `acp.html`
- [x] Ring buffer bounded by construction (`deque(maxlen=…)`), verified by code inspection rather
      than measurement; when it has dropped events, replay emits a `history_truncated` marker as its
      first item, so SC 3's "replays the conversation" degrading to a suffix is visible rather than
      silent
- [x] Incremental arrival still holds **with the dashboard open in another tab** — the send side
      runs on the same loop that blocks on 2 MB transcript reads every 5 s (`index.html` `_TICK_MS`),
      so smoothness measured on an idle server is not the real case

#### Implementation (2026-07-26, code: e8cb4df, fixes: 48f331a)

`prompt` dispatches to `session/prompt`; `agent_message_chunk` notifications forward to every socket
attached to the session; a per-session ring buffer records events and replays them on `subscribe`.
Turn boundaries are recorded events (`meta turn start` / `turn end`), so a reload mid-turn replays
into the correct pending state without a separate state field.

Three implementation decisions the plan did not anticipate:

- **Replay is one coalesced `history` frame, not N frames.** `SEND_QUEUE_MAXSIZE` is 256 and a full
  queue retires the socket, so replaying a 2000-event buffer frame-by-frame would have killed
  precisely the socket the replay exists to serve — and only for sessions with enough history to be
  worth replaying.
- **The replay cursor is structural.** `_handle_subscribe` is synchronous, so attach-and-replay with
  no `await` between them is atomic against the event loop and no live event can be delivered twice.
  Both reviewers independently confirmed the property holds; a guard test now asserts the function
  is not a coroutine function, because the invariant is otherwise only a comment.
- **The reader cap needed block reads.** `for line in stdout` and `readline()` both accumulate until
  a newline arrives, whatever it costs — neither can decide to stop. `read1` on the binary buffer
  bounds it, and also keeps streaming visible: `TextIOWrapper.read(n)` would have held each chunk
  hostage until 64 KiB accumulated.

**Tool-call rendering was pulled forward from Phase 6** on user decision, after review found tool
execution under `-a` was invisible on every channel. Tool calls now forward, record and render in
the transcript with their command, clipped at 4000 characters with the bound stated in the UI. Tool
*output* is deliberately not carried: it is unbounded by nature and every byte would evict the
conversation it annotates.

Suite: **769 → 826 passed, 1 skipped.** 57 tests added. The client-side fixes are covered by driving
the rendered template over a DOM shim in Node — 15 of 21 checks fail against `e8cb4df`, which is the
differential evidence the review findings were real.

### Phase 5: Resume an exited session [QA]

**Goal**: Open a session created earlier — including one from a terminal — and see its history.

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/web.py`,
`src/power_atlas/templates/acp.html`, `src/power_atlas/templates/partials/session_row.html`,
`src/power_atlas/templates/index.html`

Add `session/load`, rendering the replayed `session/update` events as history before live
streaming begins. Add a session-row action in the dashboard that opens `/acp` for that session id
— a distinct affordance, **not** a row click, because row click already means multi-select
(`session_row.html:1` → `handleItemClick`, `index.html:166`).

**Concretely: the new control must live inside `session_row.html`'s `<div class="session-actions">`
block** (currently holding pin / copy-sid / resume), because that container is what the row's
`onclick` excludes. Placing it anywhere else in the row makes clicking it toggle multi-select
instead. This is the same collision recorded at `CLOSED_INVESTIGATIONS.md:90` as one of the two
design collisions that killed the terminal-window-focus feature — it is a known trap here, not a
hypothetical.

**Validate the session id before it touches a path or the wire.** It arrives from the client and is
joined into `~/.kiro/sessions/cli/<sid>.lock`. Reuse the existing guard rather than inventing one:
`launcher.py:25`'s `_SESSION_ID_RE = ^[\w\-]+$` plus the 128-character cap applied at
`launcher.py:134`.

Before loading, read `~/.kiro/sessions/cli/<sid>.lock` and check pid liveness as a cheap
pre-flight. Treat this as a hint only — the authority is the typed refusal
(`-32603 … "Session is active in another process (PID n)"`), which arrives in 0.73-0.84 s and must
be surfaced as a clear message rather than a spinner. Do **not** reuse
`presence.Snapshot.is_live()` as the gate: it additionally requires provider-name match and a
start-time skew window (`presence.py:450-476`), so it can report not-live for a session ACP will
still refuse.

**Exit criteria**:
- [ ] A session created in Phase 4, then closed, reopens with its history rendered
- [ ] A session created in a **terminal** (`kiro-cli chat`), then exited, reopens with its history
- [ ] Attempting to open a session currently live in a terminal shows a clear "in use by PID n"
      message within ~1 s, and does not hang or retry
- [ ] The dashboard action opens `/acp` for the correct session and does not disturb multi-select

### Phase 6: Session close, cancel, tool calls, and context-window telemetry [QA]

**Goal**: The remaining ACP capabilities the prototype exists to evaluate — plus the session-close
capability the memory mitigation depends on.

**Session close is not optional.** §4 and §6 both accept the ~306 MB/session cost on the strength of
"explicit session close in the UI", and §3's process-ownership row calls it "the lever that
matters" — but no phase built it. Review caught this as a mitigation with no implementation. It
lands here: a close control per session that releases the ACP session, drops its ring buffer, and
is verified by measurement, not assertion.

**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/templates/acp.html`,
`src/power_atlas/static/style.css`, `plans/ROADMAP.md`, `plans/CLOSED_INVESTIGATIONS.md`,
`plans/tests/260701_POWERATLAS.md`

Add a stop button issuing `session/cancel` (expect `stopReason: "cancelled"`). Render
`tool_call` / `tool_call_update` as discrete items rather than inline text. Surface the
context-window usage percentage from `_kiro.dev/metadata`.

**Roadmap reconciliation — narrow, do not delete.** The `ACP client (kiro-cli + Claude Code)` entry
covers two providers and this prototype delivers one. Deleting it would lose live findings about the
undelivered half.

> **Address these by quoted anchor text, not line number.** Phase 1 rewrites both of these files, so
> every line number below would already be stale by the time this phase runs — the exact
> self-invalidation the plan caught for `web.py` and initially missed here. The numbers in
> parentheses are provenance from the 2026-07-25 review, not addresses. Apply edits bottom-up within
> each file as a second safeguard.

- **Preserve**: `:80` (the Claude Code ACP gate is open, static analysis only) and `:81` (the two
  Claude Code SDK-path gaps) — entirely about the half not built.
- **Preserve the clause, retire the lead-in** at `:90`: "worth running for the state/control
  learning" becomes past tense, but "committing the product to it is a separate decision" is
  precisely the framing this prototype preserves.
- **Re-measure** `:91`'s effort estimate against actuals. The roadmap orders items by value per day
  (`:41`), so a stale estimate mis-ranks its neighbours.
- **Extend, don't correct**, `:88` and `CLOSED_INVESTIGATIONS.md:102`: both say attaching mutates
  the session, which remains true but is now too narrow — `session/new` writes `.json`, `.jsonl`
  and `.lock` **at creation, before any prompt**.
- **Unblock** `:27` (live sub-agent pipeline state over ACP), whose only stated blocker was "requires
  the ACP client… so it rides that decision".
- **Qualify** `:104` (Misc — "Visualize/interact with opened sessions"). The word *opened* is
  load-bearing: live terminal sessions stay unreachable, so the item survives — but "that would make
  this reachable without ACP" now misleads, since an ACP path exists for *exited* sessions.

**Exit criteria**:
- [ ] A long-running turn can be cancelled; the UI shows the cancellation and the session stays
      usable for a subsequent prompt
- [ ] Tool calls render as distinct items with their command and status visible
- [ ] Context-window percentage displays and changes across turns
- [ ] `ROADMAP.md:75-91` narrowed per the six points above, with `:80`/`:81` preserved intact
- [ ] `ROADMAP.md:27` and `:104` updated; `:88` and `CLOSED_INVESTIGATIONS.md:102` extended
- [ ] A session can be **closed** from the UI; `Get-Process kiro-cli | Measure-Object` and the
      supervisor's RSS both drop by roughly one session's worth (expect ~5 processes, ~306 MB).
      This is the measurement that makes §4 and §6's accepted memory cost real rather than assumed
- [ ] Closing a session drops its ring buffer; the process keeps running and other sessions are
      unaffected
- [ ] **Backstop**: re-run the Phase 1 reference enumeration now that `web.py` has shifted, and
      confirm no citation regressed
- [ ] Open items recorded: whether a graceful session close removes the `.lock`; whether
      `session/load` alone mutates the transcript; whether `session/new` latency is reliably ~5.8 s

### Review escalations — all resolved 2026-07-25

The four decisions the review cycle raised, and how they landed. No open items remain.

1. **Phase 2 regression test — YES, one test in `tests/test_web.py`.** The prototype waiver applies
   to the ACP work only; Phase 2 fixes shipped code that outlives it. No new file, no amendment
   needed — `AGENTS.md:8` already permits a regression test for a bug fix, and
   `test_search_with_tag_filter` (`:2721`) is the template.
2. **SC 7 — Windows Job Object.** Teardown-in-`lifespan` was *proven* on the tray path (a reviewer
   reproduced `__main__.py:246-320` and measured t+0.13 s idle, t+0.21 s with a live WebSocket) but
   cannot cover `--stop`/`--restart`, which `TerminateProcess` outright. The job with
   `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` covers every death route including crashes and Task Manager,
   uses the already-present `pywin32`, and demotes the explicit teardown to a fast path. Chosen over
   a pid-file reaper because an OS guarantee beats code that has to run — especially with tests
   waived.
3. **WebSocket authentication — per-process token, added.** Origin stops a web page but not a local
   process, and under `-a` that gap is arbitrary command execution. Ten lines. Residual risk (the
   token is readable by anything that can fetch `GET /acp`) is recorded in Phase 3 rather than
   papered over.
4. **Effort — accept 7-10 days; SC 5 and SC 6 kept.** Cutting Phase 6's tool-call rendering and
   context-window telemetry would have meant dropping two success criteria, not trimming schedule.
   Those two are also the features most specific to ACP — cutting them would save the least
   informative days relative to the prototype's stated purpose.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Orphaned ACP process tree on exit — `os._exit(0)` at `__main__.py:320` skips finalizers, and nothing here has ever reaped a child | 1 + 5N processes, ~280 + 306N MB leaked per run | Tree-kill in `lifespan` cleanup (Phase 3), with an explicit before/after process-count exit criterion |
| Lifespan shutdown may not complete inside `__main__.py:309`'s 5 s join | Orphans survive despite the teardown code | Keep teardown to `kill()` calls only — no graceful protocol shutdown, no waiting on the agent |
| WebSocket bypasses every CSRF and DNS-rebinding defense; compounded by `-a` and fixed `port = 4915` | A visited web page could drive an agent with tool access in the user's directories | Origin check as the handler's first action (Phase 3), with a negative test as an exit criterion |
| Reader thread reaching into unlocked caches | `status_classifier._status_cache` / `notifications._session_states` corrupt or raise; the notifications path 500s (`web.py:232-236` has no guard) | Hard boundary: `acp.py` imports neither module. Q6(b) and Q10(a) keep this true — revisit if ACP status is ever integrated |
| Sessions accumulate at ~306 MB each | Three concurrent sessions ≈ 1.2 GB | Explicit session close in the UI; no idle sweeper in this prototype — accepted and recorded |
| N-session concurrency assumed from an N=2 measurement | If sessions interfere at higher N, the one-process model fails | Contained: the registry owns spawn/teardown, so falling back to one-process-per-session is a localized change |
| kiro-cli self-updates mid-development (2.14.0→2.14.1 observed previously) | Wire format or flags shift under a running implementation | Accepted for a prototype; pin observations to 2.14.1 in notes |
| ACP sessions read as closed in the dashboard after the process's first 2 minutes (`presence.py:462-464` skew window) | Dashboard is uninformative about ACP sessions | Accepted (Q7). `/acp` shows true state |
| Prototype sessions are permanent artifacts in a 13,227-entry store | Clutter; untitled entries with empty transcripts | Create against scratch directories; accepted |
| `session/new` at 5.84 s vs the spike's ~3.2 s | User-visible latency; possibly drift | Show a pending state during creation; record the measurement in Phase 6 |
| Re-numbered citations rot again (Phase 1) | The same fix needed in three months | Prefer symbol anchors over line numbers where the construct is stable |
| **This plan invalidates its own Phase 1 output** — Phases 3-6 insert routes into `web.py`, shifting every line below them | Three `web.py` citations corrected in Phase 1 are stale again by Phase 3 | Symbol anchors mandated for `web.py` refs in Phase 1; re-enumeration criterion in Phase 6 as backstop |
| Test-plan manifests silently under-describe the surface (`plans/tests/260701_POWERATLAS.md:150` enumerates 2.1–2.25 as the complete web surface) | A future `/qtest` run reports full coverage while omitting `/acp` and `/ws/acp` | §2 manifest, §2.16 (lifespan scope), §2.24 (session actions) updated in Phase 6 |
| **`--stop` / `--restart` bypass `lifespan` entirely** — `TerminateProcess` at `__main__.py:86-91` — and dev iteration uses them constantly (`memory/MEMORY.md:94-98`) | Full 1+5N tree orphaned per restart cycle, repeatedly, during the phases that spawn most | **Resolved** — Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` kills the tree on every death route, including crashes and Task Manager. Exit criteria cover tray, `--stop`, `--restart`, and Task Manager |
| Job handle garbage-collected while a session is live | The OS closes the last handle and kills the agent mid-turn | Supervisor holds the reference for its lifetime; exit criterion verifies survival across a multi-minute idle |
| XSS via streamed agent output rendered with `innerHTML` | Script executes in the app's own origin and can drive `/ws/acp`, which with `-a` is full tool access | Explicit no-`innerHTML`-for-agent-output rule and exit criterion in Phase 4 |
| Unanswered agent→client JSON-RPC request (`session/request_permission`, `fs/*`, `terminal/*`) | Turn hangs forever, indistinguishable from the ~5.8 s latency already expected | Catch-all error responder + per-request timeout in Phase 3; `clientCapabilities` declared explicitly |
| Undrained `stderr` pipe deadlocks the child at ~64 KB | Agent hangs with no error surfaced anywhere | `stderr=subprocess.DEVNULL` in Phase 3 |
| Unflushed `stdin` write is invisible to the agent | Presents as a hang with no error | Explicit flush-and-serialize rule in Phase 3 |
| `/ws/acp` has no cap on connections, sessions, or message size | One accepted socket can drive the machine to memory exhaustion at ~306 MB/session | Cap sessions per supervisor and set a max WS message size in Phase 3 |
| Origin alone does not stop a local non-browser process | With `-a`, full tool access to any local process | **Resolved** — per-process token required on the WS handshake alongside the origin check. Residual: the token is readable by anything that can fetch `GET /acp`, which raises the bar without being a boundary; recorded in Phase 3 |
| Credentials in cleartext reachable by the agent | `%LOCALAPPDATA%\power-atlas\config.toml` holds `AUTH_TOKEN_PRODUCTION`, `AUTH_TOKEN_STAGING`, `MQTT_CERT_PATH` under `[custom_launchers.env]`; one `fs/read` away from an `-a` session | Out of scope for this plan, but flagged: those tokens should move regardless of whether this ships |
| Effort re-estimated 3-5 → 7-10 days | `ROADMAP.md`'s own ~1 week covers only Phases 3 and 5, leaving nothing for 4, 6, or the two fixes | **Resolved** — 7-10 days accepted; SC 5 and SC 6 kept rather than cut, since dropping them would remove success criteria (not schedule) and would cut precisely the ACP-specific features the prototype exists to evaluate |

## 7) Verification

No automated tests (Q9). Verification is manual and per-phase, via the exit criteria above.

**Setup**: `pip install -e .` in `.venv-PowerAtlas`, then `power-atlas -f` (foreground, so the log
is attached). UI at `http://127.0.0.1:4915/acp` — the configured fixed port, not the random
default. Logs at `%LOCALAPPDATA%\power-atlas\orchestrator.log` (file handler only; nothing goes to
stdout). Create sessions against a scratch directory, since each is permanent.

**Cross-phase checks** to run once at the end:

```powershell
# no orphaned agent processes after quitting PowerAtlas
Get-Process kiro-cli -ErrorAction SilentlyContinue | Measure-Object

# the existing suite still passes (Phase 2 touches shipped code)
.venv-PowerAtlas\Scripts\python.exe -m pytest -q
```

The existing suite must stay green — Phase 2 modifies `web.py`, and 611 tests currently pass.

## 8) Documentation Updates

Derived by a doc-impact sub-agent over every tracked `*.md` outside `plans/done/`, plus
`pyproject.toml`, `_check_backend.py`, and all 15 `src/power_atlas/*.py` module docstrings.
`_proto/**` returned zero hits for every term. There is no `docs/` directory.

| Document | Update needed | Phase |
|---|---|---|
| `plans/ROADMAP.md` | Correct 6 stale line-references (§1 table); `web.py` refs as symbol anchors | 1 |
| `plans/CLOSED_INVESTIGATIONS.md` | Correct 2 stale line-references (§1 table); `:90`'s `web.py` ref as a symbol anchor | 1 |
| `plans/tests/260701_POWERATLAS.md` | §2.14 Search — add a search + status-filter probe; its absence is why the Phase 2 bug shipped | 2 |
| `src/power_atlas/web.py` | Module docstring — no longer htmx-only, now also serves a WebSocket surface | 3 |
| `plans/ROADMAP.md` | Narrow the ACP entry (`:75-91`) per Phase 6's six points; update `:27` (unblocked) and `:104` (qualify *opened*); extend the `:88` mutation caveat | 6 |
| `plans/CLOSED_INVESTIGATIONS.md` | Extend `:102`'s mutation caveat (creation, not just attach) and re-point its "still live on the roadmap" clause; add a forward pointer at `:75` — "the nearest achievable thing" was in fact built | 6 |
| `plans/tests/260701_POWERATLAS.md` | §2 manifest (2.1–2.25 no longer complete), §2.16 (`lifespan` now two-concern), §2.24 (session-action set extended) | 6 |
| `memory/MEMORY.md` | `:108-112` — add an ACP carve-out; the entry currently reads as blanket closure of kiro-cli control paths, and one is now open (ACP-launched and resumed-exited sessions) | doc-table-only (at `/qclose`) |
| `README.md` | **Deliberately skipped, and contested.** `AGENTS.md:7` requires a README update for user-visible WebUI surface changes; `/acp` plus a new session-row action is one. Three sites go incomplete: `:3` (the "discovering, resuming, batch-launching" product definition), `:30` ("Click to open the dashboard UI" — now two page routes), and the `:32-53` feature list. Skipped under the throwaway-prototype exemption proposed in the Intent, which is **not yet accepted**. If declined at `/qclose`, this row becomes required work. | none (flagged) |

## 9) Implementation Divergences from Plan

### Phase 1

- **Three references on §1's "verified accurate, leave untouched" list were rewritten anyway.**
  `index.html:166`, `presence.py:65` and `test_web.py:3774-4003`. Each rewrite is defensible under
  Phase 1's own durable-fix guidance, and two were forced: `test_web.py` was being edited by Phase 2
  during the session, and `index.html` is in Phase 5's file scope. `presence.py:65` turned out not
  to be accurate at all — it points at the `_PROVIDER_SPECS` dict header while the `--resume-id`
  literal its sentence describes is two lines below. **The "leave untouched" list was itself
  unverified**, which is what a pre-verified label invites.
- **The §1 table's own `ROADMAP.md` doc-site line numbers were stale by +8**, from commit `391808b`
  landing after the plan was written. Rows were matched by cited content instead. This offset is
  recorded here because Phase 6's exit criteria address `ROADMAP.md:75-91`, `:80` and `:81` as
  though they were addresses; the plan warns they are provenance, but the wording reads otherwise.
- **`config.py:271-291` pointed one line past end of file** and was corrected to `:271-290`. Outside
  §1's enumeration — added by `391808b`.
- **Three further `web.py` citations added by `391808b`** were converted to symbol anchors beyond
  the three the plan named, on the same reasoning.
- **A `## Platform` roadmap entry was added** proposing the `search`/`partials_workspaces`
  unification. Not in §8's Documentation Updates table for Phase 1. Added on explicit user decision
  after a Phase 2 reviewer identified the duplication as this defect's recurrence vector.
- **That entry produced false claims in two successive drafts before being simplified.** Draft one
  asserted a `/search` test count that Phase 2 invalidated in the same session; draft two replaced it
  with a fabricated behavioural claim (that a provider-filtered search overstates its session count —
  false, because `search()` filters by provider before grouping) and a `Snapshot(` total its own
  embedded grep contradicted. Both were caught by review. The entry now asserts no counts at all.
  **This is the plan's own line-number hazard generalised**: the durability problem is not line
  numbers, it is any census asserted about concurrently-edited files.
- **De-numbering was applied selectively, not uniformly.** `config.py:271-290` was re-derived rather
  than replaced with a `save_config` anchor, and several accurate coordinates were left numbered:
  `launcher_modal.html:10`, `launcher.py:403`, `config.py:53`, `data_kiro.py:16-25`,
  `workspace_card.html:2`, `session_row.html:1`, and six `presence.py` sites. All resolve correctly
  as committed. The `presence.py` ones are the exposure — Phase 2 modified that file this session,
  and Phase 6's re-enumeration criterion is the only backstop.

### Phase 2

**The phase grew from one line to roughly 240 lines of production change across five concerns.** Each
expansion below was an explicit user decision recorded at the turn it was made; none was
agent-initiated.

- **Invariant 4 deliberately overridden.** The Intent states "No change to `_workspace_status`'s
  signature or behaviour; this is a call-site fix only." Its **behaviour** now changes: it reads
  `snapshot.reported_status`. The signature is untouched. The override was necessary because
  unifying the poll onto `_workspace_status` would otherwise have silently dropped claude-code's
  provider-reported `waiting` signal from workspace cards. `/qdev` forbids editing the Intent
  section, so the override is recorded here rather than by rewriting the invariant.
- **Invariant 1 deviates in wording, not results.** "`/search` with `q` and `status` in `("", "all")`
  continues to behave exactly as today" holds for which cards render, and both reviewers verified the
  presence scan is still skipped. But with `status` empty and a `tag` or `time_filter` set, the
  empty-state *message* changed — that is the approved empty-state fix working as intended.
- **File scope expanded from three files to six.** The plan scopes Phase 2 to `web.py`,
  `tests/test_web.py` and `plans/tests/260701_POWERATLAS.md`. Also modified:
  `src/power_atlas/templates/index.html` (Phase 5's declared scope, user-directed),
  `src/power_atlas/presence.py` (comment only), and `src/power_atlas/status_classifier.py` (the
  cache lock).
- **The plan's test baseline was never true.** Criterion 6 says "611 tests currently pass". On clean
  `main`, `test_launch_session_kiro_binary_not_found` and `test_workspace_card_has_provider_icon_img`
  already fail. Verified by stashing all changes and re-running at `HEAD`. Actual: 625 collected,
  622 passing, those same 2 failing.
- **The plan's status-dot scope note overstates the user-visible effect.** It says filtered search
  results ship "with no status dots". True at first paint, but `_updateWorkspaceStatusDot` *creates*
  a missing dot and the 5-second poll covers exactly the live cwds a Working filter selects — so the
  gap is transient. A reviewer then found the self-healing is not guaranteed either, since
  `pollStatus` prefers a non-empty `window._activeCwds` over `_getAllVisibleCwds()`. The note is
  therefore wrong in both directions and should not be trusted as written.
- **`asyncio.to_thread` breached a boundary the plan reserved for Phase 3.** §6's risk table names
  "reader thread reaching into unlocked caches" and mitigates it with "`acp.py` imports neither
  module". Moving the poll off-loop made `status_classifier._status_cache` reachable from a worker
  thread by a different route entirely. Closed by adding the lock. **The plan predicted the hazard
  and guarded the wrong entry point.**

**Recorded, not fixed** — outside anything approved this phase:

- **Eighteen further tests in `tests/test_web.py` read the developer's real `config.toml`**, found by
  instrumenting `load_config`. Not uniformly trivial to fix: several render the full workspaces
  partial, where a real config supplies pinned folders and grouping, so a bare `Config()` could
  legitimately change what they assert. Wants a shared fixture, not eighteen decorators.
- **Eight known-flaky tests**, six in `tests/test_data.py` plus `TestWarmupPinned::test_populates_cache_for_existing_folders`
  and `TestGetAllSessionsPaginated::test_sort_order_by_updated_at`. All `(mtime, size)`-keyed cache
  tests whose write-read cycle is finer than the filesystem's timestamp resolution.
  `test_kiro_index_picks_up_a_newly_created_session` fails roughly 3 of 5 runs **standalone**. At
  this density a genuine regression in `test_data.py` could hide in the noise.
- **`/search` returns HTTP 200 with a toast partial when discovery fails**, so the new `r.ok` guard
  does not catch it — the one surviving form of the exact symptom that guard was added for.
- **Changing the status `<select>` drops the active search query.** `setStatusFilter` calls
  `refreshCards()` → `/partials/workspaces` with no `q`, so the two controls disagree about what is
  being filtered. Found during runtime QA. Confirmed pre-existing: commit `2341d68` does not touch
  `setStatusFilter`.
- **Session multi-select does not survive an auto-refresh** — the periodic poll re-renders rows and
  clears selection. Observed during QA, unrelated to this phase.

### Phase 3a

**§1's measured current-state is wrong about this machine, in a way that matters.** It states as
verified fact that `AutoWebSocketsProtocol is None` and "neither `websockets` nor `wsproto` is
installed". True of `.venv-PowerAtlas`; **false of the global interpreter, which is what runs the
app** — `websockets` 12.0 was already installed there and WebSocket support already worked.

**The two interpreters are two uvicorn minor series apart**, and this shaped the whole phase:

| | Runs | uvicorn | starlette | websockets |
|---|---|---|---|---|
| Global | the app | 0.30.1 | 0.37.2 | 12.0 |
| `.venv-PowerAtlas` | the tests | 0.49.0 | 1.3.1 | 16.1.1 |

§4's pin rationale is therefore correct but only for the venv — `websockets>=12,<17` resolved to
16.1.1 there, the last release before `websockets.legacy` is removed. A behaviour verified in one
interpreter is not verified in the other, and the phase's 403-vs-1008 handshake contract is
uvicorn-specific. **Each version leaked a different subset of the Host bypass** (below), so probing
either alone would have shipped a hole.

**Four Host-validation defects found and closed. Three predate this plan.**

1. **`testserver` in `_ALLOWED_HOSTS` was a complete bypass of both ACP controls.** `GET /acp` under
   `Host: testserver` returned the token; a handshake with that token plus matching `Host`/`Origin`
   returned `101`. It also let a rebound origin drive all 28 `@app.post` routes. It was allowlisted
   solely so Starlette's `TestClient` default `base_url` passed the guard, and a comment asserted it
   was "not publicly resolvable, so inert" — single-label names are resolvable by whoever wins
   LLMNR/NBT-NS/mDNS. **The plan instructed reusing `_ALLOWED_HOSTS`**; this is a plan assumption
   that did not survive the raised stakes, not an implementer error.
2. **The Host check sat inside the `method == "POST"` branch**, so no GET route had ever been
   checked. A rebound origin could read 248 KB of workspace and session data, plus `/`'s
   `_launchers|tojson` — which carries `[custom_launchers.env]`, the cleartext
   `AUTH_TOKEN_PRODUCTION` / `AUTH_TOKEN_STAGING` the risk table already flags.
3. **Both gates read `request.url.hostname`, which is not safe for an allowlist decision.** Starlette
   ≥1.x discards a Host failing `_HOST_RE` and rebuilds the URL from `scope["server"]`, so
   `a_b.evil.com` read as `127.0.0.1`; a malformed bracket Host raised `ValueError` and returned 500
   on every route including the static mount; and absent, **duplicated**, userinfo-bearing and
   non-numeric-port Hosts all passed. Both gates now parse the raw header and never raise. The
   duplicate-Host case was found by an agent going beyond its brief.
4. **`secrets.compare_digest` on `str` raises `TypeError` for non-ASCII**, so `?t=%C3%A9` returned an
   unauthenticated **500** on the authentication path — and the traceback reached nobody, since
   uvicorn's logger has `propagate=False` and writes to a stderr the tray process does not have.

`_ws_origin_ok` was deliberately left unchanged and verified still sound: both halves derive from
`ws.url`, so a discarded Host collapses the netloc too and stops matching the attacker's `Origin`.
A test pins this and a mutation re-deriving the expected origin from the raw Host fails it.

**Deviations from the plan's stated design, all deliberate:**

- **Connect ack overloads the `meta` server type** rather than adding a ninth, keeping the wire
  contract closed for Phases 4-6. `meta` is now a general out-of-band channel, not only
  context-window telemetry — Phase 4 needs to know this.
- **Message-size rejection is close code 1009, not a typed `error` frame.** The close and the queued
  frame race; 1009 is the standard code for the condition and its reason string reaches `onclose`.
- **The connection cap is enforced after `accept()`**, so the mandated pre-accept security snippet
  stays byte-for-byte and a policy close can carry a readable reason (1013) instead of a 403
  indistinguishable from an auth failure.

**Verification traps that cost real time, recorded so later phases do not repay them:**

- **`httpx` and the `websockets` client both derive `Host` from the URI** and silently ignore a
  `Host` passed in headers. Two agents — and this orchestrator — got a **false pass** from this
  before it was understood. Every `Host` probe must be hand-written raw HTTP over a socket.
- **Starlette's `StaticFiles` sets no `Cache-Control`**, so a browser can serve a stale `style.css`
  indefinitely. The `/acp` page rendered completely unstyled while the server served the correct
  file. Any CSS verification needs a cache-bypassing reload.

**Recorded, not fixed:**

- **`Host: [::1` on `/ws/acp` still returns 500** under starlette 0.37.2, because `_ws_origin_ok` was
  explicitly out of scope. It sits behind `_acp_token_ok` — a wrong or absent token gets 403 first —
  so it is not unauthenticated, and it fails closed.
- **`/acp` serves the token with no `Cache-Control: no-store`**, so it can persist in the browser's
  on-disk cache.
- **No log line for any rejection** — token 403, origin 403, or the 1013 cap. The security surface is
  unobservable to an operator.
- **Nothing pins `starlette`.** `pyproject.toml` bounds only `websockets`; defect 3's underscore case
  is latent on the runtime interpreter and becomes live on any `pip install -e .` that upgrades it —
  which Phase 3's own pre-flight mandates.

### Phase 3b

- **§1's process-tree figure is one short.** Measured 1 parent + **6** descendants for the first
  session, +5 for each after. The extra is the parent's own `conhost` from `CREATE_NO_WINDOW`.
  §4's cost model (`~1 + 5N` processes, `~280 + 306N` MB) should read `~2 + 5N`.
- **`session/new` is faster than §1 measured, once warm.** 6.83 s for the first session including
  spawn and handshake; ~2 s for subsequent sessions on the live process, against §1's 5.84 s.
- **The plan contradicts itself on teardown.** §6's mitigation row says "no graceful protocol
  shutdown, **no waiting on the agent**", but the plan's own code specimen includes
  `psutil.wait_procs(timeout=3)`, which is a wait on the agent. The implementation followed the
  specimen. The comments that misdescribed the budget were corrected; the code was not changed to
  match the prose.
- **The shutdown budget exceeds the join.** uvicorn's 0.1 s poll + `Server.shutdown`'s fixed 0.1 s
  sleep + up to 2.0 s of socket drain + 3.0 s `wait_procs` ≈ **5.2 s** against `__main__.py`'s 5 s
  `join`. Benign *only because* `os._exit(0)` then closes the job handle and the OS kills the tree —
  which is why job-object acquisition was made fatal rather than a logged downgrade.
- **`shutil.which()` returns `kiro-cli.EXE`**, uppercase. A `.cmd`/`.bat` suffix check that does not
  case-fold silently never fires.
- **The isolation criterion is self-referential.** `grep -n "status_classifier\|notifications"` over
  `acp.py` must return nothing, so the module docstring cannot *name* the two modules whose caches
  the boundary exists to keep unreachable. It describes them instead. A first draft broke the plan's
  own check by explaining it.
- **`acp.py` gained its first intra-package import**, `config.CONFIG_DIR`, because the neutral-cwd
  criterion names it explicitly. `config` imports nothing from the package, so the two unlocked-cache
  modules stay unreachable by import graph rather than by discipline.
- **The 3b review's own verification method leaked what the phase exists to prevent.** A probe
  spawned `kiro-cli acp` directly to test session reopening — outside the supervisor, therefore
  outside the Job Object — and left a **21-process tree** running. Found and killed by the
  orchestrator. The shipped teardown was never at fault; the guarantee simply does not extend to
  processes the supervisor did not spawn. **Any future verification must drive the supervisor, not
  the binary.**
- **The implementing agent's session accounting was wrong in both count and location.** It reported
  three sessions, all "against `…\poweratlas-acp-scratch` and never a real project". The store showed
  **seven** — and only three of those used the scratch workspace. The other four
  (`480b714f`, `85698bcd`, `91d801d3`, `9c7a207b`) were created against
  `C:\Users\…\OneDrive - Pole Star\Downloads`, **directly violating the brief's "never a real
  project" constraint**. All four carry `session_created_reason: subagent` and no `.history`, so
  they are unambiguously ACP-created rather than the user's. No session was ever prompted, so no
  tool executed anywhere. Caught only by auditing the artifacts against the report.
  **Cleaned up 2026-07-26**: all nine ACP test sessions removed (the seven above plus `167a54d3`
  and `457191dc` from the user's teardown tests) — 27 files, store returned to 13,298. Identified
  by `cwd`, which is the reliable discriminator; a session belonging to a terminal `kiro-cli chat`
  (home directory, carries a `.history` file) was correctly left untouched.
  **Lesson for Phase 4**: a sub-agent's own account of what it created is not evidence. Enumerate
  the store by `cwd` before and after.
- **All three human-only teardown criteria now verified (2026-07-26).** Tray quit 1 → 0 and the
  five-minute idle 1 → 1, both by the user; Task Manager kill by the orchestrator via
  `Stop-Process -Force`, which issues the same `TerminateProcess` as End Task — the 7-process tree
  was captured by pid first and **none of the seven survived**. SC 7 is therefore satisfied across
  every death route the plan names: tray, `--stop`, `--restart`, and hard kill.
  **The idle result is what makes the other four meaningful**: it rules out the alternative
  explanation that trees were dying because the handle was being collected rather than because
  teardown worked. And the tray test was the first execution of the `lifespan` teardown at all —
  before it, the log carried zero `ACP teardown:` lines across 111,869 entries.

### Carry-forward into Phase 4

Written down deliberately: these were live review findings and session facts that existed nowhere
but a conversation transcript.

- **The agent→client direction has no size cap.** `_reader_loop`'s `for line in proc.stdout` is
  unbounded, where the client→server path enforces `MAX_MESSAGE_BYTES` (256 KiB). Inert through
  Phase 3 because nothing streamed. **Phase 4 is exactly when it becomes a live unbounded-buffer
  path**, since tool output under `-a` arrives on it. Rated forward-looking, not scored, by the 3b
  review.
- **Phase 4 is where `-a` stops being theoretical.** 3a and 3b spawn an unrestricted agent but never
  prompt it, so **no tool has ever executed**. The first prompt changes that, in whatever directory
  the session was created against. Accepted per Q4 for a prototype "driven by a human watching it",
  and to be re-decided before the rebuild — but the risk profile of the phases differs sharply and
  the plan does not say so anywhere.
- **Verification must drive the supervisor, never `kiro-cli` directly.** 3b's own probe spawned the
  binary and leaked a 21-process tree, because the Job Object only covers what the supervisor spawns.
- **Scratch workspace for all test sessions**:
  `C:\Users\QSylvestre.POLESTAR\AppData\Local\Temp\poweratlas-acp-scratch`. Never a real project.
  Every session is permanent; the store stood at 13,319 after Phase 3.
- **Two verification traps that have now caught four separate agents.** `httpx` and the `websockets`
  client silently drop a header-supplied `Host` — hand-write raw HTTP. Starlette's `StaticFiles`
  sets no `Cache-Control` — hard-reload before judging appearance.
- **Environment**: the app runs on the **global** interpreter (uvicorn 0.30.1, starlette 0.37.2,
  websockets 12.0); the tests run in `.venv-PowerAtlas` (0.49.0 / 1.3.1 / 16.1.1). A behaviour
  verified in one is not verified in the other, and each has leaked a different subset of a bug.
- **Design facts Phase 4 inherits**: `meta` is a general out-of-band server type, not only
  context-window telemetry (3a); the message-size rejection is close code 1009, not a typed frame;
  the connection cap is enforced post-`accept()`; and `drain()` bounds shutdown at 2 s per socket,
  which already contributes to a ~5.2 s worst case against a 5 s join.
- **Open Low findings across 3a/3b**, recorded as `Orchestrator: proposed-accept — pending user
  decision`: no log line for any handshake rejection (token 403, origin 403, cap 1013); no
  per-socket correlation id, so open/close pairs cannot be matched at N>1; `/acp` serves the token
  with no `Cache-Control: no-store`; `SERVER_TYPES` is defined but never validated against; a
  `session/new` timeout can leave a real session created but unrecorded and uncounted against the
  cap; and `stderr=DEVNULL` means an agent-side failure has no diagnostic trace beyond an exit code.

**Side effect on the user's machine, corrected.** An agent probing that loopback POSTs still worked
POSTed to `/api/save-setting` believing it was re-saving the current value; the configured port
moved 4915 → 8080. Restored to 4915 on user instruction, verified as a single-line change against a
backup with the file size byte-identical. `save_config` rewrites the whole file, so the round-trip
cannot be proven value-preserving beyond structural inspection — the credentials block was
deliberately not enumerated.

### Phase 4

- **A shell tool executed under `-a`, unprompted, and wrote outside the session's cwd.** This is the
  phase's most important finding and it corrects two earlier accounts of it. Verified from the
  store's `.jsonl` transcripts: kiro-cli emitted a `shell` tool use carrying
  `__tool_use_purpose: "Set session tab title on first response"`, running a PowerShell command that
  read **and wrote** `%USERPROFILE%\.kiro\sessions\cli\<sid>.json`. It exited 0. The session's own
  recorded permissions were `allowed_read_paths: [<scratch dir>]`, `allowed_write_paths: []`,
  `trusted_tools: []` — so **`-a` overrode the recorded filesystem scoping**, and the write landed in
  the real kiro-cli store that the dashboard parses and Phase 5 is specified to read.
  - **It is not universal.** The implementing agent reported it fires "on the first prompt of every
    session". False: it fired on 2 of 3 sessions (`3a6702ed`, `73a40df3` — both from the same
    72-character prompt); `acaf903b` records `builtin_tool_uses: 0` on both turns. **The trigger
    condition is unknown**, so the correct planning assumption is "an ungated shell command may run
    on any turn", not "on the first turn of every session".
  - **Titles are also set without a tool.** `acaf903b` has a title and ran no tools, so the
    tool-rewrite is one of at least two title paths. This orchestrator initially inferred from that
    fact that the mechanism was refuted outright — wrong, and caught only because a reviewer read the
    `.jsonl` transcripts this orchestrator had not opened. The `.json` file records a count
    (`builtin_tool_uses`) and never the tool's identity; the `.jsonl` sibling carries the record.
  - **User decision (2026-07-26)**: `-a` stands and the prototype continues; the correction is
    recorded here rather than reopening §3's decision.
- **The tool-call audit line never ran.** Phase 3b added an INFO log line in `_on_notification` as the
  entire mitigation for "execution capability arrives with `-a` now; without this line three phases
  would run commands with no record anywhere". `orchestrator.log` holds 112,215 lines and **zero**
  `ACP tool` entries. Cause: file logging is installed only in `__main__.py:_run_foreground`, so an
  app started any other way — as Phase 4's own verification was — writes no log at all. The
  safeguard's coverage silently depends on how the app was launched. Its test also asserted only the
  not-forwarded half while its name claimed it covered logging.
- **A count is not a memory bound, twice over.** `HISTORY_MAX_BYTES` summed `len()` on `str`, which is
  characters: a nominal 2 MiB budget held 8.24 MB of UTF-8 and serialized to a measured **24.7 MiB**
  `history` frame, built synchronously on the loop this phase's own exit criterion pins for streaming
  smoothness. The identical defect stood on `_Connection._out` — 256 frames with no size bound, so
  256 MiB per socket and 2 GiB across eight — even though the commit's own comment argues the point
  for the history buffer. Both now carry byte budgets.
- **Reload and reconnect are not the same recovery.** The plan's exit criterion tests reload, which
  works because a reload re-renders the template. Reconnect reuses the same `connect()` *without* the
  re-render, so `ACP_SID` — the one value only a re-render refreshes — was stale, and reconnect either
  subscribed to nothing or silently switched the user to an older session. No server-side test could
  have caught it; the defect lives entirely in page state.
- **`agent_message_chunk` carries `content` as a single object**, not the list of content blocks the
  spec's shape suggests. Measured on kiro-cli **2.14.2** — itself a correction: §1 records 2.14.1, and
  the agent self-updated between Phase 3 and Phase 4.
- **Two undocumented notification shapes.** A kiro-private `_kiro.dev/session/update` method carries
  `tool_call_chunk`, and four `_kiro.dev/*` notifications arrive with no `sessionUpdate` field at all.
  **Phase 6 must key tool rendering off `update.sessionUpdate`, not the JSON-RPC method name.**
- **`session/prompt` needs its own ceiling.** A trivial 20-line answer took ~24 s wall clock; a turn
  running tools under `-a` is minutes, so `REQUEST_TIMEOUT_SECONDS` (90 s) would abandon working
  turns. `PROMPT_TIMEOUT_SECONDS` is 600 s — bounded, so a dead agent stays distinguishable from a
  slow one.
- **Session accounting held this time.** 3 sessions, all against the scratch directory, ids and `cwd`
  read back from the store rather than taken from the agent's report. Store 13,298 → 13,307 (+9 =
  3 × 3 files), verified independently. A later agent reported the store at 13,012 and inferred
  ~295 files had been deleted; that was a counting artifact on its side — the store was intact at
  13,307 throughout, confirmed by extension breakdown.

## Review Log

### 2026-07-25 — Plan review (via /qplan Step 4)

Three personas in parallel: Architect (gap-critic lens), Security auditor, Senior engineer.
**26 + 17 + 14 raw findings; 31 after dedupe** (10 High, 17 Medium, 4 Low). **27 auto-resolved**,
4 escalated as user decisions — **all four resolved the same day** (see §5). Confidence before
fixes: 55% / 60% / 85%.

Two reviewers ran live probes rather than reading only — teardown timing, the origin snippet
against a real server, and `websockets`+uvicorn compatibility were measured, not inferred.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Origin check read its two halves from different sources, so a Host failing Starlette's `_HOST_RE` satisfied the loopback allowlist while matching an attacker's Origin | Fixed — both sides now derive from `ws.url`, extracted as `_ws_origin_ok` |
| 2 | High | `--stop`/`--restart` hard-kill via `TerminateProcess`, never running `lifespan`, orphaning the full 1+5N tree on the path dev iteration uses most | Fixed — Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` as the guarantee; `lifespan` kill demoted to a fast path |
| 3 | High | Browser↔server wire protocol and socket→session binding never specified; SC 3 unachievable and Phases 4-5 blocked | Fixed — envelope, message types, and `?sid=` identity specified in Phase 3 |
| 4 | High | XSS unflagged: agent output streamed into `innerHTML` executes in-origin and can drive `/ws/acp`, which under `-a` is full tool access | Fixed — no-`innerHTML` rule, decision row, and Phase 4 exit criterion |
| 5 | High | The `-a` rationale was factually wrong — `/acp` replaces the TUI where the permission gate actually lives, so `-a` removes the only gate rather than matching a default | Fixed — decision row rewritten on accurate grounds; choice unchanged |
| 6 | High | No handling for agent→client requests; an unanswered `session/request_permission` hangs the turn indistinguishably from normal latency | Fixed — catch-all responder, per-request timeout, explicit `clientCapabilities` |
| 7 | High | `stdin` writes never specified as flushed or serialized; an unflushed request is invisible to the agent | Fixed — explicit flush-and-serialize rule |
| 8 | High | `stderr=PIPE` with no drain deadlocks the child at the ~64 KB buffer; the "0 bytes" evidence was N=1 | Fixed — `stderr=subprocess.DEVNULL` |
| 9 | High | Session close was the named mitigation for the 1.2 GB memory risk but no phase built it | Fixed — added to Phase 6 with a before/after measurement criterion |
| 10 | High | The 1008 exit criterion is unverifiable — uvicorn converts a pre-`accept()` close into HTTP 403 and discards the code | Fixed — restated as HTTP 403 across four negative cases |
| 11 | Medium | §1 table mapped `ROADMAP.md:52` to the kiro-cli branch; the construct it describes is the claude-code branch | Fixed — corrected to `status_classifier.py:118-122` |
| 12 | Medium | Phase 3's "no workspace gains a live dot" contradicted Q7 — the sidecar path lights the real workspace for ≤120 s independently of process cwd | Fixed — split into two criteria separating the verifiable claim from the accepted one |
| 13 | Medium | Phase 1 rewrites the two files Phase 6 then addresses by line number — the mirror of the hazard caught for `web.py` | Fixed — Phase 6 targets restated as quoted anchors, bottom-up application |
| 14 | Medium | Phase 2 fixes shipped code with no regression test; the prototype waiver does not cover it | Fixed — one test added to `tests/test_web.py`, modelled on `test_search_with_tag_filter`; waiver confirmed prototype-scoped |
| 15 | Medium | Reader thread: loop capture, daemon flag, and exception handling all unstated; `call_soon_threadsafe` raises on a closed loop | Fixed — all three specified in Phase 3 |
| 16 | Medium | Correlation table had no timeout and no agent-death path despite "health comes from the JSON-RPC channel" | Fixed — timeouts plus EOF rejection and an `agent_died` frame |
| 17 | Medium | 3-5 day estimate contradicted the cited ~1 week, which covers only two of six phases | Fixed — revised to 7-10 days; alternative recorded as §5 open item 4 |
| 18 | Medium | Phase 3 bundles four independent failure domains, so a teardown failure masks transport health | Fixed — 3a/3b seam documented as a split-on-trouble instruction |
| 19 | Medium | Fan-out had no per-socket writer or replay cursor, making the two-tab criterion unachievable | Fixed — per-socket queue and replay cursor specified |
| 20 | Medium | Client-supplied session id joined into a filesystem path with no validation | Fixed — reuse `launcher.py:25`'s `_SESSION_ID_RE` and 128-char cap |
| 21 | Medium | `websockets` unpinned; uvicorn 0.49 selects the deprecated legacy implementation | Fixed — pinned `<17`, with the explicit-selector alternative recorded |
| 22 | Medium | Blast radius understated — cleartext `AUTH_TOKEN_PRODUCTION`/`AUTH_TOKEN_STAGING` in the live config are one `fs/read` from an `-a` session | Fixed (risk row) — remediation itself out of scope, flagged to the user |
| 23 | Medium | No cap on connections, sessions, or WS message size | Fixed — caps added as a Phase 3 requirement |
| 24 | Medium | Streaming smoothness measured on an idle loop that in reality stalls every 5 s on 2 MB transcript reads | Fixed — criterion now requires the dashboard open in another tab |
| 25 | Medium | Tree-kill leaves an orphaned `.lock`; no criterion verified a killed session is still resumable | Fixed — kill/restart/reopen criterion added to Phase 3 |
| 26 | Medium | Tool-call logging arrived only in Phase 6, leaving three phases executing commands with no record | Fixed — logging moved to Phase 3 |
| 27 | Medium | Tree-kill had no PID-reuse guard, the exact hazard `presence.py` defends against | Fixed — `poll()` guard, parent-first ordering, `wait_procs` |
| 28 | Low | Ring buffer silently truncates replay, and the "bounded" criterion rewarded truncation without detecting it | Fixed — `history_truncated` marker as the first replayed item |
| 29 | Low | `_ws_origin_ok` inlined, so a future `@app.websocket` route could ship without it | Fixed — extracted and documented as mandatory |
| 30 | Low | Neutral cwd never named; `Path.home()` is plausibly a real workspace | Fixed — named `CONFIG_DIR / "acp-cwd"` |
| 31 | Low | Bare `"kiro-cli"` spawn ignores the `.cmd`-wrapper trap recorded in memory; no `creationflags` | Fixed — `shutil.which()` assertion and `CREATE_NO_WINDOW` |

**Not accepted**: one reviewer suggested annotating Phases 3-6 with `[P:N]` for symmetry. Declined —
`[P:1]`/`[P:2]` on Phases 1-2 already satisfies the symmetry rule (each partner names the other);
Phases 3-6 are sequential and correctly carry no annotation.

### 2026-07-25 — Escalation resolution

The four items escalated above were resolved the same day; each strengthened the plan beyond the
reviewers' recommendations. Two are worth noting as decisions rather than fixes:

- **The Job Object was chosen over the pid-file reaper the reviewers offered as the simpler
  option.** Both cover `--stop`/`--restart`; only the job covers crashes and Task Manager, and it
  makes teardown an OS property rather than code that must execute — which matters more than usual
  given tests are waived for the phases that spawn processes.
- **The WS token was added even though "unauthenticated local access is pre-existing" is true.** It
  is true but not equivalent: the existing surfaces open a folder or run a user-saved launcher,
  whereas `/ws/acp` under `-a` is agent-driven arbitrary execution. The residual gap (the token
  ships inside an unauthenticated page) is recorded in Phase 3 rather than presented as closed.

**Contradiction reconciled**: the Architect and Senior engineer both executed the original origin
snippet and reported it working, while the Security auditor called it bypassable. Both are correct
— the probes used a well-formed `Host` header, where the check behaves properly; the auditor
analysed the malformed-`Host` path where Starlette falls back to `scope["server"]`. Finding 1's fix
is strictly safer and free, so it was applied regardless.

### 2026-07-25 — Implementation Review (after Phase 1, persona: Senior engineer)

Implementation health: Green.
Three review cycles. Cycle 1: 5 findings (1 Medium, 4 Low). Cycle 2: 5 findings (2 Medium, 3 Low),
one of which was a **regression introduced by cycle 1's own fix**. Cycle 3: verified clean.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Cycle-2 fix fabricated a behavioural claim: that a provider-filtered search overstates its session count | Fixed — verified false (`search()` filters before grouping); entry simplified to drop the census |
| 2 | High | The replacement `Snapshot(` count (18) was contradicted by the entry's own embedded grep (16) | Fixed — count removed entirely rather than re-derived |
| 3 | Medium | Three `index.html` citations left as line numbers though Phase 5 edits that file | Fixed — converted to `_launchers` bootstrap, `editLauncher`, `handleItemClick` |
| 4 | Medium | Roadmap entry's `/search` request count went stale within the session that wrote it | Fixed — replaced with a property claim that does not depend on a count |
| 5 | Medium | Entry's "only real differences are" list omitted four genuine divergences | Fixed — completeness claim dropped; reader directed to diff the two functions |
| 6 | Low | `ROADMAP.md` cited the parse-and-poll plan by its pre-archival slug | Fixed — repointed to `plans/done/260725-1542_PARSE_AND_POLL_PERFORMANCE.md` |
| 7 | Low | `presence.py:65` points at the `_PROVIDER_SPECS` header, not the `--resume-id` literal it claims | Fixed — converted to a `_PROVIDER_SPECS` symbol anchor |
| 8 | Low | `CLOSED_INVESTIGATIONS.md` attributed "15+" call sites to one file when the total spans two | Fixed — census removed; durable property statement retained |
| 9 | Low | The +8 `ROADMAP.md` doc-site skew was recorded nowhere, though Phase 6 targets those numbers | Fixed — recorded in §9 |
| 10 | Low | `config.py:271-291` pointed one line past end of file | Fixed — corrected to `:271-290` |
| 11 | Low | De-numbering applied selectively; several accurate coordinates left as line numbers | Orchestrator: proposed-accept — pending user decision |

Two findings not in the table because they are this plan's own text rather than Phase 1's output:
§1's "verified accurate, leave untouched" list contained at least two references that were not
accurate, and Phase 6's exit criteria are phrased as addresses over numbers the plan elsewhere calls
provenance.

The cycle-2 regression is the entry worth reading twice. The correction round whose whole purpose
was removing false claims introduced a new one, more confident and more plausible than the stale
count it replaced. A census asserted about a file a sibling phase is rewriting cannot be made
durable by re-deriving it more carefully; it has to stop being a census. That is why the entry now
contains a commit SHA, four symbol names, and no numbers.

### 2026-07-25 — Implementation Review (after Phase 2, personas: Maintainability reviewer, Senior engineer)

Implementation health: Green.
Four review cycles, two personas per cycle, merged. Totals across cycles: 45 findings
(0 High, 13 Medium, 32 Low). No cycle produced a regression.
QA verification: **PASS** (2 surfaces verified — HTTP endpoints and browser UI; 28 probes executed).

#### Test execution summary

| Phase | Tests | QA | Notes |
|---|---|---|---|
| 1: Correct stale line-references | not_run | SKIP | Prose-only; no executable surface. Verified by opening every cited line. |
| 2: Fix the `/search` status-filter crash | pass | PASS | 622 passed, 2 failed (both pre-existing on clean `main`), 1 skipped. |

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | Regression test mocked away `_workspace_status`, so its pre-fix `TypeError` came from the mock, not production | Fixed — rebuilt to mock only at data-source boundaries; real filter path now executes |
| 2 | Medium | Filtered-to-empty search claimed the query matched nothing, hiding that the status filter emptied it | User: accepted — fix now; cascade added mirroring `partials_workspaces` |
| 3 | Medium | `search()` duplicates the whole `partials_workspaces` pipeline; the recurrence vector was unrecorded | User: accepted — roadmap entry added (Phase 1 file scope) |
| 4 | Medium | Poll and render computed the workspace dot two different ways and could disagree | User: accepted — fix now; `api_session_status` unified onto `_workspace_status` |
| 5 | Medium | That unification dropped claude-code's provider-reported `waiting` at workspace level | User: accepted — `_workspace_status` now reads `reported_status`; overrides invariant 4 |
| 6 | Medium | `_map_reported_status`'s hoisted docstring stated a contract `_session_status` violates | Fixed — docstring describes the mapping only; precedence moved to each call site |
| 7 | Medium | Poll reached `data.get_sessions` — a blocking cold-cache disk read on the event loop | Fixed — whole per-cwd loop moved into one `asyncio.to_thread` |
| 8 | Medium | `r.ok` guard added to `/search` but not the sibling `/partials/all-sessions` fetch | Fixed — both guarded via a shared `_runSearchPanel` |
| 9 | Medium | Empty-state cascade omitted the provider branch that `partials_workspaces` has | Fixed — branch added with search-appropriate wording, not copied verbatim |
| 10 | Medium | Poll hardcoded both providers while renders pass the active filter | Fixed — provider now travels with the poll request, backward-compatibly |
| 11 | Medium | Poll's `workspaces` map can contradict its own `sessions` map | Fixed — behaviour kept as more correct; pinned by a test in both directions |
| 12 | Medium | Plan recorded none of the approved expansion: §9 reserved, scope and invariants unamended | Fixed — this commit |
| 13 | Medium | Dead `_age_seconds` kept alive only by its own test | User: accepted — deleted with its test and sole `timezone` import |
| 14 | Low | `status_classifier._status_cache` was the only unlocked cache, now reached from a worker thread | Fixed — `threading.Lock` added; never held across file I/O |
| 15 | Low | Three of five assertions in the report test passed whether or not the report was read | Fixed — tautological cases relocated to the call site where they can fail |
| 16 | Low | `.catch` reorder left DOM-swap throws with no handler at all | Fixed — separate pre-swap and post-swap handlers |
| 17 | Low | Failure latches never re-armed when the query was cleared | Fixed — `_resetSearchFlags()` on the early-return path |
| 18 | Low | Non-string `provider` raised `TypeError` outside the guard and 500'd the endpoint | Fixed — coerced; verified live with `[]`, `7`, `{}`, `null` |
| 19 | Low | Two poll tests asserted against an explicit provider set where the render passes `None` | Fixed — corrected; mutation-verified as strictly more discriminating |
| 20 | Low | Remaining Lows from the final gate (comment precision, test-builder overlap, `_raise_status` guard, `kiro-ide` comment) | Orchestrator: proposed-accept — pending user decision |

The 32 Low findings are consolidated above; individually they were comment-precision, test-hygiene
and naming items, each recorded in its cycle's sub-agent output.

**Runtime QA (Step 5b, `[QA]`-annotated phase).** PowerAtlas was restarted so it served committed
code — both the global and venv installs are editable and resolve to this repo, verified before
testing. Ten HTTP probes across the status-filter topologies: `status=working` returns 200 with
cards where it previously 500'd, `waiting`/`errored` render filter-specific empty states, an
unmatched query returns `No results for …` with or without a filter, and quotes are escaped. Ten
further probes on the poll endpoint confirmed the non-string `provider` guard and backward
compatibility. An origin-less POST was correctly refused with 403, incidentally confirming
`same_origin_guard`.

Eighteen browser checks were driven live. The two that matter:
`GET /search?provider=all&status=working&q=ol` returned **200** — byte-for-byte the request shape
that previously threw. And with `window.fetch` monkey-patched to return a 500 carrying an injected
marker, that marker **never reached `#workspace-cards`**, which retained its previous 82 cards
intact; pre-fix the error body was written straight into the container. The toast latched correctly
(two failed calls, one toast) and the run produced zero console errors and zero unhandled
rejections.

**Not verified**: the provider-reported `waiting` dot end-to-end. No agent process was live during
QA, so every workspace correctly reported `closed` and that path was unreachable without
manufacturing session state. It is covered by mutation-verified unit tests, not by observation.

**Method note.** From cycle 3 onward reviewers verified tests by **mutation** rather than by reading
— neutering each fix and confirming the corresponding test fails. This found three assertions that
passed whether or not the behaviour they named was present, which no amount of reading would have
surfaced, and it caught one reviewer's own draft weakness. The JavaScript was mutation-tested under
Node with stubbed `fetch`/DOM/`htmx`, since the pytest suite does not reach it.

### 2026-07-25 — Implementation Review (after Phase 3a, personas: Security auditor, Reliability engineer)

Implementation health: Green.
Two review rounds, two personas each. Round 1: 22 findings (2 High, 8 Medium, 12 Low). Round 2
(final gate): 14 findings (1 High, 3 Medium, 10 Low). Both rounds returned **no REGRESSION** and an
explicit **commit: yes**. The round-2 High was an *incomplete* fix rather than a new hole — strictly
better than the state it replaced — and was fixed before commit anyway on user instruction.
QA verification: **PASS** — transport-level and browser, on the interpreter that serves the app.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `testserver` in `_ALLOWED_HOSTS` fully bypassed both ACP controls and exposed all 28 POST routes | User: accepted — fix now; removed, tests repointed to a loopback base URL |
| 2 | High | Host allowlist read `request.url.hostname`; underscore Host read as loopback, malformed Host 500'd every route | User: accepted — fix now; both gates parse the raw header and never raise |
| 3 | High | `compare_digest` on `str` returned an unauthenticated 500 for non-ASCII tokens | User: accepted — fix now; byte comparison, 403 for every hostile shape |
| 4 | Medium | Host check was POST-gated, so 17 GET routes leaked workspace data and launcher `env` under a rebound origin | User: accepted — fix now; check moved out of the method gate |
| 5 | Medium | `GET /acp`, the token's only delivery vehicle, had no Host check at all | User: accepted — fix now; inline check added and independently tested |
| 6 | Medium | `stop()` cancelled the writer with a full queue — measured 6 frames queued, 0 arrived | User: accepted — fix now; `drain()` with a 2 s bound, 6 of 6 arrive |
| 7 | Medium | Writer death left a registered socket with no writer, silently swallowing every frame | User: accepted — fix now; `_retire()` closes on every exit path |
| 8 | Medium | `web.py` imported `acp` at module scope; a broken prototype module would kill the whole dashboard | User: accepted — fix now; guarded import, `/acp` degrades alone |
| 9 | Medium | Per-launch token rotation stranded any tab open across a restart on an infinitely-retrying button | User: accepted — fix now; liveness probe distinguishes stale token from dead server |
| 10 | Medium | Malformed `Host` raised `ValueError` → 500; widened from POST-only to every route by finding 4's fix | Fixed — subsumed by finding 2's raw-header parsing |
| 11 | Low | `/acp` inline Host check was untested — deleting it left all 28 tests green | Fixed — test drives the router directly so only the inline check can 403 |
| 12 | Low | Remaining Lows: no rejection logging, no per-socket correlation id, token cacheable, unused `SERVER_TYPES`, double-buffered size cap | Orchestrator: proposed-accept — pending user decision |

**Method note.** Every `Host` probe was hand-written raw HTTP. `httpx` and the `websockets` client
both derive `Host` from the URI and silently drop a header-supplied one — this orchestrator reported
a false 403 refutation of finding 2 before catching it, and two review agents hit the same trap
independently. Both starlette versions were probed separately, which is what caught the split: 1.3.1
leaked the underscore case, 0.37.2 leaked duplicate-Host, absent-Host, userinfo and non-numeric-port.
The final fix round mutation-tested its own tests, killing 10 of 11 mutations; the survivor is a
deliberately redundant userinfo blacklist, kept and documented as untestable rather than removed.

Suite: **769 passed, 1 skipped** (611 at plan time). The 145 regression tests added across this phase
cover shipped code and are outside the prototype waiver, which applies to `acp.py` and the ACP routes
only.

---

### 2026-07-26 — Implementation Review (after Phase 4, personas: Security auditor, Reliability engineer)

Two personas in parallel, fresh context each, both read-only and forbidden from spawning the agent.
**12 findings after merge** (2 High, 5 Medium, 5 Low). Health at review: **Red**; after fixes:
**Green** with three Lows open.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Reconnect subscribed with the render-time `ACP_SID`, attaching to a stale session or none — transcript cleared, newer session stranded while still holding a slot | Fixed — every read after init uses the live `sessionId` |
| 2 | High | Tool execution under `-a` was invisible on every channel: not rendered until Phase 6, log line never exercised, and its test asserted only the not-forwarded half | User: accepted — render and log both, now; tool calls forward, record, replay and render with their command |
| 3 | Medium | `HISTORY_MAX_BYTES` counted characters, not bytes — measured 24.7 MiB serialized for a nominal 2 MiB budget, built on the loop the streaming criterion pins | Fixed — recursive UTF-8 byte walker; budget test now uses non-ASCII |
| 4 | Medium | Send queue bounded on frame count only: 256 MiB per socket, 2 GiB across eight — the defect this same commit fixed for the history buffer | Fixed — 8 MiB byte budget; never refuses onto an empty queue, which would park the writer behind a flag it never wakes to read |
| 5 | Medium | `sendPrompt()` cleared the textarea even when the send was refused, and Enter had no turn guard — a mid-turn prompt was destroyed | Fixed — `send()` reports delivery, text restored on refusal, one guard shared by Enter and the button |
| 6 | Medium | Replayed `error`/`meta` frames rendered nowhere, so a failed turn replayed as a prompt with no answer and no explanation | Fixed — errors and abnormal turn ends write to the transcript, not only the log strip |
| 7 | Medium | Subscribe carried no turn state; the page inferred it from a marker the ring buffer is designed to evict, while `inflight` sat in scope unused | Fixed — `turnActive` on the `session` frame; `setTurn` suppressed during replay, without which the server-side half was dead code |
| 8 | Low | The no-`await` invariant in `_handle_subscribe` held, but nothing enforced it | Fixed — `inspect`-based guard on `_handle_subscribe` and `_dispatch` |
| 9 | Low | No server-side trace for any prompt or subscribe refusal, including `not_subscribed` — the symptom of finding 1 | Fixed — all five refusals, both subscribe outcomes, and turn start/end now log |
| 10 | Low | `subscribe` is unthrottled and re-serializes the whole replay per call | User: accepted — fix now; per-socket 1 s floor on replay, typed `subscribe_throttled` refusal (`4549701`) |
| 11 | Low | No Content-Security-Policy on `/acp`; the no-`innerHTML` rule is the sole XSS control, now that agent-authored commands render | User: accepted — fix now; per-response nonce policy scoped to `/acp` (`4549701`) |
| 12 | Low | `json.dumps` defaults to `ensure_ascii=True`, so non-ASCII still expands ~3x on the wire | User: accepted — fix now; `ensure_ascii=False` with a per-frame ASCII fallback, so a lone surrogate cannot retire the socket (`4549701`) |

**Fix round 2 (`4549701`, scope exception to `web.py` / `base.html`).** The CSP is nonce-based
because the alternatives are worse than nothing here: `script-src 'self'` breaks `acp.html`'s own
inline script outright, and `'unsafe-inline'` permits both injected `<script>` and `<img onerror=…>`
— the exact vector it would exist to stop. `connect-src` names `ws://`/`wss://` explicitly rather
than trusting `'self'` to cover a WebSocket upgrade, since a policy that forbids `/ws/acp` breaks the
whole feature while passing every server-side test. Verified independently of the implementer: the
header nonce matches both script tags, no script tag lacks one, the nonce is fresh per response, and
`/` carries neither header nor nonce attribute. Confirmed adversarially in a real browser on the
global interpreter — an injected inline script, a wrong-nonce script, and an `<img onerror>` handler
all blocked, with the page itself raising no violation.

The `ensure_ascii` guard exists because `json.loads` accepts a `\udXXX` escape from the agent and
returns a lone surrogate that UTF-8 cannot encode; without the fallback the exception would surface
inside `ws.send_text`, where `_write_loop`'s catch-all retires an otherwise healthy socket. Note the
budget accounting was already counting UTF-8 bytes, so while the wire was escaped the queue and
history buffer were bounding roughly a third of the memory they were sizing.

The throttle is per socket, which closes the real amplification — many `subscribe` frames on one
socket — rather than the reconnect loop the finding described: `connect()` builds a fresh socket each
time, so a reconnect loop sends one `subscribe` per socket and is already bounded by
`MAX_CONNECTIONS`.

Suite after fix round 2: **842 passed, 1 skipped.** 11 mutations run, 11 caught.

**What the reviewers verified rather than assumed.** Both confirmed the no-`innerHTML` discipline
across the *whole* client path — `insertAdjacentHTML`, `outerHTML`, `document.write`, `eval`,
`new Function`, `setAttribute`, `srcdoc`, URL sinks, and payload-derived `className` — not just the
one grep the exit criterion names. The reader cap was exercised against 40 MiB of newline-free input:
one ERROR line, one delivered line, peak delivered length 17 bytes. The `-a` tool execution was found
by reading the store's `.jsonl` transcripts, which this orchestrator had not opened.

**Method note.** Findings 1 and 5-7 live entirely in `acp.html`, which the Python suite does not
cover at all. They were fixed and then verified by rendering the template through Jinja and driving
the *rendered* script over a DOM shim in Node: 21 behavioural checks pass now, and **15 of the 21
fail against `e8cb4df`** — the differential that shows the findings were real rather than stylistic.
Server-side, 20 targeted mutations, 20 caught.

Suite: **826 passed, 1 skipped** (769 before this phase). Three sessions created, all against the
scratch directory, verified by reading `cwd` back from the store rather than from any agent's report.

---

## Harness Improvement Opportunities

- `/qexplore`'s Step 3 output contract defines 9 sections, but only items 1–3 (Intent) are
  persisted to the project file; items 4–9 (patterns, risks, resolved decisions, open items,
  recommended approach, QA environment) exist only in the chat transcript — cost: in a long
  exploration the Discovery half is the more expensive output, and it is lost unless `/qplan`
  runs in the same session; here it was preserved only by hand-copying into this file —
  suggested change: persist Discovery to the project file under a transient
  `## Exploration Discovery` heading that `/qplan` folds in and removes, mirroring the existing
  `## Resolved Decisions (re-plan handoff)` precedent.
- `/qplan` Step 4 mandates appending a `## Review Log` entry after the persona review, but only the
  **Major** template defines a `## Review Log` section — the Standard template ends at
  `## 9) Implementation Divergences from Plan` — cost: a Standard-tier plan that runs the mandatory
  review has no canonical location for its output, so placement is invented per-plan and
  `/qvalidate` cannot check for it; here it was appended after §9 by choice, not by spec —
  suggested change: add `## Review Log <Reserved -- filled by review cycles>` to the Standard
  template in `shared/skills/qplan/TEMPLATES.md`, since Step 4's review is mandatory at Standard
  tier and therefore always produces one.
- `/qdev` Step 1's orphaned-working-tree rule says to "surface the situation to the user" but
  supplies no action set, unlike `/qclose` Step 2's Promote / Accept and note / Skip — cost: the
  orchestrator invented a three-option question and the user rejected all three, choosing a fourth
  framing ("assume the phase is incomplete, assess state, judge the resume point") that was the
  right one and that the rule never offers — suggested change: give the rule an explicit action set
  including *Treat as incomplete — re-verify against exit criteria and resume*, which is materially
  different from adopt-or-discard because it neither trusts nor throws away the recovered work.
- `/qdev`'s `## Multi-agent execution reference` item 6 mandates serializing auto-fix cycles in
  phase-number order, but item 1 already suppresses sub-agent commits in parallel mode, so the git
  index race that motivates serialization cannot occur — cost: following it literally would have
  doubled wall-clock across four fix rounds for no safety gain, since the two phases' file scopes
  were verified disjoint; the deviation had to be reasoned about and disclosed each time —
  suggested change: scope item 6's serialization to the commit step it protects, and permit parallel
  auto-fix when file scopes are disjoint and commits are suppressed.
- `/qvalidate`'s `commit-pairing` check silently under-covers any phase whose code commit is not
  `feat` — cost: Phase 2's code commit is `fix(<slug>): phase 2 — …`, because that is what the change
  is and `shared/AGENTS.md § Commit Conventions` explicitly permits `fix`/`refactor`; the check then
  reported PASS while its own output shows it only inspected `feat(<slug>)` commits, so Phase 2's
  pairing was never verified at all. **The expected failure was a false FAIL on a legitimate type;
  the actual behaviour is a vacuous PASS**, which is worse, because a genuinely unpaired `fix` phase
  commit would also report green — suggested change: widen the check to any Conventional Commits
  type carrying the plan slug and a phase number, and have it report the number of phase commits
  inspected so a zero-coverage pass is visible rather than indistinguishable from a real one.
- `/qdev` Step 6's cycle cap bounds orchestrator-automated cycles but explicitly exempts
  user-directed cleanup, and nothing bounds *that* — cost: four review cycles ran on a phase planned
  as a one-line deletion, each surfacing a fresh tail of mostly-Low findings (13 Medium and 32 Low
  in total), with no defined stopping point short of the user declining a further round; two
  independent reviewers had already returned "safe to commit" after cycle 3 — suggested change: have
  the post-cycle-cap prompt state the reviewers' standing verdict and the round-over-round severity
  trend alongside the action set, so the user is choosing against a visible convergence signal
  rather than an open-ended offer to keep fixing.
- `/qdev` Step 5b routes a `[QA]`-annotated phase to `/qqa`, whose runtime surfaces assume the thing
  under test can be driven safely and repeatedly. Phase 4's surface is a live agent running with
  trust-all-tools, where every exercise creates a permanent artifact in the user's real store and may
  execute an ungated shell command — cost: `/qqa` could not be dispatched as a separate read-only
  step, so runtime verification was folded into the implementation brief instead, leaving the
  implementer to verify its own work, which `shared/AGENTS.md § Multi-Agent Coordination` otherwise
  forbids ("evaluators must not evaluate their own output"); the conflict was resolved ad hoc and
  disclosed, not by any rule — suggested change: give `/qqa` a side-effecting-surface branch that
  names the tension and prescribes the split (implementer verifies, a separate reviewer audits the
  *artifacts* the run produced), rather than leaving the orchestrator to invent it.
- The harness requires a `Claude-Session:` trailer on every commit, but nothing enforces it and
  sub-agents composing their own commit messages routinely omit it — cost: 1 of the 5 commits in this
  session carries it; the miss is only discoverable after the fact, and the two ordinary remedies
  (`--amend`, `reset`) are both banned by global git-safety governance, so a missed trailer is
  permanent — suggested change: install a `prepare-commit-msg` hook that appends the trailer when
  absent, since a rule every sub-agent must remember is exactly the case automation exists for.
