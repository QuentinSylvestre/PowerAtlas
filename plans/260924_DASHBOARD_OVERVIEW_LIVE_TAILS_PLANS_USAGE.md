# Dashboard Overview: Live Session Tails, Active Plans and Usage Insights

> **Date**: 2026-09-24
> **Status**: Draft  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
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
- `usage_summary.payload.elapsedTime` is an int. Its unit is **unverified**.
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
- [ ] `node tests/acp_page.test.mjs` passes, including all new cases above. `.venv-PowerAtlas/Scripts/python -m pytest tests/ -q --timeout=300` passes, including the `unsubscribe` test.
- [ ] After a **user-approved restart**, the live checks pass:
  - On page load, the panel shows "Overview" with three sections.
  - Opening a session shows the transcript and the Home button.
  - Home, Escape (focus on the page body, rail groups expanded) and clicking the loaded open row each return to the Overview, with `_viewingSid === null` and `.viewing` cleared.
- [ ] Escape in the composer, in the search field, with a rail menu open, or with the picker open does **not** leave the session.
- [ ] After returning to the Overview from a held session, `orchestrator.log` or a WS debug-log check shows the `unsubscribe`. A turn ending in that session afterwards produces a desktop notification (notifications enabled).
- [ ] Clicking Close returns to the Overview. A sweeper or remote close keeps "This session was closed." Deleting the open session returns to the Overview.
- [ ] After returning, the pane holds exactly one `.acp-system-msg[data-empty]` child.

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
- [ ] `pytest tests/test_web.py -k OverviewPlans --timeout=300` and `node tests/acp_page.test.mjs` pass.
- [ ] After a **user-approved restart**, the live Overview lists exactly the In Progress and Complete non-`done/` plans in rail-visible workspaces, comparing against a script that globs the same folders. It includes agent-playbook's `260924_QDREAM_COST_AND_SIGNAL_REWORK` with a `done/total` taken from its current tracker.
- [ ] Expanding a row shows its tracker rows. `stale` appears only on In Progress plans unchanged for more than 7 days.

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
- [ ] `pytest tests/test_web.py -k OverviewLive --timeout=300` and `node tests/acp_page.test.mjs` pass.
- [ ] After a **user-approved restart**, the set of tile sids equals the rail rows with a status dot (all workspaces expanded, or compared by script against `/api/dashboard/sessions` rows with `availability=='held' or live`) plus held sessions without a rail row. Any difference is explained by D8.
- [ ] A live Claude Code terminal session in a collapsed, unloaded workspace appears as a tile, and its `name` keeps its original case.
- [ ] While a live session works, its tile shows ≥ 1 event and updates within about 3 s. Clicking the tile opens its transcript and marks its rail row `.viewing` immediately. Home returns.
- [ ] With the Overview or the tab hidden, the Network log shows no `/overview/live` requests. Tiles never send `subscribe` (WS debug log).

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
- [ ] `pytest tests/test_web.py -k OverviewUsage --timeout=300` and `node tests/acp_page.test.mjs` pass.
- [ ] The `elapsedTime` unit is verified against real turn timestamps and recorded in `docs/KNOWLEDGE.md` (kiro-cli v3 store section) and §9.
- [ ] After a **user-approved restart**:
  - the section shows loading, then real data within about 10 s;
  - this-week totals for PowerAtlas and agent-playbook are non-zero;
  - the daily bars cover 14 days.
- [ ] Two consecutive summary requests with no transcript changes report `reparsed == 0` on the second (via the test-visible counter, not wall time).
- [ ] Context pressure and tokens carry their "kiro-cli only" / "Claude Code only" labels.

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
- [ ] `README.md` updated with the Overview description and its caveats.
- [ ] `plans/ROADMAP.md`:
  - Workspace Intelligence reflects the shipped items;
  - both Parked lists are updated;
  - the "stale /qdev" wording reads "partly addressed";
  - LLM session names are still present.
- [ ] `AGENTS.md` "ACP UI iteration" names `overview.py` and `presence.py` as restart-required, and § Verification Setup covers the Overview landing and click-again.
- [ ] `plans/tests/260701_POWERATLAS.md` §2.16, §2.1 and the new route briefs are updated.
- [ ] The `_acp_csp` docstring is corrected.
- [ ] `node tests/acp_page.test.mjs` and `.venv-PowerAtlas/Scripts/python -m pytest tests/ -q --timeout=300` pass.
- [ ] A live QA pass over SC-1…SC-10 against the running instance (the Playwright recipe in AGENTS.md § Verification Setup).

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
| 1 | Overview shell, navigation, detach | Pending | restart (acp.py) |
| 2 | Active plans | Pending | restart |
| 3 | Live now tiles | Pending | restart |
| 4 | Usage insights | Pending | restart |
| 5 | Docs, roadmap and full QA | Pending | |

## 9) Implementation Divergences from Plan
<Reserved -- filled during implementation>

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

## Harness Improvement Opportunities

- The governance rule "a sub-agent's deliverable is a file" conflicts with the harness. All three
  `/qexplore` Step 1.5 sub-agents were told to write their report to a scratch path and were
  refused ("Subagents should return findings as text"). — cost: no lost findings, since each
  returned its report inline; but every brief carried a write instruction that could not work, and
  each agent spent a turn discovering that — suggested change: in `shared/AGENTS.md` §
  Multi-Agent Coordination, note that Claude Code sub-agents return reports as text, and apply
  the file-deliverable rule only where the harness permits sub-agent writes.
