# kiro-cli ACP Client Prototype

> **Date**: 2026-07-25
> **Status**: Draft  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
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

- **Tests.** Prototype only, to be rebuilt. This waives `AGENTS.md:8`; see the proposed
  durable amendment below.
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
| ACP teardown hook | In the existing `lifespan` cleanup (`web.py:381-389`) | Modify `__main__.py`'s shutdown sequence | Keeps `__main__.py` untouched, consistent with the minimal-footprint decision. Uvicorn runs lifespan shutdown on `should_exit` before the 5 s join at `__main__.py:309`. |
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
- [ ] All 8 stale sites in the §1 table corrected or converted to symbol anchors
- [ ] The three `web.py` citations converted to **symbol anchors**, not re-numbered
- [ ] Bare `:NNN` continuation forms in `CLOSED_INVESTIGATIONS.md:89` individually verified
- [ ] Re-run the enumeration and confirm every remaining `file.ext:NNN` reference resolves to the
      construct its surrounding prose describes (spot-check by opening each cited line)
- [ ] `ROADMAP.md:80` (`acp-agent.js:777`) left unchanged
- [ ] `plans/260725_PARSE_AND_POLL_PERFORMANCE.md` left unchanged — it carries four stale refs of
      its own but is a historical record of a completed analysis, not a live index (out of scope)
- [ ] Cold-read check: a reader following any corrected citation lands on code that matches the
      claim the sentence makes about it

### Phase 2: Fix the `/search` status-filter crash [QA] [P:1]

**Goal**: `/search` with both `q` and a status filter returns cards instead of a 500.

**File scope**: `src/power_atlas/web.py`, `plans/tests/260701_POWERATLAS.md`

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

**Why the test-plan probe gap matters here**: `plans/tests/260701_POWERATLAS.md:243-248` is the
brief for this exact endpoint (§2.14 Search) and its probe list — *"query matching a folder name;
no match…; rapid typing (debounce)"* — **never combines search with a status filter**. That
omission is why the bug shipped undetected. Closing it is the highest-value single edit in the
test corpus and costs one line.

**Exit criteria**:
- [ ] `GET /search?q=<term>&status=working` returns 200 with filtered cards
- [ ] `GET /search?q=<term>` (no status) behaves exactly as before
- [ ] Manual: type in the search box with each of Working / Waiting / Errored selected; no error
      text appears in `#workspace-cards`
- [ ] The four Intent invariants for this fix all hold
- [ ] `plans/tests/260701_POWERATLAS.md` §2.14 probe list extended with a search + status-filter
      combination

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

- **Tree-kill teardown** — `psutil` is already a dependency and already used at `presence.py:403`:

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

```python
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
    if not _ws_origin_ok(ws):
        await ws.close(code=1008)   # uvicorn converts a pre-accept close
        return                      # into an HTTP 403 handshake rejection
    await ws.accept()
```

Extracted rather than inlined so the rule exists in one place — it now lives in two (`web.py:415-438`
for HTTP, this for WS), and a future `@app.websocket` route could otherwise ship without it.

And ACP teardown appended to the existing `lifespan` cleanup after the `yield` (`web.py:381-389`).

**Exit criteria**:
- [ ] `pip install -e .` re-run, `pip show websockets` confirms installation, and
      `AutoWebSocketsProtocol` is no longer `None`
- [ ] `web.py`'s module docstring (`"""FastAPI web application with htmx-powered UI."""`) updated —
      the module now also serves a WebSocket surface, which is neither htmx nor request/response
- [ ] The page's WebSocket URL is built from `location.host`, with no hardcoded port
- [ ] `GET /acp` renders; the page opens a WebSocket and reports connected
- [ ] Clicking "new session" creates one; the returned `sessionId` is displayed
- [ ] The new session's `<sid>.json`/`.jsonl`/`.lock` appear in `~/.kiro/sessions/cli/`
- [ ] The handshake is **rejected with HTTP 403** for each of four cases: mismatched `Origin`,
      absent `Origin`, `Origin: null`, and a mismatched `Host` with a matching `Origin`.
      **Not** a 1008 close frame — a pre-`accept()` close is converted by uvicorn into a 403
      handshake rejection and the code is discarded (`websockets_impl.py:278-285`; confirmed by two
      reviewers running it, one observing `InvalidStatus: HTTP 403` with `.code is None`). An
      implementer chasing "1008" would loosen the check to make the criterion pass
- [ ] Probe written with `additional_headers=` (renamed from `extra_headers` in websockets 14)
- [ ] Quitting PowerAtlas **from the tray** leaves no surviving `kiro-cli` process from this
      session — `Get-Process kiro-cli` before and after (expect parent + 5 gone)
- [ ] `power-atlas --restart` and `power-atlas --stop` **also** leave none. These paths hard-kill via
      `TerminateProcess` (`__main__.py:86-91`, reached from `:341`/`:346`) and never run `lifespan`,
      so teardown alone does not cover them — and `memory/MEMORY.md:94-98` records that dev
      iteration restarts PowerAtlas constantly, making this the *most*-used path during Phases 3-6
- [ ] Spawning uses a neutral cwd, named explicitly as `CONFIG_DIR / "acp-cwd"` (created on demand)
      — **not** `Path.home()`, which is plausibly a real workspace and would be picked up by
      `presence.py`'s process scan, defeating the purpose
- [ ] The ACP process's **own cwd** contributes no `live_cwds` entry. (Distinct from: a *created
      session* may light its real workspace for up to `_SIDECAR_SKEW_S = 120 s` via the independent
      sidecar path at `presence.py:471-476` — that is accepted per Q7, not a failure. The original
      single criterion conflated the two and was unachievable as written.)
- [ ] `kiro-cli` resolved via `shutil.which()` and asserted **not** to be a `.cmd`/`.bat` wrapper
      before spawning with pipes — `memory/MEMORY.md:40-44` records that `.cmd` shims need
      `shell=True`, which is incompatible with holding clean stdio. (Verified clear on this machine:
      `where kiro-cli` → `kiro-cli.exe`. The assertion guards other machines and future installs.)
- [ ] Spawned with `creationflags=CREATE_NO_WINDOW` so no console flashes per session
- [ ] `grep -n "status_classifier\|notifications" src/power_atlas/acp.py` returns nothing — the
      isolation boundary §6 relies on as a mitigation, enforced by inspection since tests are waived
- [ ] Create a session, quit PowerAtlas, restart, and confirm **the session still reopens**.
      Teardown is `kill()`-only by design, which orphans its `<sid>.lock`; this verifies that a
      tree-killed session is not permanently poisoned in the real 13,227-session store

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
- [ ] A prompt sent from `/acp` produces text that appears progressively, not in one block
- [ ] Reloading `/acp` reconnects and replays the prior conversation
- [ ] Reload creates **no** second session — `~/.kiro/sessions/cli/` gains no new `<sid>.json`
- [ ] Two browser tabs on `/acp` show the same session's stream without cross-talk or duplication
- [ ] No agent-derived string reaches `innerHTML` — verified by inspection of `acp.html`
- [ ] Ring buffer bounded by construction (`deque(maxlen=…)`), verified by code inspection rather
      than measurement; when it has dropped events, replay emits a `history_truncated` marker as its
      first item, so SC 3's "replays the conversation" degrading to a suffix is visible rather than
      silent
- [ ] Incremental arrival still holds **with the dashboard open in another tab** — the send side
      runs on the same loop that blocks on 2 MB transcript reads every 5 s (`index.html` `_TICK_MS`),
      so smoothness measured on an idle server is not the real case

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

### Open items requiring a user decision before Phase 3

Surfaced by the review cycle; each changes what gets built.

1. **Phase 2 regression test.** All three reviewers flagged it. `AGENTS.md:8` permits a new test for
   "a regression bug fix"; the throwaway-prototype waiver does not cover Phase 2, which is permanent
   code. `tests/test_web.py` already has four `/search` tests (`:120`, `:130`, `:2713`, `:2736`),
   none passing `status=`. Options: (a) one test in the existing file, (b) widen the amendment to
   cover incidental fixes, (c) knowing one-off violation.
2. **SC 7 scope.** Teardown-in-`lifespan` is *proven* to work on the tray path (a reviewer
   reproduced `__main__.py:246-320` and measured teardown at t+0.13 s idle, t+0.21 s with a live
   WebSocket — well inside the 5 s join). It cannot cover `--stop`/`--restart`, which
   `TerminateProcess` outright. Options: (a) Windows Job Object with
   `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` — robust, kills the tree on any parent death; (b) write the
   ACP pid to `CONFIG_DIR` and reap stale trees at next startup — simpler, leaks until next launch;
   (c) narrow SC 7 to "quitting from the tray" and accept CLI-path orphans.
3. **WebSocket authentication.** Origin is currently the only control, and any local non-browser
   process can forge it. Options: (a) mint a per-process token, render it into `acp.html`, require
   it as a WS query param; (b) accept, on the grounds that the local-process class is pre-existing —
   `web.py:467-489` and the `custom_launchers` POST surface already reach `Popen` unauthenticated.
4. **Effort.** Re-estimated 3-5 → 7-10 days, because `ROADMAP.md`'s own ~1 week covers only Phases 3
   and 5. Options: (a) accept 7-10 days; (b) cut Phase 6's tool-call rendering and context telemetry
   to stretch goals and keep the original window.

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
| **`--stop` / `--restart` bypass `lifespan` entirely** — `TerminateProcess` at `__main__.py:86-91` — and dev iteration uses them constantly (`memory/MEMORY.md:94-98`) | Full 1+5N tree orphaned per restart cycle, repeatedly, during the phases that spawn most | Exit criterion in Phase 3 covers both CLI paths. If teardown-in-`lifespan` cannot cover them, the fallback is a Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, or writing the ACP pid to `CONFIG_DIR` and reaping stale trees at next startup — **user decision, see §5 open items** |
| XSS via streamed agent output rendered with `innerHTML` | Script executes in the app's own origin and can drive `/ws/acp`, which with `-a` is full tool access | Explicit no-`innerHTML`-for-agent-output rule and exit criterion in Phase 4 |
| Unanswered agent→client JSON-RPC request (`session/request_permission`, `fs/*`, `terminal/*`) | Turn hangs forever, indistinguishable from the ~5.8 s latency already expected | Catch-all error responder + per-request timeout in Phase 3; `clientCapabilities` declared explicitly |
| Undrained `stderr` pipe deadlocks the child at ~64 KB | Agent hangs with no error surfaced anywhere | `stderr=subprocess.DEVNULL` in Phase 3 |
| Unflushed `stdin` write is invisible to the agent | Presents as a hang with no error | Explicit flush-and-serialize rule in Phase 3 |
| `/ws/acp` has no cap on connections, sessions, or message size | One accepted socket can drive the machine to memory exhaustion at ~306 MB/session | Cap sessions per supervisor and set a max WS message size in Phase 3 |
| Origin is the only authentication; any local non-browser process can forge it | With `-a`, full tool access to any local process | Pre-existing class (`web.py:467-489` already reaches `Popen` from unauthenticated POSTs), but **worth a per-process token rendered into `acp.html` and required as a WS param — user decision, see §5 open items** |
| Credentials in cleartext reachable by the agent | `%LOCALAPPDATA%\power-atlas\config.toml` holds `AUTH_TOKEN_PRODUCTION`, `AUTH_TOKEN_STAGING`, `MQTT_CERT_PATH` under `[custom_launchers.env]`; one `fs/read` away from an `-a` session | Out of scope for this plan, but flagged: those tokens should move regardless of whether this ships |
| Effort re-estimated 3-5 → 7-10 days | `ROADMAP.md`'s own ~1 week covers only Phases 3 and 5, leaving nothing for 4, 6, or the two fixes | Estimate revised in the header; alternative is cutting Phase 6 telemetry to stretch goals |

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

<Reserved -- filled during implementation>

## Review Log

### 2026-07-25 — Plan review (via /qplan Step 4)

Three personas in parallel: Architect (gap-critic lens), Security auditor, Senior engineer.
**26 + 17 + 14 raw findings; 31 after dedupe** (10 High, 17 Medium, 4 Low). **27 auto-resolved**,
4 escalated as user decisions (§5 open items). Confidence before fixes: 55% / 60% / 85%.

Two reviewers ran live probes rather than reading only — teardown timing, the origin snippet
against a real server, and `websockets`+uvicorn compatibility were measured, not inferred.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Origin check read its two halves from different sources, so a Host failing Starlette's `_HOST_RE` satisfied the loopback allowlist while matching an attacker's Origin | Fixed — both sides now derive from `ws.url`, extracted as `_ws_origin_ok` |
| 2 | High | `--stop`/`--restart` hard-kill via `TerminateProcess`, never running `lifespan`, orphaning the full 1+5N tree on the path dev iteration uses most | Fixed (criterion) + Escalated — mitigation choice is §5 open item 2 |
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
| 14 | Medium | Phase 2 fixes shipped code with no regression test; the prototype waiver does not cover it | Escalated — §5 open item 1 |
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

**Contradiction reconciled**: the Architect and Senior engineer both executed the original origin
snippet and reported it working, while the Security auditor called it bypassable. Both are correct
— the probes used a well-formed `Host` header, where the check behaves properly; the auditor
analysed the malformed-`Host` path where Starlette falls back to `scope["server"]`. Finding 1's fix
is strictly safer and free, so it was applied regardless.

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
