# Dashboard Overview: Live Session Tails, Active Plans and Usage Insights

> **Date**: 2026-09-24
> **Status**: In Progress — Phases 1-5 implemented, final review and full QA pending  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Replace the dashboard's empty Transcript panel with an Overview (live session tails, active plans, 14-day usage insights), reachable again after a session is opened
> **Estimated effort**: 2-4 days

---

## Intent

### Problem statement & desired outcomes

The dashboard's right-hand panel shows only an empty placeholder ("Select a session to view
its transcript.") until a session is opened, and no path ever brings that state back. Meanwhile
three "Workspace Intelligence" roadmap items (`plans/ROADMAP.md` § Workspace Intelligence) have
no home: stale `/qdev` detection, a plan progress overlay, and usage stats.

Desired outcome: when no session is open, the panel is an **Overview** that answers, at a glance:

- **What is running right now?** Live tiles showing the tail of every live session.
- **What work is in flight?** Active plans across all workspaces with their phase progress,
  including stale ones and finished ones that still need `/qclose`.
- **How have I been working?** Usage insights over the last 14 days.

The user can return to the Overview at any time.

### Success criteria

- SC-1: With no session selected, the right panel shows the Overview, whose header label reads
  "Overview". It has three sections in this order: Live now, Active plans, Usage.
- SC-2: A Home button in the panel header, pressing Escape (when focus is not in the composer, the
  search field or a dialog), and clicking the already-open session's row each return to the
  Overview. Returning detaches from the session exactly as switching sessions does today.
- SC-3: A user-initiated Close or Delete of the open session returns to the Overview. A close the
  user did not initiate (such as the idle sweeper) keeps today's "This session was closed."
  message on screen.
- SC-4: **Live now** shows one tile per live session, where "live" is exactly the rail's own
  liveness: a session held by PowerAtlas, or one whose `live` flag `_session_is_live` sets. Live
  sessions in workspaces the rail has not loaded are included.
  - Filter: `All` (default) or `PowerAtlas`, the held sessions only.
  - At most 8 tiles, most recent activity first.
  - A tile idle for more than 2 minutes is dimmed and shows how long it has been idle.
  - A tile shows: status dot, provider icon, title, workspace, time since last activity, and the
    last ~5 events. Assistant text is shown as text, tool calls as `› tool + short argument`,
    results as ✓ or ✗.
  - Clicking a tile opens that session's transcript/session view.
  - When nothing is live, the section collapses to "No live sessions".
- SC-5: The tiles refresh about every 2 s, and only while the Overview and the browser tab are
  both visible. Plans and Usage refresh with the existing 60 s rail poll. The tiles read
  transcript files and never subscribe over `/ws/acp`, so they never count as "watching" and never
  suppress notifications.
- SC-6: **Active plans** lists every `plans/*.md` file (excluding `plans/done/`) in each known
  workspace whose Status is `In Progress`, or `Complete` but not yet archived.
  - Complete-but-unarchived plans carry a "Ready to close" badge.
  - A row shows: workspace, cleaned plan name, state badge, progress `done/total`, a thin bar
    naming the phase in progress, the truncated Status detail, and time since last edit.
  - Progress comes from the numbered rows of the `## Progress Tracker` table. Without a tracker,
    the row shows "N phases" counted from the `### Phase N` headings. Without either, it shows the
    state only.
  - An In Progress plan whose file has not changed in more than 7 days carries a "stale" badge.
  - Clicking a row expands it in place to list its tracker rows.
  - Draft and Exploring plans are excluded, as are files with no Status line.
- SC-7: **Usage** covers the last 14 days and shows:
  - Agent time per workspace this week, with the change against the previous week.
  - Daily bars of sessions and agent time, split by provider.
  - Tool reliability: the most-used tools with their failure rate, and the most-failing tools this
    week.
  - Context pressure, meaning sessions that reached ≥ 80% of the context window. kiro-cli only,
    and labelled so.
  - Model mix.
  - Claude Code token totals and cache-hit ratio, labelled Claude-only, with no cost estimate.
- SC-8: Usage is computed in memory by a background pass after startup, which parses only
  transcripts modified within the window. A per-file summary is memoised on `(mtime, size)`, so
  later passes re-parse only changed files. Until the first pass finishes, the section shows a
  loading state. Nothing new is written to disk.
- SC-9: The J4 signed-out / create-refusal check (`index.html:1158`, "exactly one `.acp-system-msg`
  child") keeps working. `node tests/acp_page.test.mjs` and the pytest suite pass.
- SC-10: All new routes stay loopback-only (the `pa_local` cookie gate, not in
  `_REMOTE_ALLOWED_PATHS`). All fetched strings are rendered with `textContent`, never
  `innerHTML`.

### Scope boundaries & non-goals

- **Out:** the "needs you" inbox (pending permissions, unanswered questions, unwatched finished
  turns). Reopen if the live tiles fail to catch sessions waiting on the user.
- **Out:** real-time streaming of held sessions over `/ws/acp` (option B of Q2). Reopen if a 2 s
  lag feels slow. It would need a subscription mode that does not count as watching.
- **Out:** persisting usage summaries to disk, and any history older than what the provider stores
  keep.
- **Out:** a cost estimate and a model-to-context-window table. Context pressure is kiro-cli only.
- **Out:** LLM-generated session names. They stay on the roadmap as their own item and are **not**
  removed.
- **Out:** Kiro IDE durations, which are always zero because `updated_at = created_at`
  (`data_kiro_ide.py:208`). Kiro IDE sessions still count as sessions.
- **Out:** an "open plan file" action and workspace-card overlays (the rail has no cards any more).

---

## 1) Current State

### Right panel (`src/power_atlas/templates/index.html`)

**Empty placeholder**

- The empty placeholder exists only in server markup: `#dashTranscript > div.acp-system-msg[data-empty]` (unique string `Select a session to view its transcript.`).
- The J4 signed-out / create-refusal path replaces the pane only when it holds exactly one child with `className === 'acp-system-msg'` (`dashReportSignedOut`, unique string `kids.length === 1 && kids[0].className === 'acp-system-msg'`).
- Its tests are the J4 block in `tests/acp_page.test.mjs` (around lines 15651-15694). They drive `dashPickerCreate`, `dashRailQuickCreate` and the connect region. The node test is not run by CI.

**Selection state**

- `_viewingSid` (`var _viewingSid = null;`) is the single staleness authority.
- It is written in exactly three places: `openSessionTranscript`, the `payload.created` branch of `dashHandle`, and `dashRailForgetSession` (the only place that sets `null`). Nothing brings back the empty state.

**Close, delete and socket teardown**

- `dashCloseIfAbandoned()` is the detach step used on every session switch. It resets client state and sends `close` only for dashboard-created sessions nothing was sent to. It does **not** detach the socket on the server.
- The server detaches a socket from its session only in two cases (`acp.py` `attach()`/`detach()`, around 2428-2450):
  - when the socket subscribes to a different session;
  - when the socket closes.
- `watched` means the session has subscribers (`acp.py` around 1224, `_registry.subscribers`). A watched session gets no `turn_end`/`agent_error` notifications. The idle sweeper skips sessions with a subscriber ("No attached subscriber", around `acp.py:7289`).
- The Close button (`dashCloseBtn.addEventListener('click'`) sends `close`. The resulting `session_closed` frame appends "This session was closed." and keeps `_viewingSid`.
- `dashRailForgetSession(sid)` is reached only from user-initiated `dashDeleteSession`/`dashDeleteWorkspace`. It shows "This session was deleted."
- `dashCloseSubagentView()` sets `dashTranscriptWrapEl.hidden = false` unconditionally (unique string `dashTranscriptWrapEl.hidden = false;`). It is called from:
  - the socket-drop cleanup in `dashConnect`'s onclose;
  - `session_closed`;
  - agent_died;
  - the create branch;
  - `dashRailForgetSession`.

**Escape and dialogs**

- The page has five document-level Escape listeners for the mode menu, rail action menus, settings, topbar settings and the ws-filter menu. They close menus synchronously, set `aria-expanded="false"` and never call `preventDefault`.
- `[aria-expanded="true"]` is almost always present on the page: expanded rail group toggles, `#dashLogToggle`, `#dashMcpToggle`.
- The picker (`#dashPicker`, `role="dialog"`) and the delete modal (`.acp-ws-delete-modal`) are `div`s, not `<dialog>`.

**Metadata strip and opening a transcript**

- `#dashSessionMeta` sits outside the pane because the pane is wiped on every load. It is the precedent for new panel content.
- Rail rows open a transcript via `openSessionTranscript(row, session, workspace)`, which reads `row.dataset.sid/provider/cwd`. `dashMaybeAttach(row, sid)` reads the same dataset and handles `locked`/`available`/`held`.
- Locked rail rows are `disabled` ("This session is open elsewhere right now.").
- `.viewing` is applied to a row during rail render, from `_viewingSid`, and by `openSessionTranscript` on the clicked row.

**Polling, sign-out and rendering**

- The rail polls `/api/dashboard/sessions` every 60 s while the tab is visible (`DASH_RAIL_REFRESH_MS`).
- Only htmx 403s reach `dashReportSignedOut`; plain `fetch` 403s do not.
- Fetched strings are rendered with `textContent` only. The dashboard page has no CSP. `_acp_csp`'s docstring premise, "the dashboard does not render agent-authored text", becomes false with this plan.

### Liveness and listing (`src/power_atlas/web.py`, `presence.py`, `data.py`)

**The live check**

- `_session_is_live(snapshot, session, provider)` (`web.py:238`) is true when either:
  - `snapshot.is_live` holds, meaning the session id is on a process cmdline; or
  - the session's cwd is in `snapshot.live_cwds({provider})` **and** its JSONL mtime is within 300 s.

**Held sessions and supervisor state**

- Held sessions are `_supervisor.sessions` (at most 8). The record from `_new_session_record(cwd)` (`acp.py:2580`) carries `"cwd"`.
- Supervisor state is loop-owned. It is captured on the loop and passed into threads (`_acp_availability` docstring, D9).
- Every listing route guards `acp is None` with `getattr(acp, "_supervisor", None) if acp is not None`.
- `_acp_availability` reads `session.json` through `_lock_holder_v3`. Without `workspace_hashes` it scans every hash dir (69 today). `_acp_listing` passes `workspace_hashes`.

**Rail filtering**

- The rail excludes workspaces tagged `hidden` and providers disabled in config: `_tag_keep` and `_enabled(config, p)`, around `web.py:2560-2568`, `2899`, `2914-2922`, `3174`.
- The rail maps normalised live cwds back to the discovered original spelling (around `web.py:2613-2624`).

**Presence snapshot**

- `presence.Snapshot` holds `_live_sids {(provider, sid)}`, `_sid_to_cwd` and `_live_cwds {(provider, normalized_cwd)}`.
- It has public `is_live`, `live_cwds(providers)` (which returns **bare** cwds), and `live_session_ids_for_cwd`.
- `get_snapshot()` has a 3 s TTL and no lock, and rescans on expiry.
- Measured 2026-09-24 in a separate process: 42–75 ms per rescan, with 5 live sids and 3 live cwds.
- Existing callers take it inside worker threads.

**Session cache**

- `data._normalize_path` casefolds on Windows.
- `data.get_sessions(cwd, provider)` (`data.py:326`) is cache-first. On a miss it records the passed cwd as the workspace's original spelling.
- `discover_workspaces_with_counts()` returns `(cwd, count, updated_at, provider)`, with no display name. Names are `Path(cwd).name`.
- Every workspace loaded into SessionCache is re-checked by `refresh_stale_entries` every 30 s.

**UNC and dead drives**

- `os.stat` on a dead UNC host took 42.2 s. `_acp_exists_flags` applies `_ACP_EXISTS_BUDGET_SECONDS = 2.0` for that reason (around `web.py:2412-2420`).
- No non-`C:` workspace exists on this machine today (2026-09-24).

**Import graph**

- `web.py` imports `acp` defensively (`acp = None` on failure). The new `overview.py` must not import `web` at module level: `web` imports it, so that would be a circular import.

### Transcript tails

- `status_classifier._read_tail_lines(path, max_bytes=65536)` (`status_classifier.py:135`) widens ×8 up to `_MAX_TAIL_BYTES` (2 MiB).
- It stops as soon as **one** complete line survives. It stats and opens the file without a try, so a vanished file raises `OSError`.
- Measured 2026-09-24:
  - a 28.7 MB Claude transcript's 64 KiB tail was 21 bookkeeping lines and **0** events;
  - the 100 MB v3 file's tail was 6 lines;
  - tail reads take about 0.3 ms.
- `_resolve_jsonl_path(session_id, provider, cwd)` (`status_classifier.py:56`) locates transcripts.
- `get_full_transcript` (`data.py:431`) plus `translate_transcript` (`transcript_translator.py:106`) are a full, uncached parse.
- Store roots exist as constants: `data_claude.CLAUDE_PROJECTS_DIR` and `status_classifier._V3_SESSIONS_ROOT`.

### Record shapes (probed 2026-09-24, key names only)

**v3 `messages.jsonl`**

- Each record is `{id, timestamp, payload{type, …}}`.
- `usage_summary.payload.elapsedTime` is an int, in **milliseconds** (verified in Phase 4, 2026-09-25; see §9).
- `tool_call.payload.{toolCallId, toolName, kind, status, title}`.
- `tool_result.payload.{toolCallId, success}`.
- `session_metadata.payload.value.usagePercentage` is a float.
- There are also `user`/`assistant` payloads.

**v3 `session.json`**

- `modelId` (present in 255 of 259 files), `workspacePaths`, `createdAt`, `lastModifiedAt`.

**Claude `.jsonl`**

- Top level: `type`, `timestamp`, `cwd`.
- Assistant records: `message.{model, content[], usage{input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}}`.
- Tool blocks: `tool_use{id,name,input}` and `tool_result{tool_use_id,is_error}`.

**Kiro IDE**

- No tools. `updated_at = created_at` (`data_kiro_ide.py:208`).
- 25 workspaces and 530 sessions. Loading them all through `get_sessions` takes 1.52 s.

### Scale (probed 2026-09-24, warm OS cache, separate process)

| Store / step | Files | Size | Records | Full JSON parse |
|---|---|---|---|---|
| v3 transcripts | 193 | 265 MB | 51,510 | 1.24 s |
| Claude transcripts | 48 | 304 MB | 94,006 | 1.73 s |
| In-window files | 148 | — | — | 2.17 s |

Parsing a single file:

| File | Parse time |
|---|---|
| 41 MB | 0.23 s |
| 100 MB | 0.38 s |

Plan files:

- `discover_workspaces_with_counts()` returns 66 rows and 54 distinct cwds, cached for 30 s under a lock. A cache miss costs 0.13 s.
- 23 cwds have `plans/`, with 12 non-`done/` plan files between them. A full read of all of them takes 0.029 s.

### Plans

- Nothing in `src/` reads plan files.
- **Status grammar:** `> **Status**: <State>[ — detail]` with State ∈ `Exploring | Draft | In Progress | Complete` (agent-playbook `shared/skills/qplan/TEMPLATES.md § Status Grammar`).
- **Progress tracker:** `## Progress Tracker` is a table `| # | Phase/Task | Status | Notes |`. Statuses include `Done`, `In Progress` and `Pending`. Non-numeric `#` rows occur. Example: agent-playbook `plans/260924_QDREAM_COST_AND_SIGNAL_REWORK.md`.

### Background work

- `_background_refresh()` (`web.py:709`) runs `data.refresh_stale_entries` every 30 s in a thread. It is cancelled at its `asyncio.sleep` on shutdown.
- `plans/tests/260701_POWERATLAS.md` §2.16 documents `lifespan` owning four concerns.

## 2) Goal

Add an **Overview** state to the dashboard's right panel: live session tails, active plans and 14-day usage insights. It is served by a new `overview.py` module and two loopback-only routes.

The Overview is reachable whenever no session is open, including via:

- Home;
- Escape;
- clicking the open row again;
- a user-initiated close or delete.

Entering the Overview releases the dashboard's server-side subscription, so no session stays "watched" behind it.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| D1 First-version content (Q1) | Live now + Active plans + Usage | Needs-you inbox; LLM session names | User choice. The inbox is replaced by live tails. LLM names stay on the roadmap as their own item. |
| D2 Live tail data source (Q2) | Poll transcript file tails ~2 s, all providers | Stream held sessions over `/ws/acp` | Subscribing counts as "watching" and suppresses notifications. Terminal sessions have no stream. |
| D3 Return-to-Overview (Q3) | Home button, Escape, click the open row again, user-initiated Close/Delete | Keep the closed/deleted message | User choice. A non-user close (such as the idle sweeper) keeps the message; confirmed at the assumptions checkpoint. |
| D4 Layout (Q4) | Live now → Active plans → Usage. Tiles show the last ~5 events. Filter `All` (default) / `PowerAtlas`. Max 8 tiles. Clicking a tile opens the session. | — | User-accepted mock-up. |
| D5 Usage insights (Q5, Q6) | Time by workspace (this week vs last), 14-day daily bars by provider, tool reliability, context pressure (kiro-cli only), model mix, Claude-only tokens + cache-hit ratio. No cost. | A price table | Prices go stale. Claude-only numbers are labelled. |
| D6 Stats computation (Q7) | In-memory per-file summary memo (D22), a warm pass after startup, a 14-day mtime filter. A request re-parses only files changed since the last computation, and the aggregate is reused for 30 s (D23). **Per-request bound**: one full re-parse of each transcript that changed; about 0.4 s worst case today (the 100 MB live file). | Persist to disk | No format to version. The bound is stated rather than implied to be zero. |
| D7 Plans listed (Q8) | Non-`done/` `plans/*.md` with State `In Progress`, or `Complete` (badged "Ready to close"). `stale` when In Progress and mtime > 7 days. Progress from numbered tracker rows, falling back to a `### Phase N` count, then to state only. Clicking a row expands its tracker rows. | 24 h stale; including Draft/Exploring | User choice. |
| D8 Liveness (Q9) | The rail's own rule and filters: held, or `_session_is_live`, **minus** workspaces tagged `hidden` and disabled providers (`_tag_keep`, `_enabled`). Built server-side from held sessions + the presence snapshot. Held sessions with no rail row (neutral "agent's own folder" cwd, or not yet in the store) are **included**, because PowerAtlas considers them live. Parity target: tiles = rail rows with a dot ∪ held sessions without a row. | A recency window; deriving tiles from rail rows | "Live must match PowerAtlas liveness" (Q9). Hidden workspaces stay hidden everywhere. |
| D9 Context pressure (Q10) | kiro-cli `usagePercentage` only, labelled | A Claude model→window table | A hand-kept table goes stale silently. |
| D10 Module placement and import direction | New `src/power_atlas/overview.py` holds the plan reader, usage aggregator, live candidate builder and tail parser. It never imports `web`. The rail helpers it needs (`_session_is_live`, `_acp_availability`, `_acp_status_for_held`, `_acp_row_title`, `_tag_keep`, `_enabled`) are **passed in** by the `web.py` route as a small `LiveDeps` namedtuple of callables. | Lazy `import web` inside functions; moving the helpers to a new lower module | Passing callables keeps the dependency direction downward without a refactor of `web.py` helpers. It also makes the helpers easy to fake in tests. |
| D11 Routes | `GET /api/dashboard/overview/live?filter=all\|poweratlas` (2 s poll) and `GET /api/dashboard/overview/summary` (plans + usage, 60 s poll) | One route | Different cadences. The live route stays cheap. |
| D12 Warm pass lifecycle | A dedicated `asyncio.to_thread(overview.warm_usage, stop_event)` task started in `lifespan`. `stop_event` (a `threading.Event`) is set on shutdown and checked between files. Errors are caught per file. The pass sets `usage_state` to `ready` or `error` in `finally`. When the state is `error` or `cold`, the summary route computes on demand. | Hooking `_background_refresh` | Keeps the 30 s refresh unchanged. It cannot leave Usage stuck on "loading", and it does not delay restart. |
| D13 Tile events | `overview.tail_events(path, provider, n=5)` has **its own widening loop**: 64 KiB ×8 until n events are parsed or 2 MiB is reached. It drops a leading partial line and skips lines over 256 KiB. It is memoised per path on `(mtime_ns, size)`. `OSError` gives `[]`. | Reusing `_read_tail_lines` as is; `get_full_transcript` + `translate_transcript` | `_read_tail_lines` stops at one line (0 events measured on real files). A full parse every 2 s is not viable. The memo makes idle tiles cost one stat. |
| D14 Opening a session from a descriptor | `openSessionTranscript(target, …)` and `dashMaybeAttach(target, sid)` accept a row element or `{sid, provider, cwd}`, normalised by `dashDesc()` placed **inside** the `openSessionTranscript` region. `.viewing` is applied immediately to any `[data-sid="<sid>"]` rail row. | A synthetic hidden row | Both functions read only the dataset. The helper's placement keeps the node-test region slices self-contained. |
| D15 Panel mode ownership | One mode flag: class `dash-mode-overview` on `#sessions-panel`. CSS hides `#dashTranscriptWrap`, `#dashSessionMeta` and the composer under that class, and shows `#dashOverview`. `dashTranscriptWrapEl.hidden` stays owned by the sub-agent view. Returning to the Overview restores the single `[data-empty]` placeholder into the pane. | Toggling `#dashTranscriptWrap.hidden` | The sub-agent teardown writes `hidden = false` on every socket drop, so a shared flag would reveal the pane behind the Overview. |
| D16 Test placement | New tests go in the existing `tests/test_web.py` and `tests/acp_page.test.mjs` | New `tests/test_overview.py` | AGENTS.md § Doc & Test Guidelines: no new test files unless requested. |
| D17 Checkpoint assumptions | Header label "Overview". Tiles idle > 2 min are dimmed. A non-user close keeps its message. README updated. No new external deps. | — | Surfaced at the /qexplore assumptions checkpoint. The user replied "let's go, then /qplan with 1 qreview cycle" (2026-09-24). |
| D18 Server-side detach on entering the Overview | New client frame `unsubscribe` (no payload) in `acp.py`, handled synchronously by `detach(conn)`. `dashShowOverview()` sends it when the socket is open. The socket itself stays open for later opens. | Close the dashboard socket (interacts with the reconnect/backoff logic); accept a lingering "watched" session | D2's premise: the Overview must never keep a session watched, which would suppress notifications and exempt it from the idle sweeper. **This makes Phase 1 a Python change (restart).** |
| D19 Escape handling | One **capture-phase** `keydown` listener registered before the others. It acts only when all of these hold: `_viewingSid` is set; the target is not editable; `dashRailSettingsMenu.hidden`; `_railOpenActionMenus.length === 0`; the mode menu, topbar settings and ws-filter menus are hidden; no `#dashPicker:not([hidden])` and no `.acp-ws-delete-modal` present; no `[role="dialog"]:not([hidden])`. It never checks `aria-expanded`. | The `[aria-expanded="true"]` / `dialog[open]` guard | That guard is almost always true (expanded rail groups, log toggle), so Escape would never fire. The page's dialogs are divs, not `<dialog>`. |
| D20 Click-again and locked tiles | Clicking the open row returns to the Overview only when the transcript loaded (not in the Loading or "Could not load" state). Otherwise it reloads, as today. A freshly created, unprompted dashboard session clicked again goes to the Overview and is closed by `dashCloseIfAbandoned`, the same as Home. Tiles for `locked` sessions are inert like rail rows (`aria-disabled`, tooltip "open elsewhere"). | Always toggle; open locked sessions read-only | Keeps the retry path after a failed load. Keeps rail and tile behaviour the same. |
| D21 Robust filesystem reads | Plan scan: skip cwds that are not absolute or start with `\\`, apply a 2 s per-request deadline (the `_acp_exists_flags` pattern), stat before reading, skip files > 1 MiB, read `encoding="utf-8", errors="replace"`, and catch `(OSError, UnicodeDecodeError, ValueError)` per file. Live and usage: catch errors per tile or file. Usage full parse skips lines > 8 MiB. Session ids from process cmdlines must match the existing UUID regex (`web.py`, `/api/session-transcript` validation) before any path is resolved. | Catch `OSError` only | One bad file or a dead share must not 500 the whole route or stall the executor. |
| D22 Memo shape | Keyed by **path**, storing `(mtime_ns, size, value)`, guarded by a `threading.Lock` around get/put only. A concurrent duplicate parse is tolerated. Each usage pass evicts paths no longer in the 14-day set. Tail memo and plan memo use the same shape. | Keying on `(path, mtime_ns, size)` | A key that includes mtime grows by one entry per append on live files. |
| D23 Server-side single-flight and reuse | The summary route reuses the last usage aggregate for 30 s, and plans for 30 s. The client keeps one summary request and one live request in flight at a time. | None | Home/Escape toggles and several tabs otherwise trigger repeated re-parses. |
| D24 Snapshot and acp guard | The live route captures `held` on the loop with the listing routes' `acp is None` guard (`held = {}`). `presence.get_snapshot()` is called **inside** the worker thread. `_acp_availability` receives `workspace_hashes` for the candidate cwds. | Evaluating the snapshot as a `to_thread` argument | The rescan takes 42–75 ms and would block the loop about every other poll. |
| D25 Kiro IDE daily counts | Read the Kiro IDE store's `sessions.json` per workspace directly in `overview.py` (`dateCreated` only, 14-day filter). Do not call `get_sessions`. | `data.get_sessions` for all 25 IDE workspaces | That would take 1.52 s and permanently add 530 sessions to SessionCache and the 30 s refresh. |
| D26 Rendering safety | Every overview renderer builds DOM nodes with `textContent`. Numeric-only values go through `Number()` + clamp into `el.style.height/width`. Provider colours come from a fixed class map, never from fetched strings. Each rendering phase has an adversarial node test. The `_acp_csp` docstring premise is corrected. A dashboard CSP is deferred (Follow-up). | A grep for `innerHTML =` only | The page has no CSP. Tiles render agent-authored text (tool arguments, assistant text). |

## 4) External Dependencies & Costs

### Required external changes

None. There are no new packages, services, credentials or build artifacts. The routes are loopback-only by default: they are behind the `pa_local` cookie gate and are not added to `_REMOTE_ALLOWED_PATHS`.

**Operator action:** Phases 1–4 change Python, so each needs a **user-approved PowerAtlas restart** before its live QA. Phase 1's only Python change is the `unsubscribe` frame (AGENTS.md: never restart autonomously). The owner is the user.

### Cost impact

There is no monetary cost. Local CPU and IO, measured 2026-09-24 on a warm OS cache:

- **Warm pass after startup:** about 2.2 s of JSON parsing over 148 in-window files, in a background thread. It is slower on a cold disk.
- **Live poll, every 2 s while the Overview is visible:**
  - a presence rescan roughly every 3 s (42–75 ms, in the worker thread);
  - `_session_is_live` path resolves and stats for each session in each live cwd (0–11 ms);
  - `_acp_availability` with `workspace_hashes` (≤ 10 ms for 8 ids);
  - `_acp_status_for_held` (5 s TTL cache);
  - ≤ 8 memoised tail reads (one stat each when idle; ≤ 2 MiB when changed);
  - first load of a live cwd's sessions (0.08–0.16 s, once).
- **Summary poll, every 60 s while the Overview is visible (reused for 30 s):**
  - the plan scan (29 ms, 2 s deadline);
  - a re-parse of transcripts that changed (≤ 0.4 s today).

## 5) Implementation Phases

Phases 2–4 all edit `web.py`, `overview.py` and `index.html`, so no phases run in parallel. New code comments cite the slug `260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE` rather than a bare "Phase N", because bare "Phase N" comments already exist in `index.html`, `web.py` and `style.css`.

### Phase 1: Overview shell, navigation and server-side detach [QA]
**Goal**: The right panel gets an Overview state with three placeholder sections. Every return path works. Entering the Overview releases the server-side subscription.
**Covers**: SC-1, SC-2, SC-3, SC-9
**File scope**: `src/power_atlas/acp.py`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`, `tests/acp_page.test.mjs`, `tests/test_web.py`

Changes:

1. **Server: `unsubscribe` frame (D18).**
   - Add `"unsubscribe"` to the client frame type set in `acp.py`, where the set begins `"subscribe", "new", "load", "prompt", …`.
   - Route it before `subscribe` in the dispatcher to a synchronous `_handle_unsubscribe(conn)` that calls the registry's `detach(conn)` and sends nothing back.
   - Test in `tests/test_web.py`: after `subscribe` then `unsubscribe`, `_registry.subscribers.get(sid)` no longer contains the connection, so `watched` is false. A second `unsubscribe` is a no-op.
2. **Markup.**
   - Add `<div class="dash-overview" id="dashOverview">` after `#dashSessionMeta` and before `#dashTranscriptWrap` in `section#sessions-panel`.
   - Inside it, add three `<section>`s — `#dashOvLive`, `#dashOvPlans` and `#dashOvUsage` — each with an `<h3>` and a body holding a "Loading…" line.
   - Add a Home button `#dashHome` (house icon, `aria-label="Back to overview"`) in the panel's `.section-label`, before `#dashLogToggle`.
   - Give the header "Transcript" span `id="dashPanelTitle"`.
3. **Mode (D15).**
   - `dashSetPanelMode('overview'|'pane')` toggles `dash-mode-overview` on `#sessions-panel`, sets the title text, and shows `#dashHome` only in pane mode.
   - CSS (`style.css`), under `#sessions-panel.dash-mode-overview`:
     - `#dashTranscriptWrap, #dashSessionMeta, #dashComposer, #dashPromptNav { display: none }`
     - `#dashOverview { display: flex }`
   - Outside that mode, `#dashOverview { display: none }`.
4. **`dashShowOverview()`** runs these steps in order:
   1. `dashCloseIfAbandoned()`.
   2. `if (_dashWs && _dashWs.readyState === 1 && _dashAttachedSid) send('unsubscribe')`. Match the existing `send()` helper's signature.
   3. `_dashAttachedSid = null`, then `dashUpdateCloseButton()`.
   4. The teardown trio: `dashCloseSubagentView(); dashRemoveAllCrewPanels(); dashCloseSubWs();`.
   5. Remove `.viewing` from rail rows and set `_viewingSid = null`.
   6. `dashSessionMetaRender(null)`.
   7. Restore the pane placeholder: empty `#dashTranscript`, then append a clone of the `[data-empty]` node captured once at load.
   8. `dashSetPanelMode('overview')`.
   9. `dashOverviewStart()` (a no-op stub until Phase 2).

   `dashShowPane()` calls `dashSetPanelMode('pane')` and `dashOverviewStop()`. Call `dashShowPane()` at the top of `openSessionTranscript`, `dashPickerCreate`, `dashRailQuickCreate` and `dashReportSignedOut`.
5. **Initial state.** On `DOMContentLoaded`, set the mode to `overview` directly. Nothing is attached, so the detach steps are skipped.
6. **Return paths.**
   - (a) `#dashHome` click.
   - (b) Escape per D19: one capture-phase listener, registered in the script before the existing menu listeners.
   - (c) In the rail row click handler: if `_viewingSid === session.id` **and** the pane holds a loaded transcript, call `dashShowOverview()`. "Loaded" means not a lone `.acp-system-msg` reading "Loading transcript…" or "Could not load this transcript."; track it with `_dashTranscriptLoaded`, set on fetch success and on attach (D20).
   - (d) User Close. Set `_dashUserClosedSid = sid` only after `send('close')` succeeds. In `session_closed` for that sid, call `dashShowOverview()` after the existing teardown and clear the flag. Any other `session_closed` keeps today's message. Also clear the flag in `openSessionTranscript` and `dashShowOverview`.
   - (e) Delete. In `dashRailForgetSession`, replace the "This session was deleted." branch with `dashShowOverview()`.
7. **Descriptor refactor (D14).**
   - `openSessionTranscript(target, session, workspace)` and `dashMaybeAttach(target, sid)` normalise through `dashDesc(target)`. Define `dashDesc` inside the `openSessionTranscript` region (between `function openSessionTranscript` and the `// ---- Phase 3: live-attach wiring` marker the node test slices on).
   - After setting `_viewingSid`, add `.viewing` to every `.acp-rail-row[data-sid="<sid>"]` present.
   - Sketch (delta): `function dashDesc(t){ return t && t.dataset ? {sid:t.dataset.sid, provider:t.dataset.provider||'', cwd:t.dataset.cwd||'', el:t} : t; }`
8. **Signed-out.** Define a helper `dashOverviewFetch(url)` used by the Overview. On a 403 it calls `dashReportSignedOut()` once and stops the pollers.
9. **Tests (`tests/acp_page.test.mjs`).** Update every affected region sandbox with the new free variables and element ids (`dashOverview`, `dashHome`, `dashPanelTitle`, `sessions-panel`):
   - the `openSessionTranscript` region;
   - `dashRailForgetSession`;
   - `dashHandle`;
   - the picker region (`dashPickerCreate`/`dashRailQuickCreate`);
   - the connect region (J4, `dashReportSignedOut`);
   - the close-button region;
   - the sentinel test that calls `box.dashMaybeAttach(row, "s1")`, updated to the descriptor contract.

   Extract `dashShowOverview`/`dashShowPane`/`dashSetPanelMode` as a named region with a name guard, run for real (not stubbed) in the delete and close tests. New cases:
   - deleting the viewed session leaves exactly one `.acp-system-msg[data-empty]` child and the overview mode class;
   - `session_closed` for `_dashUserClosedSid` enters the Overview, and for another sid appends the message;
   - a socket drop (`dashCloseSubagentView()`) while in overview mode keeps the overview mode class;
   - Escape with the rail settings menu open, and Escape with `#dashPicker` open, leave `_viewingSid` unchanged;
   - Escape with only rail groups expanded returns to the Overview;
   - `dashShowOverview()` sends `unsubscribe` when attached;
   - the existing J4 create-refusal and signed-out tests pass unchanged in assertions.

> **Rejected:** rendering the Overview inside `#dashTranscript`. It breaks J4's `kids.length === 1` and is wiped on every load. **Use instead:** sibling `#dashOverview` + a panel mode class (D15).
> **Rejected:** `[aria-expanded="true"]` / `dialog[open]` as the Escape guard. It is almost always true, and the page's dialogs are divs. **Use instead:** the explicit open-state predicates in D19.
> **Rejected:** toggling `#dashTranscriptWrap.hidden` for the mode. `dashCloseSubagentView()` resets it on every socket drop. **Use instead:** `dash-mode-overview` on `#sessions-panel`.

**Exit criteria**:
- [x] `node tests/acp_page.test.mjs` passes, including all new cases above. `.venv-PowerAtlas/Scripts/python -m pytest tests/ -q --timeout=300` passes, including the `unsubscribe` test.
- [x] After a **user-approved restart**, the live checks pass:
  - On page load, the panel shows "Overview" with three sections.
  - Opening a session shows the transcript and the Home button.
  - Home, Escape (focus on the page body, rail groups expanded) and clicking the loaded open row each return to the Overview, with `_viewingSid === null` and `.viewing` cleared.
- [x] Escape in the composer, in the search field, with a rail menu open, or with the picker open does **not** leave the session.
- [x] After returning to the Overview from a held session, `orchestrator.log` or a WS debug-log check shows the `unsubscribe`. A turn ending in that session afterwards produces a desktop notification (notifications enabled).
- [x] Clicking Close returns to the Overview. A sweeper or remote close keeps "This session was closed." Deleting the open session returns to the Overview.
- [x] After returning, the pane holds exactly one `.acp-system-msg[data-empty]` child.

**Implementation (2026-09-25, code: 11f444b)**

The dashboard's right panel now opens on an Overview that is always reachable again, and entering it releases the server-side subscription. Server (`src/power_atlas/acp.py`): a new `unsubscribe` client frame is listed in `CLIENT_TYPES`; `_dispatch` routes it, before `subscribe`, to a synchronous `_handle_unsubscribe(conn)` that drops the socket from any `_registry.loading` waiter list, calls `_registry.detach(conn)` (which also stamps the idle clock), sends nothing back, and logs `ACP unsubscribe: socket=… session=…` so the live-QA check can find it in `orchestrator.log`. Page (`src/power_atlas/templates/index.html`): the markup gains a sibling `#dashOverview` with three placeholder sections (`#dashOvLive`, `#dashOvPlans`, `#dashOvUsage`), a Home button `#dashHome`, and `id="dashPanelTitle"` on the header span. A new slug-marked region just before `openSessionTranscript` holds `dashSetPanelMode` (toggles `dash-mode-overview` on `#sessions-panel`, sets the title, shows Home only in pane mode), `dashShowPane`, `dashShowOverview` (abandon-close, conditional unsubscribe, attach/close-button reset, sub-agent and crew teardown, clears `.viewing`/`_viewingSid`/the metadata strip, then restores exactly one clone of the `[data-empty]` placeholder captured at load), no-op `dashOverviewStart`/`dashOverviewStop` stubs, `dashOverviewFetch` (403 reported as signed out once, pollers stopped), the Home click wiring, and a capture-phase Escape listener behind `dashEscapeMayLeave`, which checks explicit open-state predicates and never reads `aria-expanded`. The rail row click handler toggles back to the Overview when the open row is clicked again and `_dashTranscriptLoaded` is true (set on a successful fetch and on the `session` attach frame). The Close button records `_dashUserClosedSid` only after `send('close')` succeeds, and `dashHandle`'s main `session_closed` branch returns to the Overview only for that sid, so a sweeper or remote close keeps "This session was closed.". `dashRailForgetSession` now calls `dashShowOverview()` in place of the "This session was deleted." branch. `openSessionTranscript` and `dashMaybeAttach` accept a rail row or a `{sid, provider, cwd}` descriptor through `dashDesc` (inside the `openSessionTranscript` region) and apply `.viewing` to every matching rail row. `dashShowPane()` is called from `openSessionTranscript`, `dashPickerCreate`, `dashRailQuickCreate`, `dashReportSignedOut` and the created-session branch. Styles (`style.css`): under the mode class the transcript wrap, metadata strip, composer, prompt nav and sub-agent panel are hidden and the Overview is shown; the Home button has its `[hidden]` pair. Tests: `tests/test_web.py` gains `TestAcpUnsubscribe` (detach and watched=false, idempotent second unsubscribe, other watchers kept, load-waiter removal, synchronous handler); `tests/acp_page.test.mjs` runs the new region for real in `loadDashPicker` and adds checks for every return path, the Escape guard cases, the unsubscribe/close ordering, and the J4 lone-placeholder shape. Edge cases noted, not fixed: Home pressed after Close but before `session_closed` arrives drops that frame via the stale-frame guard, so the rail dot can lag until the next 60 s poll; switching sessions (rather than going to the Overview) still sends no unsubscribe, as before this plan.

**Implementation (2026-09-25, code: 8b7fe7a) — review fixes**

`src/power_atlas/templates/index.html`: the user-close flag is now dropped when the server refuses or fails the close (except `close_in_progress`) and when the socket drops, with the Close button restored at the same time. `dashShowOverview` resets the MCP indicator and the command palette. The Escape guard gains one targeted MCP-panel predicate, and the always-false `defaultPrevented` check is gone. `dashMaybeAttach`'s deferred subscribe re-checks `_viewingSid` when the socket opens. A late `session` frame arriving on the idle Overview gets an `unsubscribe`. A create refusal after Home switches back to the pane. A pending close-then-create survives the user-close return to the Overview. `src/power_atlas/acp.py`: `_handle_unsubscribe`'s docstring is corrected (waiters only; `_deliver_load` still attaches a load's initiator, with the page's stale-arrival `close` as the backstop) and it gains INFO and DEBUG log lines. `tests/acp_page.test.mjs` and `tests/test_web.py` gain the corresponding checks. Consumers of `_dashUserClosedSid`: `dashShowOverview`'s unsubscribe gate now skips only while a close is really in flight; the `session_closed` Overview return fires only for a pending or `close_in_progress` user close; `openSessionTranscript` and the Close click handler are unchanged. Consumers of the new defence-in-depth `unsubscribe`: server-side `_handle_unsubscribe` is idempotent; pending subscribe, load or create on the same socket are protected because the frame is not sent while `_viewingSid`, `_dashLoadingSid` or `_dashCreateInFlight` is set; the stale-load branch that sends `close` runs first and returns; created frames set `_viewingSid` before this point.

**Implementation (2026-09-25, code: 9c67735) — Escape closes the sub-agent view first (user decision)**

The change is in the capture-phase Escape listener in `src/power_atlas/templates/index.html`. When Escape is allowed at all, the listener now checks the sub-agent view first: if it is open, it runs `dashCloseSubagentView()` (the same function as the "‹ Back to main" button) and stops; otherwise it returns to the Overview as before. Because the new step comes after `dashEscapeMayLeave`, the same guards apply to it. Consumers: `dashCloseSubagentView()` is already safe to call at any time; the MCP panel's and menus' own Escape listeners are unaffected; `dashShowOverview()` is reached one Escape later when the view is open.

Tests: node 850 passed; pytest 2637 passed, 2 skipped.

QA (2026-09-25, live, after a restart the user granted for this plan): PASS, 15/15 checks. Load shows "Overview" with Live now, Active plans and Usage, Home hidden. Opening a row shows the transcript and Home. Home, Escape (page body focused, 3 `aria-expanded="true"` elements present) and clicking the loaded open row each return to the Overview with `_viewingSid === null`, no `.viewing` rows and exactly one `.acp-system-msg[data-empty]` child. Escape in the search field, with the rail settings menu open, with the picker open, and in the composer of a held session keeps the session. Home from a held session logged `ACP unsubscribe: socket=s1 session=sess_69d348c8…` at 10:11:58; the running turn then ended at 10:12:01 (`end_turn`) with no subscriber and notifications enabled, which is the `notify_turn_end` path. The desktop toast itself is not logged and was not observed. User Close returned to the Overview; a close sent from `/acp` kept "This session was closed." on the dashboard; deleting the open session returned to the Overview. No page errors. The test sessions were closed and deleted afterwards.

### Phase 2: Active plans [QA]
**Goal**: The Active plans section lists In Progress and not-yet-archived Complete plans across the rail-visible workspaces, with progress, `stale` and "Ready to close" badges and expandable rows.
**Covers**: SC-6, SC-10
**File scope**: `src/power_atlas/overview.py` (new), `src/power_atlas/web.py`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`, `tests/test_web.py`, `tests/acp_page.test.mjs`

**Backend.** This is a contract for `overview.py`; the implementation is the phase's own.

- **`scan_plans(workspaces, deadline_s=2.0) -> list[dict]`.**
  - `workspaces` is a list of `(cwd, name)`. The route builds it from `data.discover_workspaces_with_counts()`, de-duplicated by `data._normalize_path(cwd)`, with `name = Path(cwd).name or cwd`. It is filtered by the rail's `hidden`-tag and disabled-provider rules (the same helpers `_acp_listing` uses).
  - Robustness follows D21:
    - skip cwds that are not absolute or start with `\\`;
    - stop scanning further cwds after the deadline;
    - glob `Path(cwd)/"plans"/"*.md"` top level only (this excludes `done/`);
    - skip `ROADMAP.MD` and `CLOSED_INVESTIGATIONS.MD`;
    - skip files over 1 MiB;
    - read as UTF-8 with `errors="replace"`;
    - catch errors per file.
  - Memoise per path with the D22 shape.
- **Parsing.**
  - The first `^> \*\*Status\*\*:\s*(.+)$` line. Strip a trailing `<!-- … -->`. The state is the text before the first ` — ` or ` - `; the detail is the rest. Keep `In Progress` and `Complete` only.
  - Tracker rows are the table rows under `^## Progress Tracker`, up to the next `^## `, whose `#` cell is all digits. Status is normalised to `done | in_progress | pending | other`.
  - The phase count is the number of distinct `^### Phase (\d+)`.
- **Per-plan output.**

  | Field | Content |
  |---|---|
  | `cwd`, `workspace` | Workspace path and display name |
  | `file` | Plan file name |
  | `title` | File name without `.md` and without a leading `\d{6}_`, with `_` replaced by a space |
  | `state` | `In Progress` or `Complete` |
  | `detail` | Status detail, ≤ 140 chars |
  | `mtime` | ISO timestamp |
  | `stale` | In Progress and mtime older than 7 days |
  | `ready_to_close` | State is Complete |
  | `progress` | `{"done", "total", "current"}`, or `{"phases": k}`, or `null` |
  | `tracker` | `[{id, name, status, notes≤120}]` |

  Sort In Progress first, then Complete; within each, by mtime descending.
- **Route.** `_DASHBOARD_OVERVIEW_SUMMARY_PATH = "/api/dashboard/overview/summary"`.
  - Runs in a thread and returns `{"plans": [...], "usage": None, "usage_state": "cold"}` with `Cache-Control: no-store`.
  - Plans are reused for 30 s (D23).
  - The route is **not** added to `_REMOTE_ALLOWED_PATHS`.
- **Frontend.**
  - `dashOverviewRefreshSummary()` runs through `dashOverviewFetch`, with one request in flight. It fires on entering the Overview and every 60 s while the Overview and the tab are visible.
  - Rows are built with DOM nodes only (D26):
    - a workspace chip, the title, a state badge, a `stale` badge;
    - a progress bar with the width set via `Number()` clamped to 0–100;
    - "phase N in progress" or "N phases";
    - the detail and a relative mtime.
  - Clicking a row toggles its tracker rows.
  - The empty state reads "No active plans".
- **Tests.**
  - `tests/test_web.py` `TestOverviewPlans`, over `tmp_path` workspaces:
    - a tracker with a non-numeric `U` row;
    - no tracker, only `### Phase` headings;
    - no Status line (skipped);
    - Draft (skipped);
    - Complete gives `ready_to_close`;
    - an 8-day-old mtime gives `stale`;
    - a `done/` subfolder is ignored;
    - a non-UTF-8 file and a > 1 MiB file are skipped, and the route still returns 200;
    - a `\\server\share` cwd is skipped;
    - a hidden-tagged workspace is excluded;
    - memo: the first call reads ≥ 1 file and the second reads 0 (count via a wrapper around the module's own read function), and touching one file re-reads exactly that file.
  - Route tests use the `anonymous_client` fixture (loopback peer):
    - 403 without the cookie;
    - 200 with it;
    - 403 from a remote peer (`_peer_http`);
    - the path absent from `_REMOTE_ALLOWED_PATHS`.
  - `tests/acp_page.test.mjs`: an adversarial render test feeding `<img src=x onerror=…>` as title, detail and tracker notes, asserting no element or attribute is created from it.

**Exit criteria**:
- [x] `pytest tests/test_web.py -k OverviewPlans --timeout=300` and `node tests/acp_page.test.mjs` pass.
- [x] After a **user-approved restart**, the live Overview lists exactly the In Progress and Complete non-`done/` plans in rail-visible workspaces, comparing against a script that globs the same folders. It includes agent-playbook's `260924_QDREAM_COST_AND_SIGNAL_REWORK` with a `done/total` taken from its current tracker.
- [x] Expanding a row shows its tracker rows. `stale` appears only on In Progress plans unchanged for more than 7 days.

**Implementation (2026-09-25, code: d198d70)**

Commit d198d70 fills the Overview's Active plans section. The new `src/power_atlas/overview.py` (no `web` import) provides `scan_plans(workspaces, deadline_s=2.0)`, which reads only the top-level `plans/*.md` files of each `(cwd, name)` workspace (excluding `done/`) and skips `ROADMAP.md` and `CLOSED_INVESTIGATIONS.md`, and `parse_plan`, which reads the first Status line, keeps only `In Progress` and `Complete`, takes the detail after the first ` — ` or ` - `, collects the numbered `## Progress Tracker` rows with status normalised to done, in_progress, pending or other, and otherwise counts the distinct `### Phase N` headings. Plans are sorted In Progress first, newest first. Reads follow D21 (UNC and relative cwds rejected by a string check before any filesystem call, a between-cwd deadline, files over 1 MiB skipped, `errors="replace"`, per-file error isolation), and parsed files are memoised per path on `(mtime_ns, size)` with eviction of paths not seen by a complete scan (D22). In `src/power_atlas/web.py`, `_overview_workspaces()` applies the rail's filters (available and enabled providers, de-duplication by `data._normalize_path`, the hidden tag), `_overview_summary()` reuses the scan for 30 s (D23), and `api_dashboard_overview_summary` serves `GET /api/dashboard/overview/summary` from a thread with `Cache-Control: no-store`, returning `usage: None, usage_state: "cold"`; the route is not in `_REMOTE_ALLOWED_PATHS`. In `index.html`, `dashOverviewStart`/`dashOverviewStop` drive `dashOverviewRefreshSummary` (one request in flight, through `dashOverviewFetch`), `dashOverviewPoll` runs from the rail's existing 60 s poll and visibilitychange handler, and `dashOvRenderPlans`/`dashOvPlanRow` build DOM with `textContent` only, a clamped numeric bar width and tracker classes from a fixed map; rows with tracker rows expand in place, and the empty state reads "No active plans". `style.css` adds the row, badge, bar and tracker styles with the `[hidden]` pair. Tests: `TestOverviewPlans` in `tests/test_web.py`, and seven "dashboard overview plans" checks (including the adversarial `<img onerror>` render test) in `tests/acp_page.test.mjs`.

**Implementation (2026-09-25, code: 0c87284) — review fixes**

The Status parser no longer runs a backtracking regex over unbounded text: the value is cut to 500 characters and a trailing comment is stripped with `rstrip`/`endswith`/`rfind`. The scan checks its deadline before every file, skips a `plans` folder whose `realpath` is a network share (`_resolves_to_unc`, which treats `\\?\C:` as local), uses `lstat` and never follows symlinks, and caps the read in bytes. The tracker vocabulary now covers "not started" (pending) and "implemented…"/"review pending" (in progress), and an en dash is accepted as the Status separator. `_overview_summary` holds `_overview_plans_lock` across check, scan and store, so concurrent cold requests share one scan. `progress.current` gained a `state` field: "in_progress" for a real in-progress row, "next" for the first row not yet done (null when all are done). Its only reader, `dashOvPlanRow`, shows "Phase N in progress" only for "in_progress" and "Next: phase N" otherwise. The workspace chip's `title` holds the full cwd, and `_dashOvPlanOpen` is pruned to the rendered keys. New tests cover each fix; the fixes for the regex, the single-flight, the 30 s expiry, the "next" fallback and the stray-byte read were mutation-verified.

Tests: node 860 passed; pytest 2675 passed, 2 skipped.

QA (2026-09-25, live, after a restart the user granted for this plan): PASS, 9/9 checks. The summary route answers 403 without the cookie and 200 with `Cache-Control: no-store`. The listed plans equal an independent glob of `plans/*.md` Status lines over the rail's de-duplicated, non-hidden workspaces: 7 plans, none missing, none extra. `stale` appears only on the two In Progress meeting_transcriber plans (8 and 9 days old), and "Ready to close" only on the two Complete plans. agent-playbook's `260924_QDREAM_COST_AND_SIGNAL_REWORK` shows 9/13, matching its tracker, with "Next: phase 10". The UI renders one row per plan; expanding the QDREAM row shows its 13 tracker rows and collapsing hides them. No page errors.

### Phase 3: Live now tiles [QA]
**Goal**: Up to 8 live-session tiles with the rail's liveness and filters, 2 s tails, the All/PowerAtlas filter, and click-to-open.
**Covers**: SC-4, SC-5, SC-10
**File scope**: `src/power_atlas/overview.py`, `src/power_atlas/presence.py`, `src/power_atlas/web.py`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`, `tests/test_web.py`, `tests/acp_page.test.mjs`

**Backend.**

- **`presence.Snapshot` accessors.** Add two methods, with no constructor change (see the `plans/CLOSED_INVESTIGATIONS.md` warning about positional construction):
  - `live_sids(self) -> list[tuple[str, str, str]]`: `(provider, sid, normalized_cwd)` for each `_live_sids` entry with a `_sid_to_cwd` value. Entries without a cwd are skipped.
  - `live_cwd_pairs(self) -> list[tuple[str, str]]`: the `_live_cwds` pairs.
- **`overview.live_sessions(held: dict[str, str], snapshot, filter_: str, deps: LiveDeps, originals: dict[str, str]) -> list[dict]`.**
  - Runs in a worker thread.
  - `originals` maps normalised cwd to the discovered original spelling. It is built in the thread from `discover_workspaces_with_counts()`, and every normalised cwd passes through it **before** `data.get_sessions` is called. Unknown cwds are skipped.
  - Candidates, de-duplicated by `(provider, sid)`:
    - (a) held sids (provider `kiro-cli-v3`, with their cwd);
    - (b) `snapshot.live_sids()`, with the sid validated against the UUID regex;
    - (c) for each `(provider, cwd)` in `snapshot.live_cwd_pairs()`, the `Session`s from `data.get_sessions(originals[cwd], provider)` for which `deps.session_is_live` is true.
  - Filters: keep held or live, and drop hidden-tagged workspaces and disabled providers through `deps`.
  - A held sid not found in the store gets a minimal record titled "New session", with `cwd` from `held`.
  - `availability` is `deps.acp_availability(sids, held_set, workspace_hashes)`. `status` is `deps.acp_status_for_held` for held sessions. Both are computed for the ≤ 8 tiles left after truncation, not for every candidate.
  - `filter_ == "poweratlas"` keeps held sessions only.
  - `last_activity` is the transcript mtime, falling back to `updated_at`. Sort descending and truncate to 8.
  - Each tile carries: `id, provider, title, cwd, name, created_at, updated_at, availability, status, live, last_activity, events`.
  - `events` comes from `tail_events` (D13):
    - v3: `assistant` becomes text; `tool_call` becomes tool (`toolName` + `title`, ≤ 80 chars); `tool_result` becomes result (`ok = success`).
    - Claude: assistant text blocks become text; `tool_use` becomes tool (name + the first string `input` value, ≤ 80 chars); a user `tool_result` becomes result (`ok = not is_error`).
    - User prompts become text with role `user`.
    - Text is cut to 240 chars.
    - Parse failures log the path only, never line content.
- **Route.** `GET /api/dashboard/overview/live`, constant `_DASHBOARD_OVERVIEW_LIVE_PATH`, with `filter_: str = Query("all", alias="filter")`.
  - Values outside `{"all", "poweratlas"}` fall back to `"all"` (the `project_sort` precedent).
  - On the loop: `sup = getattr(acp, "_supervisor", None) if acp is not None else None; held = {sid: m.get("cwd", "") for sid, m in (sup.sessions.items() if sup else [])}`.
  - Then `await asyncio.to_thread(_overview_live, held, filter_)`, where `_overview_live` calls `presence.get_snapshot()` and builds `originals` and `LiveDeps` inside the thread.
  - `no-store`. Not remote-allowed.

**Frontend.**

- **Poller.** `dashOverviewStart()` / `dashOverviewStop()` own a 2 s `setTimeout` loop that fetches only when in overview mode and `document.visibilityState === 'visible'`.
  - One request in flight; a response arriving after stop is dropped via a generation counter.
  - Errors back off to 10 s. A 403 is handled by `dashOverviewFetch`.
- **Filter.** Two toggle buttons in the Live heading, stored in `localStorage` `pa_dash_ov_filter` with try/catch.
- **Tiles.** A 2-column grid, keyed by sid for reuse, built with DOM nodes only (D26). Each tile shows:
  - a status dot via `dashRailDotClass(tile)`;
  - a provider icon (`/api/launcher-icon/provider--<encoded>`);
  - the title and workspace;
  - relative `last_activity`;
  - events, with `›` for tools and ✓/✗ for results, under a top fade.
  - `.idle` (dimmed, "idle 12m") when the last activity is more than 2 min ago.
  - `locked` tiles are inert (D20).
- **Click.** `openSessionTranscript({sid, provider, cwd}, tile, {cwd, name})`.
- **Empty state.** "No live sessions".

**Tests.**

- `tests/test_web.py` `TestOverviewLive`, using a fake `LiveDeps`, a fake snapshot and fixture stores. Assert **equality** with the expected set for each case:
  - a held session in a neutral cwd;
  - a sid on a cmdline;
  - a live cwd in an unloaded workspace;
  - an old session sharing a live cwd, excluded;
  - a hidden-tagged workspace, excluded;
  - a disabled provider, excluded;
  - a non-UUID cmdline sid, excluded.
- Further `TestOverviewLive` cases:
  - original-case cwd preserved (the `SessionCache` original is not overwritten with the casefolded form);
  - the filter;
  - the cap of 8, and ordering;
  - `acp = None` gives `held = {}` and a 200;
  - a deleted transcript gives `events == []` and no 500.
- `tail_events` cases:
  - v3 and Claude fixtures;
  - fewer than 5 events in the first 64 KiB window, so it widens;
  - a single line over 256 KiB is skipped;
  - a partial first line is dropped;
  - a memo hit costs no read.
- Route tests with `anonymous_client`:
  - 403 without the cookie, 200 with it;
  - a remote peer gets 403;
  - absent from `_REMOTE_ALLOWED_PATHS`;
  - `?filter=poweratlas` binds.
- `tests/acp_page.test.mjs`:
  - no fetch while the mode is not overview or the document is hidden;
  - a late response after stop is dropped;
  - an adversarial title, event text and tool name create no element or attribute;
  - a locked tile is not clickable.

> **Rejected:** `get_full_transcript` + `translate_transcript` for tiles. That is a whole-file parse (up to 100 MB) every 2 s. **Use instead:** the memoised widening `tail_events` (D13).
> **Rejected:** `/ws/acp` `subscribe` per tile. It marks sessions watched. **Use instead:** the file-tail poll (D2).
> **Rejected:** `presence.get_snapshot()` as a `to_thread` argument. It runs a 42–75 ms rescan on the loop. **Use instead:** call it inside the worker (D24).

**Exit criteria**:
- [x] `pytest tests/test_web.py -k OverviewLive --timeout=300` and `node tests/acp_page.test.mjs` pass.
- [x] After a **user-approved restart**, the set of tile sids equals the rail rows with a status dot (all workspaces expanded, or compared by script against `/api/dashboard/sessions` rows with `availability=='held' or live`) plus held sessions without a rail row. Any difference is explained by D8.
- [x] A live Claude Code terminal session in a collapsed, unloaded workspace appears as a tile, and its `name` keeps its original case.
- [x] While a live session works, its tile shows ≥ 1 event and updates within about 3 s. Clicking the tile opens its transcript and marks its rail row `.viewing` immediately. Home returns.
- [x] With the Overview or the tab hidden, the Network log shows no `/overview/live` requests. Tiles never send `subscribe` (WS debug log).

**Implementation (2026-09-25, code: 8952918)**

Commit 8952918 fills the dashboard Overview's Live now section. In `src/power_atlas/presence.py`, `Snapshot` gains two accessors, `live_sids()` (provider, sid, normalized cwd) and `live_cwd_pairs()`, with no constructor change. In `src/power_atlas/overview.py`, which still never imports `web`, a `LiveDeps` NamedTuple carries the rail helpers that web.py passes in. `live_sessions(held, snapshot, filter_, deps, originals)` builds its candidates from three sources: held sessions; cmdline or sidecar ids, checked against `SESSION_ID_RE` (the same `(?:sess_)?UUID` pattern `/api/session-transcript` uses) before any lookup; and sessions in live cwds that pass the rail's `_session_is_live`. It drops hidden workspaces and providers the rail does not show. Every snapshot cwd goes through the discovered original spelling before `data.get_sessions`, and undiscovered cwds are never loaded. A held session with no store record becomes "New session". Tiles are sorted by transcript mtime, falling back to `updated_at`, and capped at 8. Availability (with v3 workspace hashes) and held status are computed only for the shown tiles. `tail_events(path, provider, n=5)` has its own widening loop, 64 KiB ×8 up to 2 MiB. It drops a leading partial line, skips lines over 256 KiB, turns v3 and Claude records into text, tool (≤80 chars) and result events (text ≤240 chars), logs only the path on failure, and is memoised per path on (mtime_ns, size). In `src/power_atlas/web.py`, `GET /api/dashboard/overview/live` (`Query(alias="filter")`, unknown values fall back to `all`, no-store, not in `_REMOTE_ALLOWED_PATHS`) captures `held` on the loop with the `acp is None` guard. `_overview_live` takes the presence snapshot inside the worker thread and builds `originals` and `LiveDeps` there. The rail filters were factored into `_overview_rail_filters()`, shared with Phase 2's `_overview_workspaces()`. In `src/power_atlas/templates/index.html`, inside the Overview region, `dashOverviewStart/Stop` now drive a 2 s poller with these rules: one request in flight, a generation counter that drops late responses, a 10 s backoff after errors, and a fetch only when the panel is in overview mode and the tab is visible. The same region adds the All/PowerAtlas filter, stored in `localStorage` under `pa_dash_ov_filter` inside try/catch. Tiles are built from DOM nodes only and reused per sid. Each shows a `dashRailDotClass` dot, a provider icon for a fixed set of providers only, the title, workspace and age, `idle Nm` dimming after 2 min, and the events (`›` for tools, ✓/✗ for results). Clicking a tile calls `openSessionTranscript({sid, provider, cwd}, tile, {cwd, name})`. Locked tiles are disabled and inert. With no live sessions the section reads "No live sessions". Styles are in `src/power_atlas/static/style.css`; none of the new elements is toggled with `hidden`. Tests are `TestOverviewLive` in `tests/test_web.py` and nine "dashboard overview live" checks in `tests/acp_page.test.mjs`.

**Implementation (2026-09-25, code: 9bbf18b) — review fixes**

Commit 9bbf18b applies all 13 review fixes to the Phase 3 live tiles. **Server side.** One bad transcript line now costs only that line, and the result is still memoised. The tail memo is a path-keyed LRU of 32 entries (`4 * LIVE_MAX_TILES`), so tabs with different filters no longer evict each other. Each widening step parses only the newly read bytes, and a line cut at the window edge is parsed whole on the next step. `tail_events` reuses the stat that `live_sessions` already took. Held sessions get no workspace-hash lookup; a v3 hash comes from the transcript path, otherwise there is one lookup per cwd per poll. The live route reuses the rail filters for 5 s. web.py validates session ids with `overview.SESSION_ID_RE` instead of its own copies. A held session with no transcript sorts first. **Client side.** One persistent tile grid, reordered without moving the focused tile; a tile is rebuilt only when its data changed. Each poll has its own `AbortController`, aborted on halt and after a 15 s timeout. **Tests.** New tests pin D8's hidden-workspace rule on the held path and the cmdline path, and the CRLF-fragile node slice is fixed. Consumers checked: `_tail_memo` (only `tail_events` and tests); `workspace_hashes` (read only by `_acp_availability`/`_lock_holder_v3`, which validate the name and fall back to a full scan, so the answer cannot change); `dashOverviewFetch` (the summary refresh still passes one argument); `tail_events` (only caller `live_sessions`); tile ordering (the body still holds a single child); `_overview_rail_filters` (the summary route still calls it uncached).

Tests: node 871 passed; pytest 2706 passed, 2 skipped.

QA (2026-09-25, live, after a restart the user granted for this plan): PASS, 20/20 checks (one measurement corrected below). The live route answers 403 without the cookie. **Parity:** the tile set equals the set of rail rows with a dot, collected by script from `/api/dashboard/sessions` across every group page and session page, including collapsed groups: 6 tiles, 6 rail rows, no difference either way. It includes a kiro-cli terminal session in the collapsed "Aruba" workspace (a locked tile), and a live Claude Code terminal session in the collapsed, unloaded `meeting_transcriber` workspace whose `name` keeps its original case. Every tile showed 5 events. The UI rendered 6 tiles and polled 2 times in 4.2 s; keyboard focus on a tile survived re-renders. Clicking a tile opened its transcript; clicking this session's tile marked both of its rendered rail rows `.viewing` immediately; Home returned. No `/overview/live` requests were made in 4.5 s with the transcript open, nor in 4.5 s with the tab hidden. No `subscribe` frame was sent by the tiles. **Held session:** a session created and prompted from `/acp` appeared as a "New session" tile in PowerAtlas; the PowerAtlas filter showed exactly that session. The tile showed a new prompt about 0.4 s after kiro-cli wrote its `user` record (13:13:33.98 written, tile updated about 13:13:34.4). The QA script's own "within 3 s of turn end" check reported 10.4 s because it matched the prompt text, not the reply, against the previous turn's end; the file timestamps above are the corrected measurement. The test session was closed and its directory deleted. No page errors.

### Phase 4: Usage insights [QA]
**Goal**: The 14-day Usage section per D5.
**Covers**: SC-7, SC-8
**File scope**: `src/power_atlas/overview.py`, `src/power_atlas/web.py`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`, `tests/test_web.py`, `tests/acp_page.test.mjs`, `docs/KNOWLEDGE.md`

**Backend.** This is a contract for `overview.py`.

- **File discovery.** Use existing constants, not hard-coded home paths.
  - v3: `status_classifier._V3_SESSIONS_ROOT/*/sess_*/messages.jsonl`, plus the sibling `session.json` (`workspacePaths[0]`, `modelId`).
  - Claude: `data_claude.CLAUDE_PROJECTS_DIR/*/*.jsonl`, bare-UUID stems only (mirror `data_claude._is_session_file`). The cwd comes from the record `cwd` field.
  - Kiro IDE: sessions per day only, read directly from its store (D25).
  - Only files whose mtime falls within the last 14 days.
  - Hidden-tagged workspaces and disabled providers are excluded from per-workspace rows and totals.
- **`summarize_file(path, provider) -> FileSummary`.** Memoised with the D22 shape. It skips lines over 8 MiB and catches errors per file.
  - `days`: `{YYYY-MM-DD local: {"active": bool, "agent_seconds": float}}`.
  - `agent_seconds`:
    - v3: the sum of `usage_summary.elapsedTime`, bucketed by that record's timestamp. **First verify the unit.** Compare several `elapsedTime` values against their `turn_start` → `usage_summary` timestamp deltas, pick ms or s, and record the finding in `docs/KNOWLEDGE.md` and §9.
    - Claude: per turn, from a user prompt record (string content, not a `tool_result`) to the last assistant record before the next prompt, capped at 30 min.
  - `tools`: `{name: {"calls", "failed"}}`, joined by `toolCallId` (v3) or `tool_use_id` (Claude).
  - `context_peak`: v3 max `usagePercentage`, else `None`.
  - `model`: the v3 `modelId`, or the most frequent Claude `message.model`.
  - `tokens` (Claude): `{input, output, cache_read, cache_creation}`.
  - `cwd`, `provider`, `session_id`.
- **`usage_summary(now=None) -> dict`.**

  | Key | Content |
  |---|---|
  | `by_workspace` | top 8 `{cwd, name, this_week_s, last_week_s}` |
  | `daily` | 14 × `{date, sessions: {provider: n}, agent_s: {provider: s}}` |
  | `tools` | `{top: [{name, calls, fail_rate}] ×8, failing: [{name, failed, calls}] ×5 (this week, ≥ 3 calls)}` |
  | `context_pressure` | `{sessions_over_80, sessions_total, top: [{session_id, cwd, peak}] ×5}`, kiro-cli only |
  | `models` | `[{model, sessions}]` |
  | `claude_tokens` | `{input, output, cache_read, cache_creation, cache_hit_ratio}` |

  Also add `aggregate_age_s` and the count of re-parsed files for tests. Each pass evicts memo paths that fell out of the window.
- **Warm pass (D12).** `warm_usage(stop_event)` is started in `lifespan` after the refresh task. On shutdown, set `stop_event`, then cancel. Maintain `usage_state` as `cold | warming | ready | error`.
- **Summary route.** Returns `usage` when `ready`. When the state is `error` or `cold`, it computes on demand in the thread. While `warming`, it returns `usage: None`. The aggregate is reused for 30 s (D23).

**Frontend.** `#dashOvUsage`, CSS-only, DOM nodes only (D26):

- a "This week" list: workspace, agent time, ▲/▼ versus last week;
- 14 daily stacked bars, with heights from `Number()`-clamped values and provider segments coloured from a fixed class map;
- a tool reliability table ×2;
- a context-pressure line labelled "kiro-cli only";
- model chips;
- a Claude tokens line labelled "Claude Code only";
- a loading skeleton while `usage` is null;
- the note "Claude agent time is estimated from message timestamps".

**Tests.**

- `tests/test_web.py` `TestOverviewUsage`, with fixture JSONL under `tmp_path` and the store constants monkeypatched:
  - the v3 `elapsedTime` bucket;
  - a Claude turn with the 30 min cap;
  - the tool join and failure rate;
  - the context peak;
  - tokens and the cache-hit ratio;
  - the 14-day filter;
  - a hidden workspace excluded.
- Memo behaviour:
  - the second call re-parses 0 files;
  - appending to one file re-parses exactly that file;
  - the memo does not grow with appends;
  - an out-of-window path is evicted.
- Warm pass:
  - a failure (one file raising) sets `ready` with that file skipped;
  - a warm pass raising overall gives `error`, and the route computes on demand;
  - `stop_event` set mid-pass returns promptly.
- Route: `usage` is null while `warming`.
- `tests/acp_page.test.mjs`: an adversarial model name and workspace name render as text, and bar heights are numeric only.

**Exit criteria**:
- [x] `pytest tests/test_web.py -k OverviewUsage --timeout=300` and `node tests/acp_page.test.mjs` pass.
- [x] The `elapsedTime` unit is verified against real turn timestamps and recorded in `docs/KNOWLEDGE.md` (kiro-cli v3 store section) and §9.
- [x] After a **user-approved restart**:
  - the section shows loading, then real data within about 10 s;
  - this-week totals for PowerAtlas and agent-playbook are non-zero;
  - the daily bars cover 14 days.
- [x] Two consecutive summary requests with no transcript changes report `reparsed == 0` on the second (via the test-visible counter, not wall time).
- [x] Context pressure and tokens carry their "kiro-cli only" / "Claude Code only" labels.

**Implementation (2026-09-25, code: 0eec979)**

Commit 0eec979 (on 1032dea, 7 files, no attribution) fills the dashboard Overview's Usage section. **Backend, `src/power_atlas/overview.py`.** `summarize_file` / `_parse_usage_file` parse one kiro-cli v3 `messages.jsonl` (plus the `session.json` beside it) or one Claude Code transcript into per-local-day buckets: activity, agent seconds, tool calls and failures, and Claude tokens. The results are memoised per path on `(mtime_ns, size)` in `_usage_memo`. Lines over 8 MiB are skipped, and one bad line or file costs only itself. `usage_summary` builds the 14-day aggregate (`by_workspace`, `daily`, `tools`, `context_pressure`, `models`, `claude_tokens`, `reparsed`, `aggregate_age_s`), applies the rail's hidden-tag and provider filters, re-parses only changed files and evicts paths that left the window. Kiro IDE daily counts come from its `sessions.json` files read directly (D25). `warm_usage(stop_event)` is the startup pass. `usage_payload(filters)` gives the summary route `(usage, usage_state)`: `None` while warming, otherwise a 30 s-reused, single-flight aggregate. **`src/power_atlas/web.py`.** `lifespan` starts the warm pass as its own `asyncio.to_thread` task, and on shutdown sets its `threading.Event` before cancelling and gathering it. `_overview_summary` returns real `usage` and `usage_state`. **Frontend.** `dashOvRenderUsage` draws the "This week" list with ▲/▼ against last week, stacked daily bars for agent time and for sessions (heights from `dashOvPct`, provider colours from the fixed `DASH_OV_USAGE_PROVIDERS` class map), two tool tables, a context line labelled "kiro-cli only", model chips, a tokens line labelled "Claude Code only", the estimate note, and a skeleton while warming with a 3 s re-fetch, using DOM nodes and `textContent` only. **Tests and docs.** `tests/test_web.py` gains `TestOverviewUsage` and a usage seam in the autouse fixture; `tests/acp_page.test.mjs` gains 6 checks; `docs/KNOWLEDGE.md` gains "Transcript usage records — kiro-cli v3 and Claude Code" with the verified unit and the record shapes.

**Implementation (2026-09-28, code: 28f5fdc) — review fixes**

Commit 28f5fdc applies the 14 Phase 4 review fixes. The kiro-cli context peak is kept per day, so only in-window records count; it is compared against 80% unrounded and rounded for display only. `isCompactSummary` records no longer start a Claude turn. Claude Code sub-agent transcripts under `<project>/<uuid>/subagents/` now add to Claude tokens and tool reliability, and to nothing else. `sessions_total` counts every in-window kiro-cli session. `usage_payload` never parses the whole window on the request thread: from `cold` it starts one stoppable background pass and returns `warming`; from `error` it starts one and returns `error`; from `ready` it reuses the aggregate for 30 s, keyed on the rail's filters. One module-level stop event covers every pass, and `lifespan` sets `warming` before it creates the warm task. Consumers checked: `usage_state` (read only by `dashOverviewRefreshSummary` and `dashOvRenderUsage`), `sessions_total` (only `dashOvUsageContext`), the reuse key and `_usage_cache` (internal), `hidden.key` (an added attribute the other unpackers ignore); the route payload's keys are unchanged.

**Implementation (2026-09-28, code: c700d20, 6c19d02) — two-stage pass (user decision)**

The warm pass runs in two stages. Stage 1 refreshes without Claude sub-agent transcripts and evicts nothing, then sets `_usage_stage1`; stage 2 is the full refresh. While the state is "warming" and the flag is set, `usage_payload` computes `usage_summary(partial=True)` from the memo under the single-flight lock, never caches it, and returns it with "warming"; a partial aggregate carries `partial: true`, a complete one `partial: false`. The client re-fetches every 3 s whenever `usage_state === 'warming'`, under the same Overview-active and visible-tab gates, and shows "Still counting sub-agent transcripts…" under Tool reliability and Tokens while partial ("Sub-agent transcripts are not counted yet." if the pass fails or a re-fetch fails). 6c19d02 corrects a cost sum in `docs/KNOWLEDGE.md`.

**Implementation (2026-09-28, code: 9f2deb6, 56713da) — usage parse in a child process**

The Usage warm pass parses its memo misses in one child process, `overview._UsageWorker`, started with the venv's base interpreter and `-c` (`CREATE_NO_WINDOW`), so the child never imports `power_atlas.__main__` or `web` and the parse no longer competes for the GIL with request threads. The child receives main transcripts as stage 0 and sub-agent transcripts as stage 1 and streams summaries back into `_usage_memo`; after each stage `warm_usage` runs its unchanged `_refresh` over memo hits, so stage-1 publishing, the partial aggregate, reuse, filters, eviction and the payload are unchanged. A stop is checked every 0.1 s and kills and reaps the child, leaving the state `cold` without evicting; the child exits when its stdin reaches end of file, which also covers the parent dying; a child that fails to start or dies falls back to the in-thread parse. The Claude reader also skips, once `cwd` is known, lines holding neither `"user"` nor `"assistant"`. 56713da publishes the final state before reaping the child and closes stdout from the reader thread.

Tests: node 880 passed; pytest 2741 passed, 3 skipped.

QA (2026-09-28, live, after restarts the user granted for this plan): PASS, 9/9 checks on the final run. The first run (single-stage pass, 28f5fdc) rendered Usage about 15 s after "Server ready"; the retry (two-stage pass, c700d20) rendered partial data at 19.0 s and complete data at 25.9 s, which the user chose to fix now. After 56713da, partial Usage rendered 5.9 s and complete Usage 9.0 s after "Server ready", with no usage-worker warning in `orchestrator.log` and no worker process left running. This-week agent time: PowerAtlas 26.4 h, agent-playbook 13.3 h. The daily bars cover 2026-09-15 to 2026-09-28 (14 days). A second summary request reparsed 0 files. The "kiro-cli only" and "Claude Code only" labels and the estimate note are shown. No page errors.

### Phase 5: Docs, roadmap and full QA
**Goal**: The documentation reflects the Overview, the roadmap is updated, and full-suite and live verification are done.
**Covers**: SC-9
**File scope**: `README.md`, `plans/ROADMAP.md`, `AGENTS.md`, `plans/tests/260701_POWERATLAS.md`, `src/power_atlas/web.py` (docstring only)

Changes:

- **`README.md`.** In the dashboard bullet starting "Click any session in the dashboard's workspaces rail", describe the Overview shown when no session is open:
  - live tails, active plans, 14-day usage;
  - the Home / Escape return paths;
  - loopback-only;
  - that Claude agent time is estimated and that context pressure is kiro-cli only.
- **`plans/ROADMAP.md`.**
  - Remove "Plan progress overlay" and "kiro-cli usage stats" from both the summary list and § Workspace Intelligence. Note that they shipped as the Overview's Active plans and Usage sections, not as a rail-row overlay.
  - Reword the "stale /qdev" leads and the sub-bullet to "partly addressed by the Overview's plan `stale` badge (plan file unchanged > 7 days); the session-level > 24 h marker heuristic remains open".
  - Keep the terminal-notification and fresh-terminal sub-bullets, and **LLM-generated session name alias**.
  - Drop "usage stats · plan-progress overlay" from both "Parked" lists: § Platform, and "Parked, deliberately" in § Where to start.
- **`AGENTS.md`.**
  - In the "ACP UI iteration" bullet, add `overview.py` and `presence.py` to the restart-required list, and `#dashOverview` markup/CSS to the reload-only list.
  - In § Verification Setup, "Dashboard attach": note that `/` now lands on the Overview, and that clicking the already-open row returns to the Overview, so a QA script must not double-click.
- **`plans/tests/260701_POWERATLAS.md`.**
  - §2.16: `lifespan` gains a fifth concern, `warm_usage`, with a probe that it neither blocks the loop nor delays shutdown.
  - §2.1: the dashboard's no-session state is the Overview.
  - Add briefs for `/api/dashboard/overview/live` and `/api/dashboard/overview/summary`.
- **`web.py`.** Correct the `_acp_csp` docstring premise: the dashboard now renders agent-authored text in tiles.

**Exit criteria**:
- [x] `README.md` updated with the Overview description and its caveats.
- [x] `plans/ROADMAP.md`:
  - Workspace Intelligence reflects the shipped items;
  - both Parked lists are updated;
  - the "stale /qdev" wording reads "partly addressed";
  - LLM session names are still present.
- [x] `AGENTS.md` "ACP UI iteration" names `overview.py` and `presence.py` as restart-required, and § Verification Setup covers the Overview landing and click-again.
- [x] `plans/tests/260701_POWERATLAS.md` §2.16, §2.1 and the new route briefs are updated.
- [x] The `_acp_csp` docstring is corrected.
- [x] `node tests/acp_page.test.mjs` and `.venv-PowerAtlas/Scripts/python -m pytest tests/ -q --timeout=300` pass.
- [ ] A live QA pass over SC-1…SC-10 against the running instance (the Playwright recipe in AGENTS.md § Verification Setup).

**Implementation (2026-09-28, code: 23e3965)**

Commit 23e3965 documents the dashboard Overview as it was built, not as first planned. `README.md` gains a bullet after the dashboard panel bullet covering the three sections, their refresh cadences and filters, the return paths, the caveats (Claude agent time estimated, context pressure kiro-cli only, tokens Claude Code only, no cost estimate), and that the Overview never subscribes to a session and is loopback-only. `plans/ROADMAP.md` removes "Plan progress overlay" and "kiro-cli usage stats" from the summary list and replaces them in § Workspace Intelligence with a note that they shipped as the Overview's Active plans and Usage sections; "stale /qdev" reads "partly addressed by the Overview's plan `stale` badge", with the session-level > 24 h heuristic still open; "usage stats · plan-progress overlay" is removed from both Parked lists; LLM session names, the terminal-notification and fresh-terminal sub-bullets and "Timed prompts" are untouched. `AGENTS.md` adds `overview.py` and `presence.py` to the restart-required list, notes that `#dashOverview` markup and CSS need only a hard reload, and says in "Dashboard attach" that `/` lands on the Overview, that clicking the open row again returns to it, and how a QA script finds a tile through `_dashOvTileEls`. `plans/tests/260701_POWERATLAS.md` §2.1 names the Overview as the no-session state, §2.16 adds `warm_usage` as a fifth lifespan concern, and briefs 2.28 and 2.29 cover the two new routes. `web.py`'s `_acp_csp` docstring no longer claims the dashboard renders no agent-authored text.

**Implementation (2026-09-28, code: c4b49a4) — review fixes**

The test plan's three "2.1–2.25" scope statements now include 2.28–2.29 and name 2.26/2.27 as ACP-scoped. The `test_no_policy_leaks_onto_the_dashboard` docstring in `tests/test_web.py` (outside Phase 5's declared scope, at the orchestrator's direction; docstring only) and the `_acp_csp` docstring now say plainly that the dashboard renders agent-authored text in the transcript panel and the Overview's live tiles, and that a dashboard CSP is a deferred follow-up. The README Overview bullet is split into short sentences under Live now, Active plans, Usage and Return paths, and states the exact stale boundary, that only rows with a tracker expand, and every Escape guard. The AGENTS.md tile recipe says `_dashOvTileEls` is meaningful only in overview mode after the first poll. The ROADMAP "stale /qdev" section lead bullet also says "partly addressed".

Tests: node 880 passed; pytest 2741 passed, 3 skipped.

QA: the live QA pass over SC-1…SC-10 is the Step 9b exhaustive QA, run after the final review; this criterion is ticked there.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Giant JSONL lines / bookkeeping-only tails | A tile shows no events | `tail_events` widens until n events or 2 MiB and skips oversized lines (D13, tested). |
| Tile/rail liveness drift | Tiles and dots disagree | Candidates are filtered through the rail's own helpers and filters via `LiveDeps` (D8, D10). The equality tests and the parity exit criterion name the D8 superset. |
| Casefolded cwds leak into the SessionCache original spelling | Wrong-case paths in rail rows | Normalised to original through `originals` before `get_sessions` (Phase 3 test). |
| Dead UNC or unmounted workspace stalls the plan scan | Summary route hangs; executor threads pile up | Skip `\\` paths, a 2 s deadline, client one-in-flight, 30 s reuse (D21, D23). |
| Warm pass fails or runs long | Usage stuck loading; slow restart | Per-file errors, a state machine with on-demand compute, `stop_event` (D12, tested). |
| Circular import `web` ↔ `overview` | ImportError at startup | `overview.py` never imports `web`; helpers are passed as `LiveDeps` (D10). |
| Snapshot rescan on the event loop | WS stalls every ~3 s | The snapshot is taken inside the worker (D24). |
| Memo growth on live files | Unbounded memory | Path-keyed memo plus window eviction (D22, tested). |
| Agent-authored text on a page without CSP | XSS on a cookie that reaches restart and remote-access routes | DOM-only rendering, numeric-only styles, adversarial tests per phase (D26). A CSP is a follow-up. |
| Tiles show tool arguments (possible tokens) on the landing view | Shoulder-surfing or screen-share exposure | Accepted. Loopback-only, and the same data is already reachable via the transcript panel. Arguments are capped at 80 chars. |
| `elapsedTime` unit is unknown | Agent time off by 1000× | Verified in Phase 4 before the numbers ship. |
| Claude agent time is derived, kiro time is reported | Uneven cross-provider comparison | 30 min per-turn cap. Labelled "estimated" in the UI and README. |
| Escape steals from menus or dialogs | The session is left unexpectedly | Capture-phase handler with explicit open-state predicates (D19, tested). |
| `unsubscribe` changes the server protocol | Older pages or other clients | An additive frame type. Unknown types are already rejected with `unknown_type`, so there is no break. |
| Returning closes an untouched dashboard-created session | Loses an empty session | Same as switching today (`dashCloseIfAbandoned`). Accepted (D20). |

## 7) Verification

- **Page script:** `node tests/acp_page.test.mjs`.
- **Python:** `.venv-PowerAtlas/Scripts/python -m pytest tests/ -q --timeout=300`.
- **Live QA (Phases 1–5):** standalone Playwright from the venv with the `pa_local` cookie (AGENTS.md § Verification Setup). Each Python phase needs a **user-approved restart** first. Checks:
  - the Overview on load;
  - each return path, and the Escape guard with a menu or picker open;
  - an `unsubscribe` sent, and a notification after returning;
  - tile/rail parity;
  - no polling while hidden;
  - the plans list against the files on disk;
  - usage going from loading to data;
  - J4: sign-out and create-refusal still replace the single placeholder.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Overview description in the dashboard panel bullet, plus the Claude-estimate and kiro-only caveats | 5 |
| `plans/ROADMAP.md` | Strike shipped Workspace Intelligence items (with the "shipped as the Overview" note). Reword "stale /qdev" to "partly addressed". Update both Parked lists. Keep LLM session names. | 5 |
| `AGENTS.md` | "ACP UI iteration": `overview.py`/`presence.py` restart-required, `#dashOverview` reload-only. § Verification Setup "Dashboard attach": Overview landing and click-again. | 5 |
| `plans/tests/260701_POWERATLAS.md` | §2.16 lifespan fifth concern `warm_usage`. §2.1 Overview as the no-session state. Briefs for the two new routes. | 5 |
| `docs/KNOWLEDGE.md` | Verified v3 `usage_summary.elapsedTime` unit and the usage record shapes | 4 |
| `src/power_atlas/web.py` `_acp_csp` docstring | Correct the "dashboard does not render agent-authored text" premise | 5 |

## Progress Tracker

| # | Phase/Task | Status | Notes |
|---|---|---|---|
| 1 | Overview shell, navigation, detach | Done | code 11f444b, 8b7fe7a, 9c67735 |
| 2 | Active plans | Done | code d198d70, 0c87284 |
| 3 | Live now tiles | Done | code 8952918, 9bbf18b |
| 4 | Usage insights | Done | code 0eec979, 28f5fdc, c700d20, 9f2deb6, 56713da |
| 5 | Docs, roadmap and full QA | In Progress | code 23e3965, c4b49a4; live QA at Step 9b |

## 9) Implementation Divergences from Plan

Phase 1:

1. `unsubscribe` is not gated on `_dashAttachedSid`. It is sent whenever the main socket is open, unless a close was just sent or a user Close is in flight. Reason: step 4.1's `dashCloseIfAbandoned()` clears `_dashAttachedSid` before step 4.2 reads it, so the plan's gate would never send the frame. Sending on an open socket also covers a subscribe still in flight, and the server treats unsubscribe on an unattached socket as a no-op.
2. `dashCloseIfAbandoned()` returns whether it sent a close, and `dashShowOverview` then skips `unsubscribe`. Reason: `_handle_close` runs as a spawned task and refuses with `not_subscribed` once the socket is detached, so a synchronous unsubscribe right after it would make the abandon-close fail.
3. `_handle_unsubscribe` also removes the socket from `_registry.loading` waiter lists. Reason: otherwise a subscribe parked behind a `session/load` re-attaches the socket when the load lands. The load's own initiator is still attached by `_deliver_load`; the page's stale-arrival `close` is the backstop.
4. The Escape guard also checks `dialog[open]`, plus one targeted `#dashMcpToggle[aria-expanded="true"]` predicate. Reason: D19 assumed all dialogs are divs, but the launcher, profile, workspace-settings and remote modals are real `<dialog>`s. The MCP panel has no open state other than that toggle's `aria-expanded`. No generic `aria-expanded` check was added.
5. Overview-mode CSS also hides `#dashSubPanel`. `dashShowOverview` also hides the composer and calls `clearTranscript()` and `resetCommandPalette()`. Reason: no sub-agent view, composer, MCP indicator or command catalogue from the session just left may show behind the Overview.
6. The initial Overview state is also in the server markup (class, title, `#dashHome` hidden). Reason: avoids a first-paint flash of the transcript pane.
7. `dashShowPane()` runs after the early returns in `openSessionTranscript`, `dashPickerCreate` and `dashRailQuickCreate`, and also in `dashHandle`'s created branch and the create-refusal branch. Reason: a refused call must not switch modes, and a session created (or a create refused) after the user went back to the Overview must still be shown.
8. The `session_closed` return to the Overview runs before `dashRailRefreshSoon()`/`dashPickerRunPending()`, and `_dashPendingCreate` is preserved across it. Reason: a pending close-then-create must still run.
9. `_dashUserClosedSid` is cleared on a refused or failed close (except `close_in_progress`) and on socket drop. A late `session` frame on the idle Overview gets an `unsubscribe`, and the deferred subscribe re-checks `_viewingSid`. Reason: Phase 1 review findings 1-2; a stale flag or a subscribe landing after Home left the session watched, breaking D18.
10. Escape with the sub-agent view open closes that view first; a second Escape returns to the Overview. Reason: user decision on 2026-09-25 ("When a sub-agent's transcript is open inside a session, what should Escape do?" answered "Close sub-agent first").
11. The node harness gains `El.cloneNode(deep)` and wires the real `initMcpIndicatorDom()`, and the `dashMaybeAttach` sentinel appends the real `dashDesc` source. Reason: the placeholder is restored by cloning, and the new checks need the real code rather than stubs.

Phase 2:

1. `scan_plans` takes an optional `now` keyword, used by the stale tests. Reason: a deterministic stale boundary (exactly 7 days is not stale; 7 days + 1 s is).
2. The 60 s summary refresh reuses the rail's existing `setInterval` and visibilitychange handler through `dashOverviewPoll()`, gated on the Overview being active, instead of a timer owned by `dashOverviewStart`. Reason: SC-5 says Plans and Usage refresh "with the existing 60 s rail poll".
3. The plans body div has `id="dashOvPlansBody"`. Reason: the node harness's element map is keyed by id.
4. A failed first load shows "Could not load plans."; a failed later refresh keeps the rows already drawn. A 403 still goes through the signed-out path. Reason: the spec did not cover failure.
5. A plan with no tracker rows renders its head as a `<div>` without `aria-expanded`. Reason: it has nothing to expand. Expanded rows stay expanded across the 60 s re-render, and the expand-state map is pruned to the rendered plans.
6. The workspace list also drops providers missing from `data.available_providers()`. Reason: matches the rail route (available ∩ enabled).
7. `progress.current` carries a `state` ("in_progress" or "next"), falling back to the first row not yet done, and the bar reads "Next: phase N" for the fallback. Reason: real trackers seldom mark a row In Progress, so the SC-6 phase label was empty on most plans (review finding).
8. Beyond D21: the Status value is capped at 500 characters and its comment stripped without a regex; the deadline is checked per file; a `plans` folder resolving to a UNC path is skipped; symlinks are not followed; the read is capped in bytes. A dead mapped or `subst` drive remains a recorded residual risk. Reason: security review findings (regex hang, dead-share stalls, read TOCTOU).
9. D23's single-flight is a lock held across check, scan and store. Reason: the simplest correct single-flight; waits are bounded by the 2 s scan deadline and run on worker threads.
10. The workspace chip's `title` is the full cwd. Reason: two workspaces can share a basename (two "PowerAtlas" folders exist on this machine).

Phase 3:

1. A held session is dropped when its provider (kiro-cli-v3) is disabled or unavailable. Reason: D8's prose ("minus disabled providers") and the rail hide that provider's rows. This is stricter than D8's parity formula "∪ held sessions without a row", which the parity criterion is judged against with this reading.
2. The per-case equality tests are one set-equality test covering every inclusion and exclusion together, plus separate tests for provider, filter, cap/order, original case and deleted transcript. Reason: one fixture makes an over-inclusive change fail; every case was mutation-verified.
3. While the tab is hidden or the panel is not in overview mode, the 2 s timer keeps ticking without fetching; it is not stopped on visibilitychange. Reason: simplest correct loop; a tab shown again fetches within 2 s.
4. The rail filters are factored into `_overview_rail_filters()` in web.py, shared with Phase 2's `_overview_workspaces()`; the live route reuses the result for 5 s. Reason: `_tag_keep` is an unimportable closure, and the per-poll `load_config()` cost 2.6–3.8 ms.
5. New ids `dashOvLiveBody`, `dashOvLiveAll`, `dashOvLivePa` (`aria-pressed`). Reason: the node harness's element map is keyed by id.
6. A failed first live load shows "Could not load live sessions."; later failures keep the tiles and back off to 10 s. Each request has an `AbortController`, aborted on halt and after 15 s (not 10 s, which the harness uses to recognise the backoff timer).
7. Response shape `{filter, tiles}`; a tile's `live` is `session_is_live` for held sessions and true otherwise (the dot reads `availability == 'held'` first).
8. The tail memo is a path-keyed LRU of `4 * LIVE_MAX_TILES` entries rather than a window-evicted map. Reason: pruning to each poll's paths made tabs with different filters evict each other (review finding).
9. v3 workspace hashes come from the transcript path, held sessions get none, and at most one `hash_dir_for_cwd` lookup runs per cwd per poll. Reason: that lookup walks every v3 session dir (16–30 ms each); measured 52–90 ms per poll for 3 v3 tiles before the fix.
10. The tile grid is persistent and patched in place; a tile is rebuilt only when its data changed. Reason: rebuilding every 2 s dropped keyboard focus (review finding).
11. One bad transcript line (e.g. a Claude `user` record with `message: null`, or deeply nested JSON) is skipped silently and the rest of the tail is memoised. Reason: it previously emptied the tile and logged a traceback every 2 s; per-line logging would flood the log.
12. A held session with no transcript and no `updated_at` sorts first, using the current time as its ordering key, while its displayed `last_activity` stays empty. Reason: otherwise a new held session could be cut by the 8-tile cap.
13. Known gap (not fixed): a live session id whose process cwd could not be read (psutil access denied, no sidecar cwd) gets a rail dot but no tile, because `live_sids()` needs a cwd. None existed on 2026-09-25.
14. Measured costs differ from §4 (2026-09-25, warm, 5 live sids, 8 candidate tiles): a presence rescan took 86–182 ms (§4: 42–75 ms; still in the worker per D24); a `discover_workspaces_with_counts` miss (130–211 ms) is paid by one poll every 30 s; a warm live poll took about 100–140 ms before the hash fix, most of it the v3 hash walk (divergence 9).

Phase 4:

1. **`elapsedTime` unit: milliseconds.** 399 turns over the 60 most recent v3 `messages.jsonl` files, joined by `executionId`: `elapsedTime / (usage_summary ts − turn_start ts in s)` had median, p10 and p90 of 1000.0 (range 999.8–1000.3) over spans of 0.9–1467 s. It is wall time and includes permission waits. Recorded in `docs/KNOWLEDGE.md`.
2. Per-file summaries are bucketed per local day (activity, agent seconds, tools, Claude tokens, context peak) rather than per-file totals. Reason: a file modified inside the window can hold older records; day buckets make the 14-day filter and the week split exact.
3. "This week" is the last 7 days of the window including today; "last week" is the 7 days before. Reason: a 14-day window cannot hold two calendar weeks.
4. Claude tokens are counted once per `message.id`. Reason: Claude Code repeats `message.usage` on every record of a split message (44–53 % of assistant records), so summing per record roughly doubles the totals.
5. A Claude prompt is a `user` record that is not `isMeta` or `isCompactSummary` and has non-empty string content or list content with no `tool_result` block; slash-command messages count as prompts. Reason: slash-command-driven turns would otherwise get no agent time; compaction summaries would split long turns.
6. Claude Code sub-agent transcripts (`<project>/<uuid>/subagents/*.jsonl`, 548 in-window files on 2026-09-28) count toward Claude tokens and tool reliability only, never sessions, agent time, models or per-workspace time. Reason: review finding; they held about 3.1 B cache-read tokens and 22k tool calls the plan's search rule missed. Known gap: 7 `message.id`s appear in both a sub-agent file and its parent, so those few are counted twice.
7. `usage_state` semantics: `warming` while a pass runs (with a non-null partial `usage` once stage 1 is done); `ready` with a complete aggregate; `error` when a compute fails (a background pass is restarted, and the page shows "Could not load usage."); `cold` only at shutdown. The route never parses on the request thread. `reparsed` means files parsed for this response.
8. The 30 s usage reuse is keyed on the rail's filters (providers and the hidden set), so a newly hidden workspace disappears at once without re-parsing. Filters are read through the live route's 5 s cache.
9. Two-stage warm pass with a partial state and a "Still counting sub-agent transcripts…" note. Reason: user decision on 2026-09-28 ("Usage speed": "Two-stage pass").
10. The warm pass parses in one child process (`_UsageWorker`) rather than a server thread, with an in-thread fallback. Reason: user decision on 2026-09-28 ("Usage timing": "Fix it now") after the two-stage pass still rendered 19 s / 26 s after a restart. Attribution (reproduction of the real app with two headless dashboard clients; the hidden peek webview is a second client): the CPU-bound parse shared the GIL with request threads; lock serialisation was ruled out. The remaining live-only gap before the fix was not isolated.
11. Measured costs (2026-09-28): cold parse single-threaded out of process 6.8 s over 682 files (kiro-cli v3 0.8 s, Claude main 2.2 s, Claude sub-agents 3.8 s over 526 MB); live after the child-process fix, partial Usage 5.9 s and complete 9.0 s after "Server ready". §4's "about 2.2 s over 148 files" predates the sub-agent files.
12. Kiro IDE `sessions.json` is read with `utf-8-sig`. One real file starts with a BOM; `data_kiro_ide.discover_workspaces` and `load_sessions` silently skip that workspace (pre-existing, not fixed; recorded in `docs/KNOWLEDGE.md`).
13. Frontend: `id="dashOvUsageBody"`, a "last 14 days" note, two daily charts (agent time, sessions) plus a provider legend instead of one combined bar, and "Could not load usage." on a failed first load or `error`.
14. The autouse `isolated_config` test fixture points `overview._usage_roots` at empty folders, disables the child-process worker, and resets usage state, cache, stop event and filter cache for every test. Reason: lifespan and summary-route tests would otherwise parse the developer's real stores.

Phase 5:

1. The README Overview description is a sibling bullet after "Click any session in the dashboard's workspaces rail…", not an extension of it. Reason: that bullet is about the transcript panel; the Overview reads more clearly on its own.
2. `tests/test_web.py`'s `test_no_policy_leaks_onto_the_dashboard` docstring was corrected (docstring only). Reason: it repeated the `_acp_csp` premise this phase corrects; the file is outside Phase 5's declared scope.
3. The test plan's dashboard scope now reads 2.1–2.25 plus 2.28–2.29, with 2.26/2.27 named ACP-scoped. Reason: the new route briefs would otherwise fall outside `/qtest` run mode's declared scope.
4. The `_acp_csp` docstring also names the transcript panel as rendering agent-authored text. Reason: it did so before this plan (dashboard ACP feature parity).
5. The `#dashOverview` reload-only note sits inline in the existing "ACP UI iteration" bullet of AGENTS.md, and § Verification Setup also documents the `_dashOvTileEls` tile lookup. Reason: live tiles carry no `data-sid`, which cost the Phase 3 QA several script iterations.

## Follow-up Work (Deferred)

1. **Needs-you inbox.** Pending permissions, unanswered questions, unwatched finished turns. Deferred by Q1 in favour of live tails.
2. **Real-time streaming for held tiles.** Needs a non-watching `/ws/acp` subscription mode (Q2 option B).
3. **Persisted usage history** beyond the provider stores' retention (Q7 option B).
4. **Claude context pressure.** Needs a maintained model→context-window table (Q10).
5. **Dashboard CSP.** `index.html` has no Content-Security-Policy, and it now renders agent-authored text. Source: the plan review of 2026-09-24 (Security #2) and risk row "Agent-authored text on a page without CSP".
6. **Tool arguments on the landing view.** Consider masking secret-shaped substrings in tile tool arguments. Source: risk row "Tiles show tool arguments".
7. **`get_snapshot` rescan lock.** A pre-existing duplicate rescan when two threads pass the TTL. Source: plan review 2026-09-24 (Performance #14).
8. **Stale `_acp_availability` docstring** ("a `psutil` query per id"; it actually reads `session.json`). Pre-existing, outside scope. Source: plan review 2026-09-24 (Performance).

## Review Log

### 2026-09-24 -- Plan Review (via /qplan, 1 cycle by user override)

Four personas (Architect, Senior engineer, Performance engineer, Security auditor) at standard effort, plus the mandatory doc-impact sub-agent. After de-duplication: 42 findings (5 High, 20 Medium, 17 Low), all auto-resolved in this revision. The review cycle was capped at 1 by the user ("/qplan with 1 qreview cycle"), so there was no re-review pass.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Returning to the Overview left the dashboard socket subscribed, keeping the last session watched and its notifications suppressed. | Fixed -- D18 adds an `unsubscribe` frame sent by `dashShowOverview`; Phase 1 now includes `acp.py` and a restart. |
| 2 | High | The `[aria-expanded="true"]`/`dialog[open]` Escape guard is almost always true, and the page's dialogs are divs. | Fixed -- D19 capture-phase handler with explicit open-state predicates, plus node tests. |
| 3 | High | The Overview ignored the rail's hidden-tag and disabled-provider filters, breaking parity. | Fixed -- D8 applies `_tag_keep`/`_enabled` to tiles, plans and usage, with tests. |
| 4 | High | No rendering-safety gate for agent-authored text on a page with no CSP. | Fixed -- D26 DOM-only rendering, numeric styles, adversarial node tests per phase; CSP deferred. |
| 5 | High | The plan scan caught only `OSError`; bad encoding or paths would 500 the summary, and dead UNC paths would stall it. | Fixed -- D21 robust reads, skip `\\`, 2 s deadline, per-file errors. |
| 6 | Medium | `#dashTranscriptWrap.hidden` as the mode flag is reset by `dashCloseSubagentView` on every socket drop. | Fixed -- D15 panel mode class on `#sessions-panel`, with a socket-drop node test. |
| 7 | Medium | `overview.py` using `web.*` helpers is a circular import and inverts the dependency direction. | Fixed -- D10 passes the helpers as `LiveDeps`; `overview` never imports `web`. |
| 8 | Medium | `get_snapshot()` evaluated as a `to_thread` argument blocks the event loop. | Fixed -- D24 takes the snapshot inside the worker. |
| 9 | Medium | The live route had no `acp is None` guard. | Fixed -- D24 guard, with a test. |
| 10 | Medium | `_read_tail_lines` stops at one line, so tiles would often show 0 events. | Fixed -- D13 own widening loop until n events, memoised, tested. |
| 11 | Medium | Casefolded snapshot cwds passed to `get_sessions` overwrite the original spelling. | Fixed -- map through `originals` first, with a test. |
| 12 | Medium | The parity exit criterion could not hold for held sessions without a rail row. | Fixed -- D8 defines the superset; the criterion is reworded. |
| 13 | Medium | Kiro IDE counts through `get_sessions` would load 530 sessions into SessionCache (1.52 s). | Fixed -- D25 reads the IDE store directly. |
| 14 | Medium | The warm pass had no failure or stop path, risking permanent "loading" and slow restarts. | Fixed -- D12 state machine, on-demand compute, `stop_event`, tests. |
| 15 | Medium | Usage memo key `(path, mtime, size)` grows per append; no window eviction. | Fixed -- D22 path-keyed memo plus eviction, tested. |
| 16 | Medium | Memo tests checked only hits; a never-invalidating memo would pass. | Fixed -- invalidation and exactly-once re-parse tests in Phases 2 and 4. |
| 17 | Medium | Cookie tests without the `anonymous_client` fixture pass vacuously as remote peers. | Fixed -- `anonymous_client`, a positive control and a remote-peer check for both routes. |
| 18 | Medium | `filter_` would not bind to `?filter=` without an alias, and was unvalidated. | Fixed -- `Query(alias="filter")` with a validated fallback and a route-level test. |
| 19 | Medium | A vanished or locked transcript could fail the whole live response. | Fixed -- per-tile error handling and a deleted-transcript test. |
| 20 | Medium | The usage full parse had no per-line cap. | Fixed -- skip lines > 8 MiB (D21). |
| 21 | Medium | Node-test sandbox updates were under-specified (picker, connect, close-button, sentinel regions). | Fixed -- Phase 1 item 9 lists every region and runs the new region for real. |
| 22 | Medium | Overview fetch 403s never reached the signed-out handling. | Fixed -- `dashOverviewFetch` calls `dashReportSignedOut()` once and stops polling. |
| 23 | Medium | Cost section understated the live poll and the per-request re-parse. | Fixed -- §4 and D6 state the measured bounds; D23 adds 30 s reuse. |
| 24 | Medium | The summary poll had no single-flight guard. | Fixed -- D23 client one-in-flight plus 30 s server reuse. |
| 25 | Medium | Doc-impact: `plans/tests/260701_POWERATLAS.md` §2.16/§2.1 and the new routes were missing from §8. | Fixed -- added to §8 and Phase 5. |
| 26 | Low | Click-again discarded the retry path after a failed load. | Fixed -- D20 toggles only when the transcript loaded. |
| 27 | Low | Locked-session tile click behaviour was unspecified. | Fixed -- D20 makes locked tiles inert like rail rows. |
| 28 | Low | `_dashUserClosedSid` was never cleared. | Fixed -- set after a successful send, cleared when consumed and on open or Overview entry. |
| 29 | Low | `.viewing` would lag up to 60 s for sessions opened from a tile. | Fixed -- applied immediately to matching rows (D14). |
| 30 | Low | "Tile sids satisfy the filter predicate" was a tautological test. | Fixed -- expected-set equality tests with fixture stores. |
| 31 | Low | `live_cwds()` returns bare cwds, not `(provider, cwd)` pairs. | Fixed -- new `live_cwd_pairs()` accessor. |
| 32 | Low | Command-line session ids were not validated before path joins. | Fixed -- UUID regex check (D21). |
| 33 | Low | Parse-failure logging was unspecified and could log transcript content. | Fixed -- log the path only. |
| 34 | Low | Style sinks from fetched strings (bars, provider colours) were unspecified. | Fixed -- D26 numeric-only styles and a fixed class map. |
| 35 | Low | `discover_workspaces_with_counts` has no display name; `_session_is_live` was cited at line 236. | Fixed -- `Path(cwd).name`; citation corrected to 238. |
| 36 | Low | Fragile exit criteria: literal "8/12", and "< 300 ms" wall time. | Fixed -- compared against the live tracker, and a `reparsed == 0` counter. |
| 37 | Low | Hard-coded store paths duplicated existing constants. | Fixed -- use `CLAUDE_PROJECTS_DIR` and `_V3_SESSIONS_ROOT`. |
| 38 | Low | Roadmap "stale /qdev" is a session heuristic, not a plan-mtime badge; a second Parked list was missed. | Fixed -- Phase 5 rewords it to "partly addressed" and updates both lists. |
| 39 | Low | The `presence.py` restart need and the `/` landing change were missing from AGENTS.md updates. | Fixed -- Phase 5 AGENTS.md rows. |
| 40 | Low | The README lacked the Claude-estimate caveat the risk table claimed. | Fixed -- Phase 5 README sentence. |
| 41 | Low | The `elapsedTime` unit finding would be archived with the plan only. | Fixed -- recorded in `docs/KNOWLEDGE.md` in Phase 4. |
| 42 | Low | Bare "Phase N" comments collide with existing comments in the same files. | Fixed -- new comments cite the plan slug. |

**Raw per-reviewer summaries** (for audit; full reports are in the session transcript):

- **Architect**: 21 findings (1 High). 60% confidence as written. Blocking: the Escape guard, the mode flag, parity for held sessions with no row.
- **Senior engineer**: 15 findings (2 High). 55% confidence as written. Blocking: the server-side detach and the Escape guard.
- **Performance engineer**: 15 findings (0 High). 80% confidence. Would ship as defects: the snapshot on the loop, the memo key shape, the stuck warm pass.
- **Security auditor**: 12 findings (4 High). 75% confidence. Blocking: hidden-workspace parity, rendering safety, robust plan reads, the UNC stall.
- **Doc-impact**: 8 documentation gaps. All were added to §8 and Phase 5, or to Phase 4 for KNOWLEDGE.md.

### 2026-09-25 -- Implementation Review (after Phase 1, persona: Senior engineer, Reliability engineer)

Implementation health: Green.
11 findings (2 High, 5 Medium, 4 Low).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `_dashUserClosedSid` was never cleared when a close was refused, failed or lost to a socket drop, so Home skipped `unsubscribe` and the session stayed watched. | Fixed -- cleared on a matching error (except `close_in_progress`) and on socket close, with the Close button restored (8b7fe7a). |
| 2 | High | A subscribe chained on a still-connecting socket fired after the user went back to the Overview, leaving the session watched. | Fixed -- the deferred subscribe re-checks `_viewingSid`; a late `session` frame on the idle Overview gets `unsubscribe` (8b7fe7a). |
| 3 | Medium | `dashShowOverview` did not call `resetCommandPalette()`, so the left session's MCP indicator and catalogue stayed visible. | Fixed -- called in the teardown, with a node check (8b7fe7a). |
| 4 | Medium | Escape with the MCP panel open left the session, because that panel's only open state is its toggle's `aria-expanded`. | Fixed -- one targeted `#dashMcpToggle` predicate, with a node check (8b7fe7a). |
| 5 | Medium | A create refusal arriving after Home was written into the hidden pane and removed its placeholder. | Fixed -- `dashShowPane()` in the refusal branch, with a node check (8b7fe7a). |
| 6 | Medium | The unsubscribe gate, the D14 `.viewing` loop and the `.viewing` clear had no test; mutations passed all 832 checks. | Fixed -- mutation-verified node checks added (8b7fe7a). |
| 7 | Medium | The declared divergences were not recorded in §9. | Fixed -- recorded in §9 by the Step 7 plan update. |
| 8 | Low | `_handle_unsubscribe`'s docstring claimed it stops a late attach after a load, but it only drops waiters. | Fixed -- docstring narrowed, initiator behaviour pinned by a pytest (8b7fe7a). |
| 9 | Low | Unsubscribe logged only for attached sockets, and a `defaultPrevented` check in the capture listener was dead code. | Fixed -- INFO for waiter removal, DEBUG for a no-op; the dead check removed with a comment (8b7fe7a). |
| 10 | Low | A pending close-then-create was dropped when the user-close returned to the Overview. | Fixed -- `_dashPendingCreate` preserved across that call, with a node check (8b7fe7a). |
| 11 | Low | Escape with the sub-agent view open left the whole session rather than closing the view. | Fixed -- per the user's decision, Escape closes the sub-agent view first (9c67735). |

Cycle 2 was not run: the user capped review at 1 cycle per phase ("1 qreview cycle per phase", `/qdev` invocation, 2026-09-25). The Step 9 final review covers the fixes. Findings 1, 4, 6 and 7 were raised by both personas; #4 was rated Medium by the Senior engineer and Low by the Reliability engineer, merged at Medium. Finding 7 is Step 7 bookkeeping.

### 2026-09-25 -- Implementation Review (after Phase 2, persona: Security auditor, Senior engineer)

Implementation health: Green.
16 findings (1 High, 4 Medium, 11 Low).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | The Status-line comment regex ran in quadratic time on long whitespace, so one crafted plan file could hold executor threads for minutes per request. | Fixed -- Status capped at 500 chars, comment stripped without regex, 100k-space time-bound test (0c87284). |
| 2 | Medium | D23 promised server-side single-flight, but concurrent cold requests each ran a full scan. | Fixed -- lock held across check, scan and store; concurrent test asserts one scan (0c87284). |
| 3 | Medium | Mapped drives or junctions to dead shares passed the string check, and the deadline was checked only between cwds. | Fixed -- per-file deadline and realpath-UNC skip; dead mapped drives recorded as residual risk (0c87284). |
| 4 | Medium | The 30 s reuse test never checked expiry, so a never-expiring cache would pass. | Fixed -- test ages the cache 31 s and asserts a second scan (0c87284). |
| 5 | Medium | On real trackers `current` was usually null, so the bar never named a phase (SC-6). | Fixed -- falls back to the first not-done row, labelled "Next: phase N" (0c87284). |
| 6 | Low | "Not started" and "implemented / review pending" tracker statuses showed as "Other". | Fixed -- mapped to pending and in progress, with a table test (0c87284). |
| 7 | Low | `scan_plans(now=…)` had no caller. | Fixed -- used by the stale tests, which now pin the 7-day boundary (0c87284). |
| 8 | Low | The 60 s poll wiring through the rail's interval had no test. | Fixed -- node check fires the registered interval with the Overview active and inactive (0c87284). |
| 9 | Low | No test pinned `errors="replace"`; a strict decode would silently drop a plan with one stray byte. | Fixed -- stray-byte plan is listed with U+FFFD, mutation-verified (0c87284). |
| 10 | Low | The size cap was checked on stat but the read was unbounded (TOCTOU with an appending agent). | Fixed -- binary read capped at the limit plus one byte (0c87284). |
| 11 | Low | Symlinked plan files were followed, reading targets outside the workspace. | Fixed -- `lstat` and regular-file check, with a test (0c87284). |
| 12 | Low | Two workspaces with the same basename got identical chips. | Fixed -- chip tooltip holds the full cwd (0c87284). |
| 13 | Low | Memo eviction on complete scans and the available-provider filter had no test. | Fixed -- one assertion each (0c87284). |
| 14 | Low | A Status line separated by an en dash was silently excluded. | Fixed -- en dash accepted, with a test (0c87284). |
| 15 | Low | The client expand-state map was never pruned. | Fixed -- pruned to the rendered plans (0c87284). |
| 16 | Low | The plan's §9 held no Phase 2 divergences at the reviewed commit. | Fixed -- recorded in §9 by the Step 7 plan update. |

Cycle 2 was not run, per the user's 1-cycle cap. Finding 9 was raised by both personas. The fix sub-agent was interrupted once by an API session limit before editing any file, and was resumed with its context intact. The Senior engineer's read-only parity check over 47 real workspaces found every inclusion, exclusion, count and badge correct.

### 2026-09-25 -- Implementation Review (after Phase 3, persona: Security auditor, Performance engineer, Senior engineer)

Implementation health: Green.
17 findings (0 High, 5 Medium, 12 Low).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | A non-`ValueError` per-line error (null Claude `message`, deep nesting) emptied the tile, skipped the memo and logged a traceback every poll. | Fixed -- per-line `except Exception`, result memoised, poison-line tests (9bbf18b). |
| 2 | Medium | D8's hidden-workspace rule was untested on the held path and the cmdline path; removing either check passed all tests. | Fixed -- both cases added to the set-equality test, mutation-verified (9bbf18b). |
| 3 | Medium | Every 2 s poll rebuilt the tile grid, so a keyboard-focused tile lost focus. | Fixed -- persistent grid patched in place, node focus checks (9bbf18b). |
| 4 | Medium | The v3 workspace-hash lookup walked every v3 session dir per tile per poll (52–90 ms for 3 tiles). | Fixed -- hash from the transcript path, none for held sids, one lookup per cwd (9bbf18b). |
| 5 | Medium | Pruning the tail memo to each poll's paths made tabs with different filters evict each other. | Fixed -- LRU of `4 * LIVE_MAX_TILES`, with a filter-toggle test (9bbf18b). |
| 6 | Low | The memo bound had no test. | Fixed -- bound and LRU-order tests, mutation-verified (9bbf18b). |
| 7 | Low | Each poll ran `load_config()` and rebuilt the filter map. | Fixed -- rail filters reused for 5 s on the live route (9bbf18b). |
| 8 | Low | A restart could overlap an in-flight request, and a hung request froze the tiles with no timeout. | Fixed -- per-generation `AbortController`, 15 s timeout (9bbf18b). |
| 9 | Low | Each transcript was resolved and stat'd 3–4 times per poll. | Fixed -- `tail_events` reuses the activity-pass stat (9bbf18b). |
| 10 | Low | The widening loop re-parsed the inner window at each step. | Fixed -- each step parses only the new bytes, boundary line carried (9bbf18b). |
| 11 | Low | The session-id regex existed in three copies. | Fixed -- web.py uses `overview.SESSION_ID_RE` (9bbf18b). |
| 12 | Low | A held "New session" with no transcript sorted last and could be cut by the cap. | Fixed -- sorts first, display unchanged (9bbf18b). |
| 13 | Low | A Phase 2 node check sliced source with `"\n}\n"`, failing on CRLF checkouts. | Fixed -- normalised before slicing (9bbf18b). |
| 14 | Low | A live sid with an unreadable cwd gets a rail dot but no tile; D8 does not name this. | Fixed -- recorded as a known gap in §9 (Phase 3 item 13). |
| 15 | Low | Dropping held sessions of a disabled provider departs from D8's literal parity formula. | Fixed -- reading recorded in §9 (Phase 3 item 1). |
| 16 | Low | Plan §4 cost figures no longer match measurement. | Fixed -- measured figures recorded in §9 (Phase 3 item 14). |
| 17 | Low | §9 held no Phase 3 divergences at the reviewed commit. | Fixed -- recorded in §9 by the Step 7 plan update. |

Cycle 2 was not run, per the user's 1-cycle cap. Findings 1, 2 and 3 were raised by two or three personas; 5 was rated Medium by the Security auditor and Low by the Performance engineer, merged at Medium. Two reviewers ran in CRLF worktrees, where one pre-existing Phase 2 node check failed (finding 13). The Senior engineer's read-only parity check found tiles and rail equal (8 = 8) on 2026-09-25, and `tail_events` returned 5 mixed events on the 12 largest and most recent real transcripts in 0.3–8.8 ms each.

### 2026-09-28 -- Implementation Review (after Phase 4, persona: Performance engineer, Senior engineer)

Implementation health: Green.
18 findings (0 High, 6 Medium, 12 Low).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | D23's usage single-flight was untested; removing the lock passed every test. | Fixed -- concurrent test, lock-removal mutant killed (28f5fdc). |
| 2 | Medium | The 8 MiB per-line cap was not pinned; the test's oversized line was invalid JSON anyway. | Fixed -- valid oversized record test, mutant killed (28f5fdc). |
| 3 | Medium | Claude Code sub-agent transcripts were outside the file search, missing about 3.1 B cache-read tokens and 22k tool calls. | Fixed -- counted for tokens and tools only (28f5fdc). |
| 4 | Medium | The Kiro IDE path of the hidden-workspace and provider rule was untested. | Fixed -- IDE cases added, both mutants killed (28f5fdc). |
| 5 | Medium | The this-week / last-week boundary was untested; an 8-day week passed. | Fixed -- records at now−6 d and now−7 d, mutant killed (28f5fdc). |
| 6 | Medium | Live QA: Usage rendered about 15 s, then 19 s / 26 s, after a restart (target about 10 s). | Fixed -- two-stage pass and child-process parse, 5.9 s / 9.0 s live (c700d20, 9f2deb6, 56713da). |
| 7 | Low | Plan §4 and D6 cost figures were stale. | Fixed -- measured figures recorded in §9 (Phase 4 item 11). |
| 8 | Low | Criterion 2 was ticked without the §9 half, and §1 still called the unit unverified. | Fixed -- §9 item 1 and §1 updated by the Step 7 plan update. |
| 9 | Low | `warming` was set inside the worker, so an early request could trigger a full on-demand pass. | Fixed -- lifespan sets `warming` before creating the task (28f5fdc). |
| 10 | Low | On-demand computes took no stop event. | Fixed -- one module-level stop event covers every pass (28f5fdc). |
| 11 | Low | `sessions_total` counted only sessions with context data. | Fixed -- counts every in-window kiro-cli session (28f5fdc). |
| 12 | Low | A metadata-only in-window v3 file counted in models and context pressure. | Fixed -- peak kept per day, in-window only, mutant killed (28f5fdc). |
| 13 | Low | Compaction-summary records counted as Claude prompts. | Fixed -- excluded like `isMeta` (28f5fdc). |
| 14 | Low | A failed warming re-fetch left a stale skeleton with no retry. | Fixed -- shows "Could not load usage." (28f5fdc). |
| 15 | Low | The 30 s usage reuse ignored filter changes. | Fixed -- reuse keyed on the filters (28f5fdc). |
| 16 | Low | The context peak was rounded before the 80 % comparison. | Fixed -- raw comparison, rounded for display (28f5fdc). |
| 17 | Low | A cold or error state made the summary route (and plans) wait for a full parse. | Fixed -- background pass, immediate return (28f5fdc). |
| 18 | Low | §9 held no Phase 4 divergences at the reviewed commit. | Fixed -- recorded in §9 by the Step 7 plan update. |

Cycle 2 was not run, per the user's 1-cycle cap; the two later timing fixes (two-stage pass, child process) were verified by live QA rather than re-review, and the Step 9 final review covers them. Finding 8 was raised by both personas. The Senior engineer's read-only cross-check reproduced every v3 day's agent seconds, every daily session count and the Claude token totals exactly. The fix sub-agent was interrupted once by the end of a Claude Code session and resumed with its uncommitted work intact.

### 2026-09-28 -- Implementation Review (after Phase 5, persona: Senior engineer)

Implementation health: Green.
8 findings (0 High, 1 Medium, 7 Low).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | The test plan's "2.1–2.25" scope statements left the new briefs 2.28/2.29 outside `/qtest` run mode's scope. | Fixed -- all three statements widened, 2.26/2.27 named ACP-scoped (c4b49a4). |
| 2 | Low | A `tests/test_web.py` docstring still claimed the dashboard renders no agent-authored text. | Fixed -- docstring corrected, assertion unchanged (c4b49a4). |
| 3 | Low | The corrected `_acp_csp` docstring misdated when its premise became false and read poorly. | Fixed -- reworded plainly (c4b49a4). |
| 4 | Low | The README Overview text was one long semicolon-chained paragraph in an undeclared new bullet. | Fixed -- split under four sub-bullets; placement recorded in §9 (c4b49a4). |
| 5 | Low | Three README details were imprecise (tracker expansion, the stale boundary, Escape guards). | Fixed -- all three stated exactly (c4b49a4). |
| 6 | Low | The AGENTS.md tile recipe did not say `_dashOvTileEls` is meaningful only in overview mode after the first poll. | Fixed -- recipe says so, with a `wait_for_function` example (c4b49a4). |
| 7 | Low | The ROADMAP "stale /qdev" section lead bullet was not reworded. | Fixed -- now says "partly addressed" too (c4b49a4). |
| 8 | Low | §9 held no Phase 5 divergences at the reviewed commit. | Fixed -- recorded in §9 by the Step 7 plan update. |

Cycle 2 was not run, per the user's 1-cycle cap; the fixes are prose only and the Step 9 final review covers them.

## Harness Improvement Opportunities

- The governance rule "a sub-agent's deliverable is a file" conflicts with the harness. All three
  `/qexplore` Step 1.5 sub-agents were told to write their report to a scratch path and were
  refused ("Subagents should return findings as text"). — cost: no lost findings, since each
  returned its report inline; but every brief carried a write instruction that could not work, and
  each agent spent a turn discovering that — suggested change: in `shared/AGENTS.md` §
  Multi-Agent Coordination, note that Claude Code sub-agents return reports as text, and apply
  the file-deliverable rule only where the harness permits sub-agent writes.
- `/qdev` Step 5b calls `/qqa`, but no `/qqa` skill is installed in this Claude Code setup (only
  `/qbrowser-test`). Phase 1's QA was hand-written Playwright following AGENTS.md § Verification
  Setup. — cost: about 25 minutes of script iteration (row selectors, the rule that an unprompted
  session has no rail row, cleanup) — suggested change: install `/qqa` for Claude Code, or have
  `/qdev` Step 5b name `/qbrowser-test` as the fallback when `/qqa` is absent.
- Review sub-agents running with `isolation: "worktree"` test the **main** repo's source, because
  the venv installs `power_atlas` in editable mode from the main tree. One reviewer noticed only
  when a mutation went undetected. — cost: two wasted pytest runs, and a real risk of a false "this
  test pins it" claim — suggested change: add "set `PYTHONPATH=src` when running pytest from a
  worktree" to this project's AGENTS.md § Doc & Test Guidelines.
- Harness worktrees for review sub-agents check out with `core.autocrlf=true`, while the main
  tree stores LF, so a node test that slices template source on `"\n}\n"` fails only in the
  reviewer's worktree. Two Phase 3 reviewers each spent turns proving the failure was
  environmental. — cost: roughly 10 reviewer turns and one pre-existing defect discovered by
  accident — suggested change: in this project's AGENTS.md § Doc & Test Guidelines, say that
  source-slicing tests must normalise `\r\n` first.
- User override of the per-phase review cycle cap: the default is 2 cycles (`/qdev` Step 6),
  overridden to 1 ("1 qreview cycle per phase", `/qdev` invocation, 2026-09-25). Recorded per
  `shared/AGENTS.md § Continuous Improvement`. — cost: none observed yet; a fix regression would
  surface only at Step 9 — suggested change: none unless Step 9 finds a regression that a cycle 2
  would have caught.
