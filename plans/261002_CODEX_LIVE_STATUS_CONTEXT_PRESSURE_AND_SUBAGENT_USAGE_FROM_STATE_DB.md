# Codex Live Status, Context Pressure and Sub-agent Usage from the State Database

> **Date**: 2026-10-02
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Follow-ups deferred by the archived Codex provider plan: a lock-owner live dot, a Working/Idle verdict and turn-end notifications for terminal Codex sessions, Codex context pressure, sub-agent token usage, and an optional state-database layer that feeds them. Driving Codex from `/acp` and app-server integration are out of scope.

---

## Intent

### Problem statement & desired outcomes

The Codex provider shipped in `plans/done/261002-1113_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW.md`. Five gaps remain, and exploration measured each one on Codex 0.160.0 (Windows):

1. **The live dot is a guess.** A Codex row is live when `resume <uuid>` is on a process command line, or when a Codex process runs in its folder and its last activity is under 300 s old. An idle terminal session older than 300 s shows no dot. A desktop-held thread in the same folder as a terminal Codex can show one.
2. **No verdict and no notification for terminal Codex sessions.** Working/Waiting verdicts exist only for sessions that PowerAtlas's own ACP engine holds. Notifications fire only from that engine.
3. **Context pressure is kiro-cli only.** The Overview Usage block, the README and a node test all say so.
4. **The Usage page omits Codex sub-agent threads, and the omission is large.** In the current 14-day window, 26 sub-agent threads hold 24.2 M tokens against 37.0 M for the 4 top-level threads: about 40% of the Codex total. The archived plan assumed a lower bound of about 1.6% and an unknown size.
5. **Discovery reads only rollout files.** Codex's own state database (`state_5.sqlite`) holds data the rollouts do not, and it already matches the rollout scan.

Desired outcomes: a live dot that follows the thread's lock owner, a Working/Idle verdict and a turn-end toast for terminal Codex sessions, a Codex context-pressure estimate, an honest sub-agent token line, and a read-only state-database layer that fails open.

### Success criteria

- **SC-1 Live dot by lock owner.** A Codex row is live when a terminal process holds its writer lock. A terminal process is one whose command line has no helper subcommand (the existing `_CODEX_HELPER_SUBCOMMANDS` deny-list). The owner is looked up once when a lock appears and cached, and re-checked by process id and start time. When the lookup fails, or the platform is not Windows, the row uses today's rule. Verified live: an idle TUI older than 300 s shows a dot, a `codex exec` run behaves as measured, and a desktop-held or VS Code-held thread shows none (if one can be observed).
- **SC-2 Working/Idle verdict.** A terminal Codex row whose lock a terminal process holds shows Working or Idle from its last rollout record (`task_started` without `task_complete` is Working; `task_complete` is Idle). There is no Waiting state. A session whose process is gone shows neither. The rail dot and the Overview tile use the existing dot classes.
- **SC-3 Turn-end notification.** A toast fires once per new `task_complete` record of a lock-held terminal Codex session, through `notifications.py` and the existing `notifications.enabled` toggle. A session first seen mid-run records its starting offset without notifying, so old turns never fire.
- **SC-4 Context pressure.** The Overview Usage "Context pressure" block shows Codex as its own labelled estimate (peak of `last_token_usage.input_tokens` over `model_context_window` per day, sessions above 80%, top list). kiro-cli numbers are unchanged. `_USAGE_SCHEMA` is bumped.
- **SC-5 State-database layer.** A read-only reader opens `state_*.sqlite` with `mode=ro` (never `immutable=1`), in short-lived connections, normalises the `\\?\` path prefix, selects columns by name, and treats any `sqlite3` error or missing table as "no database". The spike's checks are re-run on the then-current Codex version before the layer ships: coverage equal to the rollout scan, recency within seconds, `tokens_used` equal to the rollout's final total, fail-open on a missing column.
- **SC-6 Sub-agent usage.** The Codex Tokens block gains a "Sub-agent threads" line (total only, split into `thread_spawn` and `guardian` review) for threads created in the window. The usage note says the total includes context inherited at spawn and comes from Codex's state database. With no database the page shows today's note. `claude_tokens` stays byte-identical.
- **SC-7 Documentation corrected.** README, `docs/KNOWLEDGE.md` (the 1.6% and "unknown size" statements are replaced by the measured figures and their method), the archived plan's decisions D4 and D5 are amended in a note, and the roadmap records the out-of-scope items with today's measurements.
- **SC-8 Cleanup.** The probe's throwaway Codex session (working folder ends `codexprobe_work`) is removed with the user's approval at the end.

### Scope boundaries & non-goals

In scope: the five gaps above, the state-database layer as optional enrichment, tests and documentation.

Out of scope, with the reason:
- **Driving Codex from `/acp`.** Codex has no ACP support of its own. An adapter exists: `@agentclientprotocol/codex-acp` 2.1.1 (Node, maintained under the ACP organisation, starts the Codex App Server, supports `session/new`, `session/load`, permission requests and four modes). Windows support and `session/list` are not stated. Not spiked. A future project would need a driver abstraction in `acp.py` and a permission model for Codex's approval policy and sandbox. Decided out of scope by the user (Q2).
- **App-server integration.** Dropped by the user (Q3). A standalone app-server saw a TUI's thread as `notLoaded`, and `thread/list` and `thread/read` return only metadata the rollouts already hold.
- **A Waiting verdict.** No approval record exists in any rollout, and the app-server cannot observe a terminal TUI's thread.
- **The state database as source of truth.** The scan stays authoritative (Q4).
- **A desktop/VS Code marker, a live context bar on tiles, notifications for other providers' terminal sessions.**
- **Windows-only behaviour is the target.** POSIX uses the existing fallback rule.

---

## Exploration Discovery
<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

**Step 1.5.** The code-tracing trio was dispatched (directed, subsystem map, data-flow tracer). The in-scope files are predominantly Python and JavaScript source. The reports were checked with read-only probes before the interview. Line numbers below come from those reports and may drift.

**Measurement labels.** Full scan = all 92-93 top-level rollouts or all 644 DB rows. n=1 = one run. All Codex facts are for Codex 0.160.0 on Windows 11 unless stated; `docs/KNOWLEDGE.md` is pinned to 0.159.2.

**Liveness and lock** (`data_codex.py`, `presence.py`, `web.py`):
- `session_writer_locked` (data_codex.py:1528) is a try-lock under `.coordination.lock`; it answers held or not, caches 5 s, and its only consumer is `web._mark_resume_locked` (web.py:304). Lock files are 0 bytes.
- The live rule is `presence._scan` (presence.py:578) plus `web._session_is_live` (web.py:266) with `ACTIVITY_WINDOW` = 300 s. `_PROVIDER_SPECS["codex"]` is argv-only; there is no Codex sidecar, so `reported_status` is always empty.
- `_CODEX_HELPER_SUBCOMMANDS` (presence.py:84) has 34 tokens. Checked against `codex --help` on 0.160.0: every command is covered except `resume` and `fork`, which are meant to be absent.
- Overview loop (c) (overview.py:979) makes every Codex thread in a folder with any Codex process live if written within 300 s.

**Verdicts and notifications** (`status_classifier.py`, `web.py`, `acp.py`, `notifications.py`):
- Verdicts exist only for held ACP sessions: `_acp_status_for_held` (web.py:2524) hard-codes `kiro-cli-v3`; `_acp_availability` returns "available" for any id without a `sess_` prefix (web.py:2508); `_resolved_session_status` returns "working" when the classifier returns None (web.py:319); the rail dot gives a verdict only for held rows (index.html:6498).
- `_classify_from_path` returns None for Codex before reading, because `_read_tail_lines` uses a plain `open()` that blocks Codex's rename and delete (status_classifier.py:500). The classifier cache is keyed on `st_mtime` alone with a 5 s TTL (:460); Windows freezes a held rollout's mtime, so a Codex classifier must key on (mtime_ns, size) as `data_codex` does.
- Notifications originate only in `acp.py` through `_notify` (acp.py:1346) and `web._notify_from_acp` (web.py:474). A poll-shaped notifier was removed on 2026-09-19 because it never ran (notifications.py:1-19); `plans/done/260922-1140_ACP_TURN_END_AND_PERMISSION_NOTIFICATIONS.md` lists reviving it as a non-goal. "Watched" means an attached WebSocket, which a terminal session never has.
- A Codex verdict reader is a fourth reader of the rollout record format (readers today: tile `_codex_events`, `_parse_codex_usage`, the transcript reader). The archived plan's Follow-up 18 names a fourth reader as its reopen trigger. `PARITY_CASES` (tests/test_web.py:33831) pins the three readers on 11 tool-output cases and does not cover `token_count` or `task_started`.

**Usage and context** (`overview.py`):
- `_parse_codex_usage` (:1679) reads `info.total_token_usage` and `info.last_token_usage`, never `model_context_window` (:1787). Context peak is kiro-only: parse at :1402-1413, aggregation `v3_sessions` and `pressure` at :2161-2166, threshold 80.0 at :1197. `_USAGE_SCHEMA` is 6 (:1285; the archived plan's D19 text still says 2). `_usage_roots()` must stay a 3-tuple (:1299; tests at test_web.py:33119). The Codex root follows `data_codex.CODEX_SESSIONS_DIR` through `_codex_usage_root` (:1310).
- Sub-agent exclusion happens in three places: the verdict in the store index (data_codex.py:512), `is_subagent_rollout` (:1021) and `_parse_codex_usage` (overview.py:1716). The UI note "Codex sub-agent threads are not counted" is in D20 and index.html:2043.
- UI text that pins "kiro-cli only": index.html:1924 and :2031, README.md:135, tests/acp_page.test.mjs:17915.

**Repository rules that bind the work:**
- `acp.py` imports only `config`, `launcher._SESSION_ID_RE` and the leaf `permission_rows` (test at test_web.py:26316); `acp.py` and `presence.py` may not import each other (wiring through `web.py` hooks); `data_codex.py` must not import `overview`, `web` or `presence`.
- Every Codex rollout read goes through `data_codex.open_shared` (FILE_SHARE_DELETE). No `sqlite3` import exists in `src/` today.
- Tests: the Codex roots are redirected in autouse fixtures (tests/test_data.py:1723, tests/test_web.py:141) and a guard test iterates a fixed three-root tuple (test_data.py:1865, test_web.py:723), so a new DB path constant joins both. No new test files unless requested. Node page tests are not in CI.
- CRLF working copies: `web.py`, `agent_profile.py`, `data.py`, `launcher.py`, `templates/*.html`, `static/transcript-renderer.js`. Mixed: `status_classifier.py`, `static/composer-chrome.js`. LF: `acp.py`, `data_codex.py`, `overview.py`, `presence.py`. Use the Edit tool.
- `_check_public_ids.py` blocks a commit that adds a real session id. The repository is public: no ids, home paths or user names in plans, docs or tests.
- Python changes need a PowerAtlas restart, which needs a per-task grant. Template, CSS and static-JS changes need only a hard reload.

**Measured facts** (n and method in brackets):
- Idle `codex resume` TUI takes its thread's writer lock and creates a second lock file for a helper thread [n=1]. `psutil.open_files` (about 5 s) and the Windows Restart Manager via `ctypes` (about 1.2 s) both name the TUI process as the holder of the lock file and the rollout [n=1]. After the TUI ended the lock file stayed on disk, unlocked [n=1]. Two stale lock files from older sessions exist [full listing].
- The desktop app's Codex processes are `codex.exe … app-server` with `ChatGPT.exe` as parent, plus an `app-server daemon` helper [process table, n=1]. A lock held by them was not observed.
- A standalone `codex app-server` over stdio accepts requests carrying `"jsonrpc":"2.0"`; its `initialize` result has `codexHome`, `platformFamily`, `platformOs`, `userAgent`; it reported the TUI's thread as `notLoaded` with an empty loaded list; `thread/read` works on a stored thread (keys include `historyMode`, `cliVersion`, `cwd`, parent id, `path` marked unstable); `account/usage/read` returns account totals and 109 daily buckets with a null per-thread field [n=1].
- No approval, permission, elicitation or user-input record type exists in any rollout [full scan, 92 top-level rollouts, all CLI eras]. 82 of 92 end in `task_complete`, 2 in `turn_aborted`, 5 in a message. Record types: `response_item`, `event_msg` (`token_count`, `item_completed`, `task_started`, `task_complete`, `thread_settings_applied`, `turn_aborted`), `turn_context`, `inter_agent_communication_metadata`, `token_usage_record`, `world_state`, `compacted`, `session_meta`.
- Context window is 258,400 in 13,951 events and 272,000 in 6. `last_token_usage.input_tokens / model_context_window` over 13,957 events: median 0.31, p99 0.80, max 0.93, none above 1.0. After 20 of 20 compactions that began at 0.2 of the window or more, the next reading was about 0 [full scan].
- `state_5.sqlite`: 644 rows, 42 columns, WAL mode, 58 migrations (newest: "threads archive sort indexes", "cleanup guardian thread metadata"). `mode=ro` honours the WAL; `immutable=1` ignored it and missed the newest row [n=2]. 537 of 644 `cwd` values carry the `\\?\` prefix; after stripping it 1 of 93 top-level threads still differs (not explained). The 93 unarchived non-sub-agent rows are exactly the 93 top-level rollouts. `archived` matches the folder for 643 of 643. `updated_at_ms` matched the last rollout record within 5 s for 92 of 93. `tokens_used` equals the rollout's final `total_tokens` for 86 of 86 top-level threads. `thread_spawn_edges` (297 rows) agrees with the `source` field of 297 of 297 sub-agent threads. A missing column or table raises `sqlite3.OperationalError`. Cold-process timing: rollout scan 318 ms against 8 ms for a DB query (median of 5).
- Sub-agent tokens: 511 sub-agent rollouts, 246 `guardian`, 297 with a parent edge. In the 14-day window: top-level 37.0 M (4 threads), sub-agent 24.2 M (26 threads: 18.8 M `thread_spawn`, 5.4 M `guardian`). At a sub-agent's first `token_count`, a median 7% of its final total is already present (p25 2%, p75 22%).
- `token_usage_record` exists in only 3 of 92 top-level and 21 of 511 sub-agent rollouts, so it is not a general attribution source.
- Fields populated among the 93 top-level threads: `approval_mode` and `sandbox_policy` 93, `cli_version` 93, `preview` 89, `title` 89, `first_user_message` 88, `model` 84, `name` 46, `git_branch` 13; `is_pinned`, sections and `agent_nickname` 0. `approval_mode` is `never` for 59 and `on-request` for 34.
- Codex has no built-in ACP support: no `acp` command and no ACP feature flag among 154. The app-server protocol (schema generated locally) has `thread/status/changed` (`notLoaded | idle | systemError | active{waitingOnApproval | waitingOnUserInput}`), `thread/tokenUsage/updated` with `modelContextWindow`, and server requests for command, file-change and permission approvals.

**Falsified hypotheses (do not re-derive):**
- "Sub-agent threads hold about 2% of Codex tokens." Wrong twice: the earlier lower bound counted only records that could be windowed, and a later probe filtered on `agent_role`, which is empty for most sub-agent threads.
- "A Waiting state is visible in the rollouts." No such record exists.
- "The app-server can show a terminal TUI's thread status." It reported `notLoaded`.
- "An idle interactive TUI may not hold the lock." It holds it (the archived plan had measured only `codex exec resume`).
- "`immutable=1` is safe for a live WAL database." It misses recent rows.
- "The SQLite cold-scan trigger (about 2 s) is near." The rollout scan takes 0.2-0.3 s; the speed gain is not the reason to use the database.

### 5. Risks & mitigations

| Risk | Mitigation |
|---|---|
| The state database schema changes (58 migrations; a `state_6` is plausible). | Select columns by name, catch `sqlite3.Error`, treat any problem as "no database", re-run the spike checks before shipping, and test the missing-column path. |
| A held SQLite connection or a `-shm` file interferes with Codex (unmeasured). | Short-lived connections (open, query, close), a roughly 30 s cache, and one measurement in the pre-flight phase. |
| The lock-owner rule lights a dot for a desktop or VS Code holder with no helper token. | Measure the desktop-held shape in the pre-flight phase; keep the fall-back rule; the dot classes do not change. |
| Owner lookup is slow (about 1-5 s per call). | Look up once when a lock appears, cache by process id and start time, re-check cheaply, evict when the lock is free; never per poll. |
| "Working" includes a session waiting for approval (34 `on-request` threads). | State it in the UI note; do not label it "Thinking". |
| A verdict reader is a fourth reader of the record format. | Extend `PARITY_CASES` for `task_started`, `task_complete`, `turn_aborted`; key any cache on (mtime_ns, size). |
| Sub-agent totals include inherited context (median 7%, p75 22% at first event). | The note says so; the line is a total with no split. |
| Windows freezes a held rollout's mtime. | Reuse `activity_epoch` and `last_event_epoch` semantics; never key a cache on mtime alone. |
| Version churn (0.159.2 to 0.160.0 within hours; `docs/KNOWLEDGE.md` is pinned to 0.159.2). | Pre-flight re-measurement; re-check the helper deny-list; record the version beside each new fact. |
| Public repository: a real id could enter fixtures or docs. | Synthetic fixtures; the pre-commit hook; no ids in the plan. |
| Python changes need a restart; live QA needs an unlocked screen and a real TUI. | Ask for the per-task restart grant at QA; use `codex exec resume` for lock tests when the screen is locked (recipe in AGENTS.md). |

### 6. Resolved decisions

- Q1: One project file or several child files? — A: "1 project" — Decision: one combined project file, phased.
- Q2: What should driving Codex from `/acp` mean here? — A: "C" (after asking about ACP support; Codex has none built in, an adapter exists) — Decision: out of scope; record the adapter findings and measurements for a later project. The adapter spike was offered and not run.
- Q3: Record items 2 and 3 as not-now? — A: "3: drop. 2: keep, at least as a spike" — Decision: item 3 dropped; item 2 kept as a spike that can ship.
- Q4: If the SQLite spike works, what role should SQLite play? — A: "let's see the results first", then "B" after the spike results — Decision: optional enrichment over the rollout scan; the scan stays the source of truth; fails open.
- Q5: Which verdicts for terminal Codex sessions? — A: "ok" (accepting the recommendation, option A) — Decision: Working or Idle only, from the last record, gated by the lock holder; no Waiting.
- Q6: Notifications for terminal Codex sessions? — A: "ok" (option A) — Decision: a toast per new `task_complete` record, event-shaped, in a separate phase after the verdict.
- Q7: What should the live dot mean? — A: "ok" (option A) — Decision: the lock owner replaces the guess, with a fall-back to today's rule when the lookup fails or off Windows.
- Q8: Where should Codex context pressure appear? — A: "ok" (option A) — Decision: the Overview Usage block only, per-provider rows, Codex labelled as an estimate.
- Q9: How should sub-agent tokens appear? — A: "ok" (option A) — Decision: a separate "Sub-agent threads" line, total only, split into `thread_spawn` and `guardian`.
- Assumptions checkpoint: A: "alright" — Decision: the listed assumptions stand.

### 7. Open items

Deterministic or execution-contingent (none is a decision for the user):
1. The lock-holder shape of the desktop app and VS Code. Execution-contingent: the pre-flight phase needs a desktop-held thread open.
2. Whether a short-lived read-only SQLite connection can interfere with Codex (rename, migration, WAL checkpoint). Settled by one pre-flight measurement.
3. The 1 of 93 top-level thread whose folder still differs from the rollout after normalising. Read the two values and compare.
4. How the owner cache handles process id reuse. Compare process start time; test with a synthetic process.
5. The polling interval of the turn-end notifier and where its background task lives (web lifespan). A judgment for `/qplan`.
6. Whether `_USAGE_SCHEMA` is bumped once for items 6 and 7 or twice. Depends on phase order.
7. POSIX behaviour of the lock owner lookup (`psutil.open_files`). Unverified; the fall-back rule applies.
8. Re-run the `docs/KNOWLEDGE.md` key-path scan against 0.160.0 and diff it (the file says a release that renames record types invalidates it).

### Assumptions (unconfirmed)

- Windows first; POSIX uses the fall-back rule (covers: platform).
- No new runtime dependency and no cost change: `sqlite3`, `ctypes` and `psutil` are already available (covers: external dependencies, cost).
- A new DB path constant joins the autouse test redirects and guard tests; `_usage_roots()` stays a 3-tuple; no new test files (covers: testing).
- The notifier is one background task that follows only lock-held terminal sessions, records its starting offset without notifying, and reuses `notifications.py` and the existing toggle (covers: integration).
- A sub-agent thread is counted on its created day inside the window (covers: data model; accurate for the 26 measured threads, all created in the window).

### 8. Recommended approach

Standard tier, phased. The order puts measurement first and keeps each source independent:
- **Phase 0: pre-flight.** Re-measure on the current Codex version: the desktop-held and VS Code-held lock shape (needs the user's apps), the SQLite interference question, the one remaining folder mismatch, the KNOWLEDGE key-path diff, the Restart Manager and `psutil` timings. A gate revises the design if a premise fails.
- **Phase 1: state-database reader and sub-agent usage** (items 2 and 7). A read-only reader in `data_codex.py` (or beside it), the Usage line, the note text, the schema bump, tests with a synthetic SQLite file.
- **Phase 2: context pressure** (item 6). The per-day peak in `_parse_codex_usage`, the Usage UI row, the schema bump (shared with Phase 1 if they ship together).
- **Phase 3: lock owner and live dot** (item 1). Owner lookup with cache and fall-back, wired through `web.py`.
- **Phase 4: Working/Idle verdict** (item 5a). A Codex reader with a (mtime_ns, size) cache, parity cases, rail and tile wiring.
- **Phase 5: turn-end notifications** (item 5b).
- **Phase 6: documentation, roadmap, cleanup and final QA.**

### 9. QA environment

- Standalone Playwright from the venv (AGENTS.md Verification Setup), signed in with the `pa_local` cookie or a login code. The Chrome-tools route has not been tried against PowerAtlas yet.
- A real interactive TUI needs an unlocked screen: launch `codex resume <throwaway id>` in a new console from a scratch folder whose name ends `codexprobe_work`. When the screen is locked, use `codex exec resume` for lock tests (AGENTS.md).
- A desktop-held or VS Code-held thread needs the user to open one thread in the app.
- The state database is read live from `~/.codex/state_5.sqlite` with `mode=ro`.
- A PowerAtlas restart is needed for Python changes and needs the user's grant for the task.
- Probe scripts used in exploration were throwaway files in the session scratchpad; none is in the repository.

## Harness Improvement Opportunities

- A heredoc passed to the shell tool on Windows halves backslashes, so a probe script containing `\\?\` or `\U` failed to parse (once) and a "fix" replaced text with itself (once) — cost: two extra tool rounds and one wrong commit that had to be corrected — suggested change: `/qexplore`'s Probe gate says to write probe scripts with the file tool, not a heredoc, when the script contains backslashes.
- A derived figure ("sub-agent tokens are about 2%") was reported from a filter (`agent_role` is empty) that stands in for a classification, and a later probe on the real classification (`source`) showed it was about 40% — cost: a wrong number shown to the user and a correction round — suggested change: extend the Probe gate's sample rule: a count taken through a proxy field is cross-checked against the field that actually defines the class before it is shown.
