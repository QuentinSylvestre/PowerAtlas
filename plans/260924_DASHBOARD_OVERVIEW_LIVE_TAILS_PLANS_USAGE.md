# Dashboard Overview: Live Session Tails, Active Plans and Usage Insights

> **Date**: 2026-09-24
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Replace the dashboard's empty Transcript panel with an Overview (live session tails, active plans, 14-day usage insights), reachable again after a session is opened

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

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### Existing patterns & constraints

**Right panel**

- The empty placeholder lives only in server markup (`index.html:449-456`). It must remain the
  single `.acp-system-msg` child: the J4 path at `index.html:1153-1161` depends on it, and so does
  `tests/acp_page.test.mjs:11059-11070`.
- `#dashSessionMeta` (`index.html:444-448`) is the precedent for content that lives outside the
  pane, which is wiped on every load.
- `_viewingSid` is the single authority for staleness. It is written only at `index.html:1015`,
  `3120` and `5882`.
- `dashCloseIfAbandoned` (`index.html:3038-3083`) is the detach path used on switch.
- `session_closed` (`3662-3697`) currently keeps `_viewingSid` set.

**Rail liveness**

- `_session_is_live` (`web.py:238-266`) returns true when either:
  - the session id appears on a process command line (`presence.Snapshot._live_sids`), or
  - a provider process runs in the session's cwd and its JSONL was written within 300 s.
- Held sessions come from `_supervisor.sessions` (at most 8). `_acp_availability` is at
  `web.py:2232-2280`.
- Supervisor state is owned by the event loop and must be captured there, then passed to worker
  threads (`web.py:2232-2240` docstrings).
- `presence.get_snapshot()` takes 60 ms uncached and has a 3 s TTL (`presence.py:680`). Measured
  2026-09-24: 5 live session ids and 3 live cwds.

**Lazy loading**

- The rail loads unpinned workspaces lazily (`_acp_listing`), so the rail's rows cannot be the
  source of the live set.
- `refresh_stale_entries` (`data.py:341-356`) covers only workspaces already loaded.

**Transcript reading**

- Transcripts are parsed by `get_full_transcript` (`data.py:431`), which is uncached, then passed
  through `translate_transcript`.
- `status_classifier` reads the tail of a transcript through a window that widens from 64 KB to
  2 MB (`status_classifier.py:132`).

**Record shapes, probed 2026-09-24**

- **kiro-cli v3**
  - `usage_summary` has `elapsedTime` (int) and `promptTurnSummaries`.
  - `tool_call` has `toolName`, `kind` and `status`.
  - `tool_result` has `success` (bool).
  - `session_metadata` has `value.usagePercentage`.
  - `session.json` has `modelId` (255 of 259 sessions), `status` and `effortLevel`.
- **Claude Code**: assistant records carry `message.model`, `message.usage` (input, output and
  cache token fields) and `timestamp`.
- **Kiro IDE**: has no tool concept (`data_kiro_ide.py:376`).

**Plans**

- No code in `src/` reads plan files today.
- The Status grammar is the closed set `Exploring | Draft | In Progress | Complete`, optionally
  followed by `— detail` (agent-playbook `shared/skills/qplan/TEMPLATES.md` § Status Grammar).
- `## Progress Tracker` is a table with columns `# | Phase/Task | Status | Notes`. Its Status
  values are Done, In Progress and Pending. Non-numeric rows such as `U` also occur.

**Security and rendering**

- Every route except `/local-auth` and `/static` requires the `pa_local` cookie
  (`web.py:1752-1772`).
- The remote allowlist is deny-by-default (`web.py:1426-1435`).
- The no-innerHTML rule for fetched data applies (`index.html:226-227`, `1632`).

**Tests and restarts**

- `tests/acp_page.test.mjs` is not run by CI; run it by hand.
- Sandbox tests enumerate the free variables of the functions they execute. New helpers called
  from sandboxed functions need stubs there (a precedent from commit `6f9a1b8`).
- Python changes need a user-initiated PowerAtlas restart (AGENTS.md). Template and static changes
  need only a hard reload.

### Risks & mitigations

- **Giant JSONL lines.** The largest v3 transcript is 100 MB across only 153 lines, so a fixed
  64 KB tail read can land mid-line and yield nothing. Mitigation: widen the read the way
  `status_classifier` does, and skip or truncate oversized records.
- **Live-list parity drift.** If the Overview recomputes liveness its own way, tiles and rail dots
  will disagree. Mitigation: reuse `_acp_availability` and `_session_is_live` on candidates rather
  than reimplementing them.
- **Side effect of building the live list.** Candidate discovery through `get_sessions(cwd)` loads
  live-process folders into the SessionCache, and the 30 s background refresh then covers them.
  The extra cost is small, with about 3 folders today.
- **Cold-cache cost of the stats pass.** Measured on a warm OS cache: a full JSON parse took
  1.24 s for v3 (193 files, 265 MB, 51,510 records) and 1.73 s for Claude (48 files, 304 MB,
  94,006 records). A cold disk after a reboot will be slower. Mitigation: run the pass in a
  background thread, use the 14-day mtime filter, and show a loading state.
- **Two ways of measuring agent time.** kiro `elapsedTime` is exact. Claude agent time is derived
  from timestamps, from each turn's user prompt to its last assistant record. Label or document
  the difference.
- **Out-of-date roadmap premises.** The 5 s status poll and the workspace cards are gone, and
  there is no clarifying-question state. Mitigation: the plan must not build on them, and the
  ROADMAP needs updating on close.
- **Detaching when returning to the Overview.** Returning to the Overview closes a
  dashboard-created session that has never been used. This is the same as switching sessions
  today, so the behaviour is unchanged.

### Resolved decisions

- Q1: Which roadmap items does the Overview's first version cover (plans, needs-you inbox, usage
  stats, LLM names)? — A: plans + usage stats; instead of the inbox, show the live tail of every
  live session, filterable to PowerAtlas-owned or all, at most 4-8 — Decision: Active plans +
  Usage + Live tails. Inbox and LLM names are out of this project.
- Q2: How does the live tail get its data: file-tail polling for all providers (A), or ACP
  streaming for held sessions plus files for the rest (B)? — A: A — Decision: poll transcript
  file tails about every 2 s. Never subscribe over `/ws/acp`, because a subscription counts as
  watching and would suppress notifications.
- Q3: How does the user return to the Overview after opening a session? — A: Home button,
  Escape, and clicking the open row; closing or deleting the open session also goes straight to
  the Overview — Decision: SC-2 and SC-3. Only user-initiated close and delete navigate.
- Q4: What are the layout and the tile content? — A: accepted as proposed (Live now, Active
  plans, Usage; last ~5 events per tile; default All; 8 tiles), and clicking a live tile must
  open the transcript/session view; Usage must be more insightful — Decision: SC-4, with the
  Usage content decided by Q5 and Q6.
- Q5: Which usage insights? — A: time by workspace, activity trend, tool reliability, context
  pressure — Decision: SC-7.
- Q6: Tokens and model mix? — A: model mix and tokens, no cost — Decision: model mix for both
  providers, plus Claude-only token totals and cache-hit ratio. No price table.
- Q7: Stats computed in memory with a per-file `(mtime, size)` memo (A), or persisted to disk
  (B)? — A: A — Decision: SC-8.
- Q8: Which plans are listed, what does a row show, and what does a click do? — A: stale after
  7 days instead of 24 h; show finished plans not yet closed with /qclose; Draft and Exploring
  plans do not belong — Decision: SC-6.
- Q9: Which sessions count as live for the tiles? — A: "live" must match PowerAtlas's own
  liveness status — Decision: held or `_session_is_live`, with no extra recency window. The server
  builds the live list from held sessions plus the presence snapshot and filters it through the
  rail's own functions, so workspaces the rail has not loaded are covered.
- Q10: Context pressure for Claude through a model-to-window table, or kiro-cli only? — A: kiro-cli
  only (as recommended) — Decision: context pressure is labelled kiro-cli only.
- Roadmap: LLM-generated session names stay on the roadmap (user, 2026-09-24).

### Open items

- **Deterministic.** Where exactly the per-file summary cache and the background pass live: a new
  module or `data.py`, and whether the pass hooks the existing 30 s `_background_refresh` or runs
  its own thread.
- **Deterministic.** The endpoint shape: one `/api/dashboard/overview`, or separate live, plans and
  usage routes. The tiles poll at 2 s and the rest at 60 s, which argues for at least splitting
  off the live route.
- **Deterministic.** Plan-name cleanup rules, and how the Progress Tracker parser handles
  multi-phase rows and non-numeric IDs.
- **Execution-contingent.** Claude agent-time derivation: validate it against a few real sessions
  before fixing the rule.
- **Execution-contingent.** Tile event rendering reuses the transcript translator. Its output shape
  for a compact 5-line tile has to be checked in practice.

### Assumptions (unconfirmed)

- The header label reads "Overview" when no session is open (UX flow).
- Tiles idle for more than 2 minutes are dimmed (UX flow).
- A non-user-initiated close keeps the closed message instead of navigating (UX flow).
- `README.md:102-111` is updated to describe the Overview, per the Doc & Test Guidelines
  (Documentation).
- No new external dependency, cost or build artifact (External dependencies, Build & distribution,
  Cost impact).

### Recommended approach

**Backend (restart required)**

1. A **live list** endpoint.
   - Capture the held session ids on the event loop.
   - In a worker thread, gather candidates from held sessions, `snapshot._live_sids` and
     `_sid_to_cwd`, plus `get_sessions(cwd, provider)` for each `live_cwds()` folder.
   - Filter through `_acp_availability` and `_session_is_live`.
   - For the first 8 by last activity, read a widening tail window, translate it, and return the
     last ~5 compact events.
2. A **plans** reader.
   - For each known workspace (from `discover_workspaces_with_counts`), glob `plans/*.md`, skipping
     `done/`.
   - Parse the Status line, the Progress Tracker and the `### Phase N` headings, and take the
     file mtime.
   - Memoise per file on `(mtime, size)`.
3. A **usage** aggregator.
   - A background pass over transcripts modified in the last 14 days.
   - A per-file summary with day buckets, agent time, tool counts and failures, the kiro context
     peak, the model, and Claude tokens.
   - Memoised on `(mtime, size)` and aggregated per request.

**Frontend (hard reload only)**

- An `#dashOverview` sibling of `#dashTranscriptWrap`, shown whenever `_viewingSid` is null. The
  placeholder stays hidden in the pane so the J4 check keeps working.
- A 2 s tile poller gated on Overview visibility and `document.visibilityState`, plus plans and
  usage refreshed with the 60 s rail poll.
- Home, Escape and click-again navigation.
- Close and delete handlers route to the Overview.
- All rendering through DOM nodes.

**Docs**

- README panel section.
- ROADMAP: strike the shipped parts of Workspace Intelligence (plan progress, stale `/qdev`, usage
  stats) and keep the LLM names.

### QA environment

- Live instance at `http://127.0.0.1:4915`. Sign in with the `pa_local` cookie recipe in
  `AGENTS.md` § Doc & Test Guidelines.
- Drive it with standalone Playwright from `.venv-PowerAtlas/Scripts/python` (see § Verification
  Setup).
- Python changes need a user-approved restart through `POST /api/restart`, or the topbar button.
- Real data on this machine:
  - About 5 live sessions and 3 live cwds.
  - 12 active plan files across 23 workspaces that have a `plans/` folder, including In Progress
    plans in PowerAtlas and agent-playbook, and Complete-but-unarchived ones.
  - 193 v3 and 48 Claude transcripts.
- Unit tests:
  - pytest for the plan parser (Progress Tracker variants, missing tracker, no Status line), the
    usage summariser (fixture JSONL for each provider) and live-list parity (mock snapshot plus
    held set).
  - `node tests/acp_page.test.mjs` for the page script (navigation back to the Overview, the J4
    single-child invariant).

## Harness Improvement Opportunities

- The governance rule "a sub-agent's deliverable is a file" conflicts with the harness. All three
  `/qexplore` Step 1.5 sub-agents were told to write their report to a scratch path and were
  refused ("Subagents should return findings as text"). — cost: no lost findings, since each
  returned its report inline; but every brief carried a write instruction that could not work, and
  each agent spent a turn discovering that — suggested change: in `shared/AGENTS.md` §
  Multi-Agent Coordination, note that Claude Code sub-agents return reports as text, and apply
  the file-deliverable rule only where the harness permits sub-agent writes.
