# PowerAtlas — Roadmap

> Non-executed ideas and future features, organized by theme. Shipped items are removed rather than
> struck through — `git log -- plans/ROADMAP.md` carries their history.
>
> **Paths already investigated and rejected live in `plans/CLOSED_INVESTIGATIONS.md`**, with the
> measurements that decided them and the condition that would reopen each one. Read it before
> proposing `kiro-cli serve`, `_kiro.dev/session/list`, kiro-cli remote control, or a reader for
> kiro-cli's sqlite `conversations_v2` store again.
>
> **Provider measurements live in `docs/KNOWLEDGE.md`** — how kiro-cli, Claude Code and Codex actually
> behave, including findings taken while building things that shipped. Those used to sit here and
> made this file read as a work list with a research appendix stapled to it. The split, in one
> line each: this file is *what to build*, `CLOSED_INVESTIGATIONS.md` is *what not to build again*,
> `docs/KNOWLEDGE.md` is *what is true*.
>
> **Carve-out on that last one, 2026-08-01.** Remote *control* is no longer a single closed question.
> `260731_ACP_REMOTE_CLIENT_PRODUCTIZATION` shipped the half PowerAtlas can own: it drives kiro-cli
> sessions it hosts over ACP and exposes that surface on the NetBird interface behind a device cookie.
> What remains closed is the half that entry is actually about — taking over a session already live in
> someone's terminal, which the session lock still refuses.

---

## Table of Contents

### Priority ranking
- [Ranking table](#where-to-start--ranked-2026-10-12) — 5 items ranked by payoff/effort, with tier and one-line rationale each, plus a positioning note against Kiro Crew and the parallel-agent tools

### Automation & Workflows
- **Dispatch no-interactive tasks** — fire a kiro-cli task without a terminal; `--no-interactive` leaves no session trail so ACP is the right path, but unattended safety is still unsettled
- **Open session with a prompt or skill** — prompt delivery and skill loading are proven; `$ARGUMENTS` expansion re-checked on 2.22.0 and still negative, but a skill can read its arguments off the prompt instead
- **Template prompts** — save reusable per-workspace prompts; build on the read-from-prompt convention rather than `$ARGUMENTS`
- **Scheduled tasks** — cron-like recurring kiro-cli launches; mechanism is measured and process cost is known, but the auto-permissions gate must come first
- **Timed prompts** — a one-shot prompt at a wall-clock time; the quota-reset resume for Claude Code and Codex shipped 2026-10-09, the general case and kiro-cli did not
- **Chained launches** — when a session finishes, automatically start the next one; works for sessions PowerAtlas drives, not for terminal sessions
- **Skills support spike, Claude Code half** — how a Claude Code skill receives its arguments; the kiro-cli half is answered
- **Plan-file shortcuts** — detect `plans/*.md` files and offer one-click `/qdev` buttons; same read-from-prompt convention

### Workspace Intelligence
- **Session status extensions** — the session-level "stale /qdev" heuristic (the Overview's plan `stale` badge covers the plan level), and toasts for kiro-cli and Claude Code terminal sessions (terminal Codex sessions have one)
- **LLM-generated session name alias** — a short generated title where kiro-cli has set none
- **Codex live-status follow-ups** — what `261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB` deferred: a Waiting verdict, a marker for threads held by the desktop app or VS Code, a Codex row in the Status-mode Working bucket, a POSIX lock-owner lookup, a re-diff of the helper deny-list
- **Codex usage follow-ups** — a live context bar on Overview tiles, and a per-day token split for Codex sub-agent threads

### Platform
- **Secret-aware env vars for custom launchers** *(shape a still open)* — credentials in launcher env blocks are in cleartext; serving them was fixed, storing them safely is not yet
- **Electron app** — replace the Python/pywebview stack; not near-term
- **Separate the ACP half into its own module** — the atlas side imports `agent_profile` and `acp` directly today, so retiring or replacing the ACP half would be surgery rather than a delete
- **One shared Codex rollout record reader** — the Working/Idle verdict reader is the fourth reader of the rollout record format, and parity tests are the chosen guard for now against the readers drifting apart
- **Adapter hooks for provider enumeration** — a provider id is spelled out in about 35 places, so each new provider repeats a hand sweep
- **Shared `_normalize_path` UNC gap** — the shared helper can block on an unreachable network share; the Codex adapter already avoids it
- **Env scrub for `launch_custom` and `launch_terminal`** — both still pass `CLAUDE_CODE_*` markers through, on purpose; decide whether to scrub by default

### Session Control & Integration
- **Manual compaction in ACP sessions (kiro-cli v3 compatibility check)** — v3 has no manual compaction over ACP, so the `/compact` palette entry was removed 2026-10-08; check each kiro-cli minor release for a native path
- **Slash commands may be broken on kiro-cli 2.29.1** — `_kiro.dev/commands/execute`, which the palette sends, was refused with `PersistenceClassification` in one probe call; confirm with a real palette command, and measure Stop during a command
- **Resumed-session tool inventory with workflows off** (Low) — the one probe left from the settings alignment
- **Deferred kiro-cli settings** — `tangentMode`, `checkpoint`, `c2s`, `memory`, `disableAutoCompaction`, `_subagent`, `_delegate`; each needs a measurement or PA UI work before it is forwarded
- **Full workflow support, Layers 3 and 4** — a workflow monitor UI and child-session subscriptions; Layers 1-2 and workflow liveness shipped 2026-10-09
- **Workflow follow-ups** (mostly Low) — live check of routed step approvals in Manual, wiring `workflow-cancel` into Stop, `loop_iteration` id reuse, the idle sweeper's 600 s bound, the bookkeeping couplings, and workflows in a loaded session
- **Needs attention residuals** (Low) — unwatched turn ends are not listed, replayed step messages read as user bubbles in the file-based transcript view, and a pending card can be lost to history eviction
- **Clean close (`session/close` on the wire)** — issue the wire call on close; it returns `-32603` on v3, so this waits on kiro-cli
- **Increase session cap to match TUI** — default MAX_SESSIONS=8 (capped at 16) is conservative vs TUI's soft 64; measure v3 per-session cost first
- **Spike: one shared process vs per-process (Kiro Crew model)** — determine whether to stay on the shared process model or adopt per-process isolation
- **Creating a session in a workspace that has none** — cut from the picker because PowerAtlas has no folder browser; two candidate shapes described
- **Reach the operator away from the machine** — the desktop half shipped 2026-09-19; reaching a phone needs a secure context on the remote bind, i.e. the TLS decision
- **Decide permission requests by rule for unattended sessions** — the real Automation keystone and the Auto permission mode's decider; an `ask` rule alone leaves an unattended session waiting until someone answers or presses Stop, rather than deciding
- **A lean dispatch agent** — strip the full interactive-developer context before dispatching a narrow task; ~27k tokens saved when measured on 2.16.0, due a re-measure; now part of the unattended item above
- **A fresh git worktree per session** — a checkbox in the new-session picker; table stakes in Conductor, Crystal and Vibe Kanban, and two `/acp` sessions in one workspace share a working tree today
- **Drive Codex from `/acp` through `@agentclientprotocol/codex-acp`** *(deferred)* — Codex has no ACP support of its own; the adapter's Windows behaviour and `session/list` support are unmeasured
- **Revisit `None` → `"working"` fallback** — unclassifiable sessions show as working; may warrant an explicit "unknown" state now that the fallback fires rarely
- **Reconsider the Yolo default** — in Yolo, Protected emits no rules, so the default posture is Always blocked plus allow-all, weaker than the Manual design suggests
- **Crew verification spikes** — five one-off checks, run in a throwaway VM or Windows profile, each a trigger for reopening the 2026-09-28 keep-and-borrow decision

### Misc
- **[SECURITY — accepted] No NetBird access policy restricts this host** — a decision with its own reopen condition
- **Claude Code sidecar fields inventory** — full table of every field PowerAtlas reads (or could read) from `~/.claude/sessions/<pid>.json`
- **Public-ids hook coverage gaps** — what `_check_public_ids.py` still cannot catch in a public repository: home paths and user names, ids written without hyphens, and commits made by cherry-pick, rebase or revert

---

## Where to start — ranked 2026-10-12

> A ranking by payoff per unit of effort, not a plan. **It is a snapshot and it decays**: every shipped
> or closed item shifts it. Re-rank rather than trusting a stale order — the reasoning for each item
> lives in the item itself, and this table only records the comparison between them. Earlier rankings
> (2026-08-04 and 2026-09-19) are in `git log -- plans/ROADMAP.md`. One rule decides the top of the
> table: a verified defect with a known, small fix outranks any feature.
>
> **Positioning, so the borrowing is deliberate.** Kiro Crew (AWS, open-sourced 2026-08-04) is one
> long-running gateway driving a single `kiro-cli` process over ACP for many sessions — the same shape
> as `acp.py`'s supervisor — and adds persistent memory, scheduled jobs, webhook triggers, chat-surface
> integrations and an app SDK on top. The parallel-agent desktop tools (Conductor, Crystal, Vibe Kanban,
> Claude Squad) converge on worktree isolation per agent, a diff view, and an at-a-glance
> blocked/active/ready board. PowerAtlas is neither: its value is machine-wide visibility over sessions
> it did not start, across four providers (kiro-cli, Claude Code, Kiro IDE and Codex). What it should take from them is narrow — Crew's
> execution-boundary permission model, Crew's single "needs you" view, and the tools' worktree per
> session. Memory, lessons, apps and chat integrations stay out.
>
> **Corrected 2026-09-28, from Crew's source at commit `6a8cb5e`.** The note above understated Crew in
> one respect. Crew is not kiro-only: it ships eight selectable ACP backends, among them Claude Code
> (through `@agentclientprotocol/claude-agent-acp`) and Codex (`src/kiro_crew/agent_sdk/backends.py`
> L207-266). The "machine-wide visibility over sessions it did not start" claim still holds, and more
> firmly: Crew resumes only sessions it created, and its onboarding spec says session and transcript
> import "does not exist, and must not be added back" (`docs/system-specs/modules/onboarding-import.md`
> L661-683). Three more findings bear on any future borrowing:
> - Crew has no native Windows sandbox. Non-Kiro backends run unconfined there by default (`sandbox.py`,
>   `docs/guides/windows-install.md`).
> - Crew's trust and yolo session modes auto-answer `ask` rules, the same way PowerAtlas's Yolo mode
>   emits none (`dashboard/chat_runner.py` L5390-5399).
> - Crew's own sessions are written into kiro-cli's `~/.kiro/sessions` store, the one the atlas parses.
>
> **Decided 2026-09-28: keep and borrow.** The ACP half stays PowerAtlas's own engine. Crew is a
> reference, not a dependency: mine it at Crew release points, not per commit. The user chose this
> after a council of seven options, which voted 4-1 for it; the dissent was "freeze ACP features and
> decide on evidence". The rejected alternatives were migrating to Crew, splitting the atlas from a
> Crew-driven runtime, retiring the ACP half, interposing under Crew, and a plans-and-sessions
> cockpit. Each broke on a concrete finding in Crew's source:
> - the tray, peek overlay and hotkey cannot live in a Crew App;
> - Crew's sessions collide with the atlas's kiro-cli store;
> - a proxy cannot reach Claude Code's ACP stream;
> - Crew's `prompt.md` and lesson injection conflict with the playbook's gates.
>
> Vendoring Crew's backend adapters was also rejected. Three items follow from the decision:
> *Separate the ACP half into its own module* (Platform), *Reconsider the Yolo default* and
> *Crew verification spikes* (Session Control & Integration). **Reopen when** a Crew verification
> spike passes, or when ACP work stops being something the user wants to build.
>
> **What would invalidate it**: the unattended keystone shipping — *Decide permission requests by rule for unattended sessions*, under
> `## Session Control & Integration`, ranked 1 below, which gates the unattended
> `## Automation & Workflows` items (dispatch, scheduled tasks and chained launches); or kiro-cli gaining — or being found to already have — a per-request permission mechanism on
> `acp` + v3, which would restore the keystone to a design problem rather than a question for the
> vendor. The `$ARGUMENTS` re-check has been run (negative, 2026-09-19) and is no longer pending.

| # | Item | Tier | Why here |
|---|---|---|---|
| 1 | *Decide permission requests by rule for unattended sessions* | week | The keystone: it gates dispatch, scheduled tasks and chained launches, and it is the Auto mode decider. The mechanism is proven; a probe of when a queued steer reaches the agent and a bounded wait are still missing. Decide *Reconsider the Yolo default* alongside it. **Skipped for now, 2026-10-12** |
| 2 | *A fresh git worktree per session* | days | Removes the working-tree collision between parallel sessions in one workspace, sharper while Yolo is the default. Decide the rail grouping and the cleanup of finished worktrees before building. **Skipped for now, 2026-10-12** |

**Parked, deliberately**: creating a session in a workspace that has none · secret-aware
custom-launcher env vars (shape (a) — durable, not urgent) · the `None` → `"working"` fallback
revisit · the accepted `[SECURITY]` NetBird item (carries its own reopen condition) · clean close
(waits on kiro-cli: `session/close` returns `-32603` on v3) · the session cap (measure v3 per-session
cost first) · the Electron app.

---

## Automation & Workflows

> Every item here needed one capability PowerAtlas did not have: sending a prompt to an agent
> without a terminal. **PowerAtlas has it as of 2026-08-01** — `260731_ACP_REMOTE_CLIENT_PRODUCTIZATION`
> promoted the prototype to product, so `/acp` creates, resumes, prompts and closes kiro-cli sessions.
> What every item below still turns on is whether dispatch is safe *unattended*; the capability is no
> longer the blocker, the posture is. The findings are recorded per item below, dated 2026-07-26 and
> measured on kiro-cli 2.14.2 unless a bullet says otherwise — several were re-measured on **2.16.0**
> on 2026-07-31 and those re-measurements supersede the pinned figures where they overlap. They come
> from `260725_KIRO_CLI_ACP_CLIENT_PROTOTYPE`, so read them with that
> prototype's boundary in mind: it proved a **chat surface driven by a human watching it**, which is
> what its `-a` posture assumed. Three of the six — fire-and-forget dispatch, scheduled tasks and
> chained launches — are *unattended*, which is the absence of that human, so for those the prototype
> prices the work without deciding it. Where it established nothing, that is said rather than left
> blank.

- **Dispatch no-interactive tasks** — fire a kiro-cli task without a terminal; `--no-interactive` leaves no session trail so ACP is the right path, but unattended safety is still unsettled.
  - *Unattended posture, corrected twice — 2026-09-19* — this bullet first said an unattended session runs with `-a`; that was corrected to "it stalls at its first shell or write request, waiting for a human". **Direct measurement falsified the correction too.** `acp.py` indeed never passes `-a` (the v3 engine rejects it), but nothing else gates the session either *as this machine is configured*: a probe drove shell, read and write — plus a write to an absolute path outside the cwd — with zero `session/request_permission` frames, because `~/.kiro/settings/permissions.yaml` allows every capability. A headless session today does not stall and does not wait; it executes. That is now fixable per session from an agent profile: the interactive permission profile shipped 2026-09-23 in `260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL`, and on 2026-09-25 `260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL` replaced its on/off switch with permission modes: Yolo, the default, runs without asking except for an Always blocked list, and Manual prompts per rules the user edits. Deciding requests with nobody watching is still open, as *Decide permission requests by rule for unattended sessions* under `## Session Control & Integration`.
  - *Exit condition for this item* — *Decide permission requests by rule for unattended sessions* under `## Session Control & Integration` (split out of the interactive permission item, which shipped 2026-09-23 in `260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL`): a per-tool auto-approve rule with deny patterns and a bounded wait. Once that exists, dispatch is a prompt plus a session close on turn end.

- **Open session with a prompt or skill** — prompt delivery and skill loading are proven; `$ARGUMENTS` expansion was re-checked on 2.22.0 (2026-09-19) and is still negative, but a skill can read its arguments off the prompt line instead.
  - *`$ARGUMENTS` — measured on kiro-cli 2.14.2, 2026-07-26.* Slash-command argument passing uses `$ARGUMENTS` in the SKILL.md body (e.g. `/qdev plans/my-plan.md` expands to `$ARGUMENTS` → `plans/my-plan.md`). The expansion is handled by kiro-cli's own command parser, not by PowerAtlas. Whether a prompt string containing `$ARGUMENTS` is expanded by the model or by the CLI is unverified — a test session reliably received the literal string `$ARGUMENTS`. Until this is verified, skill invocations with arguments are not reliably deliverable.
  - *Re-checked 2026-09-19 on kiro-cli 2.22.0 — still negative, and there is a workaround.* Probed over a disposable `acp --agent-engine v3` subprocess with a scratch skill whose body contained `$ARGUMENTS`, invoked as ordinary prompt text (`/qa-argtest <value>`), which is how `/acp`'s palette sends it. The skill loads, but the placeholder is **not** substituted: asked to report what it literally saw, the agent answered `EXPANDED=NO`, with and without an argument.
  - *The naive version of this test gives a false positive — do not repeat it.* Simply asking the skill to echo its arguments returns the right value, because the agent can read it off the user's own prompt line and infer it. Only a skill that asks the agent to report **what it literally sees** at the placeholder discriminates expansion from inference. The first run of this probe passed and was wrong.
  - *Why this is not a hard block.* The argument text does reach the agent — it is right there in the prompt — so a skill authored to read its arguments from the user's message works today, with no CLI-side expansion. The blocker is the `$ARGUMENTS` **mechanism**, not the capability. That is a skill-authoring convention, and it is what *Template prompts* and *Plan-file shortcuts* should be built on rather than waiting for kiro-cli.

- **Template prompts** — save reusable per-workspace prompts. No longer blocked on `$ARGUMENTS`: author the template to read its arguments from the prompt line (see *Open session with a prompt or skill*).

- **Scheduled tasks** — cron-like recurring kiro-cli launches; mechanism is measured and process cost is known, but the auto-permissions gate must come first.
  - *Process cost* — measured 2026-07-26 on kiro-cli v2: each kiro-cli ACP session costs ~161 MB RSS and 3 processes. On **v3**, all sessions share ONE process tree (kiro-cli.exe → bun.exe → node.exe); no per-session processes exist. Close is local only: `session/delete` deletes the session from disk, so it is not used as a close (measured 2026-09-24, see `docs/KNOWLEDGE.md`); idle-TTL releases PowerAtlas's own state for unattended sessions, and an agent with no session for 15 min is stopped, which is what releases kiro-cli's copies.
  - *Auto-permissions gate* — a scheduled task has no human watching, and as configured today nothing prompts, so it runs its tools unsupervised rather than stalling. *Decide permission requests by rule for unattended sessions* under `## Session Control & Integration` is the gate. Its mechanism is measured working (agent-profile `ask` rules, shipped for interactive sessions 2026-09-23 in `260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL`), so this is waiting on that item shipping rather than on an unknown.

- **Timed prompts** — schedule a one-shot prompt for a specific wall-clock time; e.g. "at 11:50am, resume session X with prompt P"; cross-provider (kiro-cli ACP, Claude Code, kiro-cli terminal, Codex terminal); complements *Scheduled tasks* (recurring launches) but distinct: one-shot, prompt-delivery-focused, and works for sessions PowerAtlas does not own.
  - *Quota-limit auto-schedule* — **partly shipped 2026-10-09** in `plans/done/261009-2109_DASHBOARD_OVERVIEW_REHAUL_QUOTA_RESUME_AND_GIT_STATUS.md`, not yet tested live: the Overview lists Claude Code and Codex sessions stopped by a quota limit, with the reset time and Quick resume and Schedule resume buttons (a schedule fires at reset plus 60 seconds; the default prompt is "resume", editable). Still open: kiro-cli, which is detect-and-show only with no resume; defaulting the payload to the last user prompt instead of "resume"; and a general one-shot timed prompt at any wall-clock time, which this does not provide.

- **Chained launches** — when a session finishes, automatically start the next one; works for sessions PowerAtlas drives, not for terminal sessions.

- **Skills support spike, Claude Code half** — understand how a Claude Code skill receives its arguments. The kiro-cli half is answered: `$ARGUMENTS` is not expanded on 2.22.0, and a skill reads its arguments off the prompt line (see *Open session with a prompt or skill*). That answer already unblocks *Template prompts* and *Plan-file shortcuts* for kiro-cli, so this spike matters only once those items are built for Claude Code too.

- **Plan-file shortcuts** — detect `plans/*.md` files and offer one-click `/qdev` buttons. Same unblock as *Template prompts*: the plan path rides the prompt line, not `$ARGUMENTS`.

---

## Workspace Intelligence

- **Session status extensions** — "stale /qdev never completed" heuristics, partly addressed by the Overview's plan `stale` badge, and detecting fresh terminal sessions. Live status dots shipped in `260712_LIVE_SESSION_STATUS`; notifications shipped 2026-09-19. What is left is below.
  - *Stale /qdev detection* — partly addressed by the Overview's plan `stale` badge (no activity > 7 days; activity is the plan's latest slug-scoped commit, else the file's mtime), shipped in `260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE`; the session-level > 24 h marker heuristic remains open. `/qdev` writes a progress marker into the plan file on each phase; a session that last wrote a marker >24 h ago with a non-complete status is "stale". Would require reading plan files on every status poll — expensive. Deferred until status poll performance is better understood.
  - *Notifications for terminal-hosted sessions* — the ACP half shipped, and so did terminal Codex sessions, in `261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB`: a daemon thread watches the rollouts of Codex threads whose writer lock a terminal process holds and sends one toast per new `task_complete` record, through the existing `notifications.enabled` toggle. Terminal sessions of the other providers have no toast, and cannot take the ACP route. PowerAtlas does not host a terminal session, so there is no frame to fire from: for kiro-cli the only signal is the `messages.jsonl` tail the status poll already reads, which makes a poll-side hook the sole option. Nothing reads it today. The Codex notifier is event-shaped (an offset per rollout, one toast per record), not poll-shaped, because the poll-shaped notifier removed on 2026-09-19 never ran. Notifications for the other providers' terminal sessions were left out of the Codex plan on purpose (its Q6, option C).
  - *Fresh terminal sessions* — sessions started in a terminal after PowerAtlas was launched are picked up on the next `refresh_stale_entries` tick (15–30 s). No gap for ACP sessions (PowerAtlas creates them). Terminal-session detection latency is bounded by the refresh interval, not by process monitoring.

- **LLM-generated session name alias** — when kiro-cli has not set a session title, call `claude-haiku-4-6` with the first user message to generate a short, task-oriented label; shown in the rail and transcript panel without a user action. Keeps the existing kiro-cli-written title where one exists; only fills the gap where the title is absent or equals the session id.

- **Codex live-status follow-ups** — what `261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB` shipped (a live dot that follows the thread's lock owner, a Working/Idle verdict, a turn-end toast) left open on purpose. Where the plan names a trigger, the line says when to reopen it.
  - *A Waiting verdict* — a terminal Codex row shows Working or Idle only, and Working can include a session waiting for approval. Measured 2026-10-02 on Codex 0.160.0: none of the 92 top-level rollouts holds an approval, permission, elicitation or user-input record. **Reopen if** Codex starts recording one. The app-server cannot supply it either (see `plans/CLOSED_INVESTIGATIONS.md`, *Codex app-server as a status or discovery source*).
  - *A marker for threads held by the desktop app or VS Code* — such a thread shows no dot, by design: its lock holder is an `app-server` process whose parent is the desktop app or the VS Code extension, which the owner rule reads as `other`. Measured in Phase 0b of the plan (Codex 0.160.0, 2026-10-02). A marker would say "open elsewhere" instead of showing nothing. It was left out when the user chose the lock-owner rule without it; the holders are measured now, so what is left is a UI decision.
  - *A Codex row in the Status-mode Working bucket, and a terminal-aware "active" flag on the workspace header* — the Status-mode buckets mean "held by this PowerAtlas" by decision, so a live terminal Codex row with a verdict stays in Available. The header's "active" flag stays folder based. The plan's D12 and D21 deferred both.
  - *A POSIX lock-owner lookup* — `lock_owner.find_holders` returns None off Windows, and the live dot there follows the older rule (a Codex process in the thread's folder and a record newer than 300 s). POSIX behaviour is unmeasured. The Windows mechanism is the Restart Manager at 0.55 to 0.66 s per lookup (Phase 0b); `psutil.open_files` took about 5 s per lookup and was rejected. **Reopen with** a measured POSIX mechanism.
  - *A re-diff of the helper deny-list when the Codex version changes* — `_CODEX_HELPER_SUBCOMMANDS` (34 tokens) decides which Codex processes count as terminals and bypasses the folder and recency gates, so a new helper subcommand gives false dots until it is added. It was last checked against `codex --help` on 0.160.0: of 27 commands only `resume` and `fork` were absent, both on purpose. The re-diff needs a source for the installed Codex version.

- **Codex usage follow-ups** — left open by `261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB`.
  - *A live context bar on Overview tiles* — Codex context pressure appears only as the Usage page's per-day peak. **Reopen if** that block alone is not enough. Measured in a full scan of 13,957 `token_count` events on 2026-10-02 (Codex 0.160.0 installed): last input tokens over the context window has median 0.31, p99 0.80, max 0.93 and never exceeds 1.0, and after 20 of 20 compactions that began at 0.2 of the window or more the next reading was about 0, so the ratio behaves like context fill.
  - *A per-day token split for Codex sub-agent threads* — the state database holds one lifetime total per thread, so each thread's tokens land on its creation day and the line is not comparable day by day with the top-level rows. A split needs the rollout route, which the plan sized at about 500 tail reads.

---

## Platform

- **Secret-aware env vars for custom launchers** *(shape a still open)* — credentials in launcher env blocks are in cleartext in `config.toml`; shape (a) is an OS keystore reference, shape (b) is an encrypted-at-rest blob. Both require a UI decision about how the user enters/updates credentials.

- **Electron app** — package PowerAtlas as an Electron desktop app, replacing the current Python/pywebview stack. Removes the Python runtime dependency for end users, enables a distributable binary (no venv setup), and gives native access to the Chromium renderer without the pywebview abstraction layer. The web UI (`src/power_atlas/`) is already framework-free HTML/JS/CSS and would port directly; the Python backend logic (`data.py`, `web.py`, `acp.py`, etc.) would need a rewrite in Node.js or a bundled subprocess boundary to be decided at spike time. Not a near-term item — the current stack works and the rewrite cost is substantial.

- **Separate the ACP half into its own module** — make retiring or replacing the ACP half a delete, not surgery. Follows from the 2026-09-28 keep-and-borrow decision (see the positioning note under the ranking).
  - *What exists* — `web.py` imports `agent_profile` at module level and `acp` under a guarded import, and mentions `acp` about 260 times (2026-10-12). That includes the session gate that waits on `agent_profile`'s generation lock and the permission-settings routes. The atlas modules (`data*.py`, `presence.py`, `status_classifier.py`, `launcher.py`, `overview.py`) do not depend on the ACP half. The coupling sits in `web.py`, `index.html` (the dashboard ACP panel) and `config.py`.
  - *Shape* — the ACP routes, the WebSocket handlers and the permission-settings routes move behind one registration call, for example an `acp_web` module that `web.py` mounts if present. The dashboard panel loads as its own script.
  - *Done when* — the atlas starts, serves the dashboard and passes its tests with the ACP modules absent.

- **One shared Codex rollout record reader** — the same Codex record format is parsed in several places, and `data_codex.turn_state` (the Working/Idle verdict, `261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB`) is the fourth reader. It was Follow-up Work 18 of the archived Codex provider plan (`plans/done/261002-1113_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW.md`), which named the fourth reader as the trigger. The trigger is met, and the plan chose parity tests over a merge for now: a parity case compares `turn_state` with the usage parser's agent-time pairing on five record sets, and the new readers match records by key path, never by substring.
  - *Done when* — one reader sits behind all of them, and the parity cases are deleted because there is nothing left to compare.

- **Adapter hooks for provider enumeration** — a provider id is spelled out in about 35 places (launcher tables, web tables, presence specs, overview dispatch, JS maps, CSS and templates), so every new provider repeats the sweep the Codex plan did by hand. Found when the Codex provider was added (`plans/done/261002-1113_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW.md`, Follow-up Work 16). Worth doing before a fifth provider is planned.
  - *Shape* — optional hooks on the adapter module (display name, default colour, helper subcommands, usage parser) so that a registration replaces most of the sites. Which sites can really move is not measured: start from a grep of an existing provider's id.
  - *Done when* — a new provider needs one adapter module and one registration, and a test fails when a site is missed.

- **Shared `data._normalize_path` UNC gap** — the shared helper expands 8.3 short names through `GetLongPathNameW`, which can block on an unreachable network share. Found in the Codex final review and **not reproduced**. The Codex adapter already avoids it by keying a network or device cwd (UNC, `\\?\`, `//`) by case-fold only; the Claude Code and kiro-cli paths still call the helper (Follow-up Work 17 of the same plan).
  - *Shape* — the same guard inside the shared helper, so the adapter-side special case can go.
  - *Done when* — a cwd on an unreachable share does not stall discovery for any provider, shown by a test with a stubbed slow resolver.
  - *Also waiting on this* — `261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB` leaves a network-shaped Codex cwd out of the sub-agent token total and out of the turn-end toasts, failing closed so that the hidden-workspace check never touches the share (its D5 and D13, Follow-up Work 13). Once the gap is fixed those threads can be counted and announced.

- **Env scrub for `launch_custom` and `launch_terminal`** — `_build_child_env` in `launcher.py` strips `CLAUDE_CODE_*` and the other Claude Code markers from provider launches only. Two launch paths were left out on purpose (`plans/done/260818-1432_ACP_ENV_MARKER_AND_OVERLAY_STEERING.md`, Follow-ups #2 and #5):
  - *`launch_custom`* — a user-defined script may rely on the inherited environment, so scrubbing it could break a working launcher.
  - *`launch_terminal`* — opens a bare shell, and the user starts a process inside it by hand.
  - *To decide* — whether either path should scrub by default, with an opt-out per launcher.

---

## Session Control & Integration

- **Manual compaction in ACP sessions (kiro-cli v3 compatibility check)** — kiro-cli v3 does not intercept `_kiro.dev/commands/execute` for "compact" over ACP: the command is forwarded to the model instead of triggering a real compaction. Auto-compaction still works, and its progress and recap rows render in the transcript. The `/compact` palette entry was removed on 2026-10-08 (`56d82ff`) until kiro-cli exposes a native path.
  - *Check each kiro-cli minor release* for a `session/compact` ACP method, or for "compact" reinstated in `available_commands_update` with v3 semantics.
  - *Done when* — such a path exists, the palette entry is back, and it is wired to that path.

- **Slash commands may be broken on kiro-cli 2.29.1** — in a second-process probe on 2026-10-12, `_kiro.dev/commands/execute` `{sessionId, command: {command: "compact", args: {}}}` was refused at once with JSON-RPC `-32603` and `data.details` `[PersistenceClassification] Ext method "_kiro.dev/commands/execute" has no persistence classification`. `commands_execute` in `acp.py` sends exactly this method and shape for every palette entry, so the palette's skill and command entries may all fail on this version. Inferred from one call with `compact`: `orchestrator.log` and its 3 rotations held 0 `PersistenceClassification` lines, so it is not confirmed in production. The same refusal left Stop during a slash command unmeasured; Stop's local fallback (`docs/KNOWLEDGE.md` § "Agent questions", "Stop's local fallback") covers the command wait either way. Found in Phase 1 of `plans/261012_ACP_NEEDS_ATTENTION_QUESTIONS_AND_WORKFLOW_STEP_MESSAGES.md`.
  - *Done when* — a real palette entry (a skill) is run through PowerAtlas on the installed kiro-cli and its result recorded; if the method is refused, the palette uses whatever path kiro-cli now offers or shows the refusal plainly; and Stop during a long command is measured.

- **Resumed-session tool inventory with workflows off** (Low) — the one probe left from the settings alignment (`plans/done/261009-1736_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`, which shipped the dynamic settings block built from `cli.json`; mapping and deferred keys in `docs/KNOWLEDGE.md` § "ACP session/new settings"). The mirror case is unmeasured: a session created with workflows on and loaded with them off.
  - *Done when* — the loaded session's tool list is measured, and `docs/KNOWLEDGE.md` says whether `run_workflow` and `invoke_sub_agent` follow the creation-time or the load-time setting.

- **kiro-cli setting: `tangentMode`** — Enable `chat.enableTangentMode` in PA's dynamic settings sync once PA supports tangent-mode sessions. Currently deferred from `plans/done/261009-1736_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 1 because enabling it may produce new notification types or UI states (sub-sessions, visual indicators) that PA has no handler for. Investigate what tangent mode produces in the TUI, implement PA support, then add to the `w1()` mapping.
  - *Done when* — PA handles all notification types tangent mode produces; a tangent session in PA is visually identifiable and controllable.

- **kiro-cli setting: `checkpoint`** — Enable `chat.enableCheckpoint` in PA's dynamic settings sync once PA supports checkpoint notifications. Deferred from `plans/done/261009-1736_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 1. Checkpoints may create save-point/rollback UI in the TUI that PA would need to surface.
  - *Done when* — PA handles checkpoint notification types; checkpoints are visible in the transcript and actionable.

- **kiro-cli setting: `c2s`** — Enable `chat.enableC2s` (cloud-to-server?) in PA once its behavior and UI requirements are understood. Deferred from `plans/done/261009-1736_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 1. Unknown feature — probe the TUI and KAS bundle to determine what it does before building PA support.
  - *Done when* — Feature is understood, PA handles its notification types, and any required UI is present.

- **kiro-cli setting: `memory`** — Enable `memory.enabled` / `userMemoryOptIn` in PA's dynamic settings sync once PA has a memory panel. Deferred from `plans/done/261009-1736_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 1. Memory tools (indexing, search, recall) produce tool calls that PA renders, but the full memory experience likely requires dedicated UI (memory index browser, opt-in control).
  - *Done when* — PA has a memory panel or equivalent; `memory.enabled` and `userMemoryOptIn` are forwarded from `cli.json`.

- **kiro-cli setting: `disableAutoCompaction`** — Decide whether PA should forward `chat.disableAutoCompaction` from `cli.json` or pin it to a PA-specific value. Deferred from `plans/done/261009-1736_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 1. Sending `disableAutoCompaction: true` would suppress kiro-cli's automatic compaction for PA sessions; may or may not be desirable depending on whether the user wants their PA sessions to auto-compact.
  - *Source* — separate from how compaction rows are replayed to a reconnecting tab; this is about whether auto-compaction fires at all.
  - *Done when* — A deliberate decision is recorded in `docs/KNOWLEDGE.md` and implemented.

- **kiro-cli setting: `_subagent`** — Decide whether PA should forward `chat.enableSubagent` (KAS key `_subagent`) from `cli.json`. Deferred from `plans/done/261009-1736_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 1. PA already renders `_kiro/subagent/list` crew entries, but nobody has audited what this setting changes in what KAS emits. It also interacts with `workflows`, which makes KAS suppress `invoke_sub_agent` (`suppressChatDelegationTool`); that interaction needs investigating before the setting is sent.
  - *Done when* — The setting's effect on tools and notifications is measured, its interaction with `workflows` suppressing `invoke_sub_agent` is understood, and a deliberate decision is recorded in `docs/KNOWLEDGE.md` and implemented.

- **kiro-cli setting: `_delegate`** — Decide whether PA should forward `chat.enableDelegate` (KAS key `_delegate`) from `cli.json`. Deferred from `plans/done/261009-1736_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 1, for the same reason as `_subagent`: its effect on emitted notifications is unaudited, and it interacts with `workflows` suppressing `invoke_sub_agent`, which needs investigating first.
  - *Done when* — The setting's effect on tools and notifications is measured, its interaction with `workflows` suppressing `invoke_sub_agent` is understood, and a deliberate decision is recorded in `docs/KNOWLEDGE.md` and implemented.

- **Full workflow support, Layers 3 and 4** — kiro-cli 2.28.0 introduced `run_workflow`. Layers 1 and 2 shipped in `plans/done/261009-1736_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`. PA sessions receive the tool when `chat.enableWorkflows` is on, and `_Supervisor._on_workflow_notification` turns `node_start`, `node_complete`, `node_paused` and `run_complete` into crew rows, with `run_start` recording the run for the crew header; `run_complete` `status: "paused"` keeps the run's steps waiting, and the run-level `paused` and the other `_kiro/workflow/*` methods are logged only. A step's `send_message` (`_kiro/session/notify`) is handled too, since `plans/261012_ACP_NEEDS_ATTENTION_QUESTIONS_AND_WORKFLOW_STEP_MESSAGES.md`: it sets the row's text, a warning or error becomes a transcript row, and a warning is a step question that sets the step waiting until the parent passes an answer on (measured: the paused run resumes in the same step). Workflow liveness shipped in the same plan: a PA-held parent shows Working while a step runs, and a terminal kiro-cli row keeps its live dot through a silent step. The terminal-row dot is unit-tested only. Known limits are in `docs/KNOWLEDGE.md` and in the Low items below: the `failed` mapping is inferred; a run cancelled from the parent emits no `run_complete`; and a resumed step that reports `success` can end paused again ("Awaiting next user message on step session."), so it reads waiting, with the header "Paused", for up to the 6 h waiting bound, and whether a further parent message completes it is unmeasured.

  **Layer 3 — Workflow monitor UI (/acp and dashboard panel, larger):**
  The TUI has a dedicated `workflow-monitor` mode separate from `crew-monitor`. Workflow nodes have a different shape from crew entries: `{ nodeId, nodePath, branchId, iteration, status, agentName, sessionId }` vs `{ role, task, status }`. PA needs either a unified panel that accepts both shapes or a parallel track. The TUI's inline status line ("handhyg-001-phase… running 0/2 · ctrl+x expand · ctrl+g monitor") is rendered from the workflow's `nodes` state array. The steer/queue send-mode selector already exists in `/acp`'s composer CSS; it needs to be wired to the `_kiro/session/setWorkflowNotificationDelivery` call. Workflow progress is also embedded in `user_message_chunk` frames tagged `_meta.kiro.notification.kind == "workflow-progress"` (replay-only, measured 2026-10-08); decoding them would give basic status on reload, and nothing reads them today.

  **Layer 4 — Child session ACP subscriptions (architecture, later):**
  Each workflow step session is a full `sess_<uuid>/` with its own ACP event stream. PA currently processes one connection per session. To show per-step tool activity and transcript in the panel, PA would need to subscribe to each child session's ACP stream as they appear in `node_start`. Cost: N+1 ACP connections per workflow (parent + all running steps). This intersects with the process-model spike below — if PA moves toward per-session processes, child workflow sessions become first-class PA sessions naturally.

  - *Sources* — workflow child-session investigation 2026-10-11, run-workflow tool registration investigation 2026-10-11 (both under `.agents/tasks/`).
  - *Done when (Layer 3)* — `/acp` and the dashboard panel show a workflow monitor with per-step progress, the steer/queue toggle, and Ctrl+G navigation; parity with TUI's `workflow-monitor` mode.
  - *Done when (Layer 4)* — per-step transcript and tool activity visible in the panel; architecture decision documented.

- **Verify routed workflow step approvals live in Manual mode** (Low) — a workflow step's `session/request_permission` used to be answered `cancelled` (the step's tool call denied, no card). Fixed 2026-10-09 in `acp.py` (`_on_permission_request`, `_workflow_step_parent`; user decision: fix now before archival): a request from a tracked step is routed to the parent's page, attributed to the step in the card title, and answered by the step's own request id. Unit-tested only. **Not verified:** no step has ever raised a permission request in a probe, so it is assumed, not measured, that the request carries the child's `sessionId`, that the answer reaches the step, and that a step under Manual asks at all. Details and limits: `docs/KNOWLEDGE.md` § ACP permission wire shapes, "Workflow step approvals". Found in the review of `plans/done/261009-1736_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 2.
  - *Done when* — in Manual mode with workflows on, a step whose tool call needs approval shows a `[workflow step ...]` card on the parent's page, Allow runs the call and Deny refuses it (with the denial notice and the row's 'tool call denied' text), and the Stop (including Stop with no turn active, which both pages now offer while a card is pending), close and turn-end behaviours in the knowledge doc are observed.

- **Wire `workflow-cancel` so Stop can end a running workflow and stop the lingering step process** — Stop sends `session/cancel` for the parent; it returns `cancelled` at once and no `run_complete` follows (measured 2026-10-08). Live QA 2026-10-09 showed the workflow then stalls (no later step) while the in-flight step's process lingers `in_progress` on disk; by user decision PowerAtlas now treats Stop as ending the workflow (row "stopped", tracking dropped, a transcript note), but it cannot stop that process. **The Stop outcome is condition-dependent** (Low): a second-process probe 2026-10-09 with quick steps ran all three steps to `run_complete` after the same cancel, so wiring `workflow-cancel` is what would make the outcome deterministic; until then the notice says the workflow "may not continue". `docs/KNOWLEDGE.md` has the detail. PowerAtlas has no other route to stop a workflow. kiro-cli lists a `workflow-cancel` slash command in `available_commands_update` (100 commands against 96 without workflows); it was never tried. Found in the Phase 2 review of `plans/done/261009-1736_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`.
  - *Done when* — `workflow-cancel` is measured (does it stop the step, does it emit `run_complete`), and Stop either calls it for a parent with live workflow children or the UI offers a separate stop control. Partial: Stop already marks the rows "stopped", drops the tracking and posts a transcript note (an `agent_error` frame, no client change); that is PowerAtlas's view only and stops no process.

- **A `loop_iteration` that reuses a child id is ignored** — `_on_workflow_node_start` is terminal-sticky, so a repeat id-bearing `node_start` for a finished, non-reaped row changes nothing (now logged at WARNING). `loop_iteration` was never captured, so whether it reuses or mints child ids is unknown. Found in the final review of `plans/done/261009-1736_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`.
  - *Done when* — a looping workflow is run and measured; if it reuses ids, a new iteration reopens the row.

- **The idle sweeper can release a workflow parent whose step is quiet for more than 10 minutes** — `_sweepable` condition 7 uses the supervisor's 600 s read of "live", while the Part B disk path keeps a child `in_progress` for 1800 s. A workflow that has outlived `ACP_IDLE_TTL_SECONDS` with one step quiet for over 10 minutes and no tab attached can have its parent swept. Low probability: the longest measured quiet step is 330 s. The clocks are tabulated in `docs/KNOWLEDGE.md` § Workflow liveness. Found in the final review of `plans/done/261009-1736_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`.
  - *Done when* — a quiet step of 10 to 30 minutes is measured, and either the bounds agree or the sweeper treats a child still `in_progress` on disk as live.

- **Needs attention residuals** (Low) — three gaps left by `plans/261012_ACP_NEEDS_ATTENTION_QUESTIONS_AND_WORKFLOW_STEP_MESSAGES.md`, deferred at its final review. (1) A turn that ended while no tab watched the session is not listed in *Needs attention*: the plan's Intent kept it out because PowerAtlas has no "seen" state, so it could never leave the list; reopen if a seen state is added. (2) The file-based transcript view (a session read from disk rather than through `/ws/acp`) shows a replayed `system-notification` record, a step's `send_message`, as a user bubble; only the live `/acp` path turns it into a step message row. (3) A pending approval or question card can be evicted from the replay history (2000 events or 2 MiB) in a long turn, so a reload no longer shows the card; the attention list reads the pending store, so the item still shows there and clicking it opens a session with no card to answer.

- **Workflow bookkeeping has five hidden couplings** — the crew-row keys `workflow` and `reaped` would be lost if `_on_subagent_list` rebuilt a workflow row; workflow rows ride the legacy `_active_fan_out_wave` filter; there are two crew-order schemes; "is a workflow row" has two markers (`entry['workflow']` and `_workflow_child_meta`); and module functions reach `_workflow_*` privates. Future cleanup, not a defect today: one supervisor method `finalize_stale_workflow_rows` and a single per-child record. Found in the final review of `plans/done/261009-1736_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`; the couplings are listed in `memory/MEMORY.md` under `_workflow_children`.
  - *Done when* — the parallel dicts are one record per child and the stale-row finalisation lives in one method.

- **A workflow running in a loaded session is not tracked** — `session/load` replays `workflow-progress` as `user_message_chunk` frames only; it never re-sends live `_kiro/workflow/*` frames, and PowerAtlas deliberately ignores those chunks. A workflow still running when a session is resumed therefore has no crew rows and no liveness until its next live frame (a `node_complete` or `run_complete` for an untracked child is ignored). Found in the Phase 2 review of `plans/done/261009-1736_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`.
  - *Done when* — resuming a session with a running workflow shows its steps (Layer 4 of Full workflow support, or a rebuild from `session.json` of the child sessions).

- **Clean close (`session/close` on the wire)** — PA's close is local-only: it pops the session from its own state and broadcasts `session_closed`, but never issues `session/close` to kiro-cli. kiro-cli TUI does issue it. The kiro-cli process keeps closed sessions in memory until the agent is recycled (15 min after the last session is gone, `AGENT_IDLE_RECYCLE_SECONDS`). For a user closing and re-opening many sessions in one PA run, memory grows silently. The fix: issue `session/close` after removing the session from PA's own state.
  - *Why it was left out* — `session/close` returns `-32603` on v3 (measured 2026-09-24, `docs/KNOWLEDGE.md` § "Session close — no v3 method unloads..."), so the call has no protocol effect at present. The item is open for when kiro-cli exposes a proper unload path, or as a speculative call (harmless -32603, but kilo-cli can change behavior later).
  - *What to check before building* — confirm whether `session/close` semantics changed in current kiro-cli builds, and whether the call now has an effect without deleting history.

- **Increase session cap to match TUI** — the current default `MAX_SESSIONS = 8` (range 1-16) is more restrictive than the TUI's soft cap of 64 inactive conversations. A user who routinely has >8 kiro-cli sessions hits the PA limit in ways they would never hit in the TUI. Source of the 8: process-cost measurement on v2 (~161 MB/session); on v3, all sessions share one process tree, so the per-session cost is much lower. A reasonable new default is 16-32; the `acp_max_sessions` config key exists for per-machine tuning, but `_clamped` caps it at 16, so the range has to move with the default.
  - *What to measure before changing the default* — current v3 per-session memory overhead (particularly how `history` and `subagent_history` grow under load), and whether the `MAX_CONNECTIONS = 8` WebSocket cap should move in tandem.

- **Spike: one kiro-cli process (current) vs one process per session (Kiro Crew model)** — PA holds all sessions on a single shared `kiro-cli acp` process; Kiro Crew spawns a dedicated process per session (warm pool). One-process keeps overhead low and lets PA reclaim the agent after 15 min idle; per-process gives hard isolation (a crashed session doesn't affect others) and natural teardown on close. The TUI shares one process with PA's model. The spike should answer: what is the real isolation failure rate of the shared model, what is the memory and startup cost of per-process on v3, and which model better supports future unattended dispatch?
  - *Source* — multi-session comparison investigation, 2026-10-06 (`plans/.agents/tasks/acp-multi-session-comparison.md`).
  - *Done when* — a written recommendation with measured costs and a failure-mode analysis, committed to `plans/CLOSED_INVESTIGATIONS.md` or promoted to a plan.

- **Creating a session in a workspace that has none** — cut from the picker because PowerAtlas has no folder browser; two candidate shapes described.
  - *Shape A* — an inline text field in the "new session" dialog for entering a path manually. Simple, but not discoverable for paths the user doesn't have memorized.
  - *Shape B* — a separate "add workspace" flow that opens a native folder browser and writes the path into `config.toml` as a pinned folder. More discoverable but adds a new surface.

- **Reach the operator away from the machine** — the desktop half shipped 2026-09-19 (`plans/done/`); what remains is the phone, and it is parked behind the TLS decision on measured rather than assumed grounds.
  - *The limit is measured, and stricter than this item used to assume.* The old wording accepted losing the phone case to a **sleeping** tab. Measured on the live deployment: on `http://<netbird-ip>:4915` the page is not a secure context, so `Notification.permission` reads `"denied"` — hard-denied, unpromptable, no user gesture can rescue it — and `navigator.serviceWorker` is absent. An **awake** remote tab cannot notify either, and Web Push is independently foreclosed. Both shipped surfaces reach the machine running PowerAtlas and nothing else.
  - *What it would take* — a secure context on the remote bind, nothing less: Web Push needs HTTPS and so does the plain `Notification` API. The reasoning against TLS holds only while WireGuard is the sole remote path; the cost of that choice is now precisely known rather than estimated.

- **Decide permission requests by rule for unattended sessions** — the real Automation keystone, split out on 2026-09-21 of the interactive permission-policy item (shipped 2026-09-23 in `260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL`). When nobody is watching, a `session/request_permission` must be *decided*, not parked: approve read-class tools, deny shell and write unless an explicit pattern allows them, and after a bounded wait cancel the turn rather than leaving it parked until someone answers or presses Stop. (Marking the session as needing attention shipped: an open request is listed in the dashboard's "Needs attention" and reads Waiting.)
  - *Why it is separate* — the interactive item gave the agent a mouth; this one gives PowerAtlas an answer. Without it an unattended session still stalls, so **this** is what the six `## Automation & Workflows` items actually wait on.
  - *What it inherits* — the whole mechanism is already proven: an agent-profile `ask` rule fires a real `session/request_permission` (measured 2026-09-21, `scope: agent`, `source: agent-profile`), and `_meta.kiro.consent` carries `capability`, `resource` and `matchedRule`, which is exactly the input a rule engine needs to decide on. The answering path (`_handle_permission_response`) already validates ownership and single-resolution.
  - *What it must decide* — the allow/deny pattern set, and whether "unattended" is a property of the session, the dispatch, or the agent definition. Also where the bounded wait lives: no timer of any kind ends a pending request today (only Stop, close, a restart or the 24 h turn ceiling end it), so the bound must be request-specific.
  - *Auto mode is where this lands — 2026-09-25.* `260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL` shipped the pattern set as Manual mode (a rule editor, and "Allow, and always in new sessions…" on prompt cards) and built the Auto slot, shown disabled as "coming soon — behaves like Manual". Auto's decider is this item. Its recorded design (that plan's D-5): answer Deny, send the reason to the agent as a steer message, and escalate to the user when the agent repeats the request. It can only decide `ask` prompts: Always blocked rules are refused by kiro-cli before any prompt exists. It still needs a probe of when a queued steer reaches the agent. The bounded wait is still missing too: an unanswered prompt waits until someone answers it or presses Stop.
  - *It also absorbs the lean dispatch agent* (below). Narrowing `resources:` is right for a dispatched narrow task and wrong for an interactive session, so that folding belongs here rather than in the interactive item.

- **A lean dispatch agent** — strip the full interactive-developer context before dispatching a narrow task; saves ~27k tokens per session (measured). As of 2026-09-19 this is the agent-definition half of the permission-policy work, not a separate deliverable; since the interactive item shipped (2026-09-23, `260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL`) it belongs to *Decide permission requests by rule for unattended sessions* above, which absorbs it: the same agent file that narrows `resources` is where the tool allow-list lives.
  - *What is measured* — a `resources: []` agent costs ~46k tokens (the floor from cwd-driven context), versus ~73k for `kiro_default`. The delta is ~27k tokens, confirmed on kiro-cli 2.16.0. **Stale-when**: kiro-cli moves off 2.16.0. **That has fired**: the installed build was 2.29.1 on 2026-10-12, so re-measure before building on this figure.
  - *One open question* — whether skills the dispatched task invokes (e.g. `/qplan`, `/qdev`) still load correctly with a stripped `resources` list. Untested; the skills themselves arrive via `skill://` resolvers and may not depend on the resources list.

- **A fresh git worktree per session** — a checkbox in the new-session picker that creates `git worktree add` under the workspace and starts the session there.
  - *Why* — worktree isolation per agent is the one feature Conductor, Crystal, Vibe Kanban and Claude Squad all share. Two `/acp` sessions created in the same workspace today edit the same working tree, and nothing warns.
  - *What it touches* — the picker (`acp.html` and the dashboard copy shipped 2026-09-19), `_handle_new`/`new_session` for the cwd, and the rail: a worktree path is a distinct cwd, so it becomes its own workspace group in the rail and in kiro-cli's session store unless grouped back under its parent. Decide the grouping before building; cleanup of finished worktrees is a second decision.

- **Drive Codex from `/acp` through `@agentclientprotocol/codex-acp`** *(deferred)* — make `/acp` start, resume and prompt Codex threads, as it does for kiro-cli. The user decided it out of scope while planning `261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB` (its D2, Follow-up Work 1); this entry keeps what was found.
  - *Measured, 2026-10-02, Codex 0.160.0 on Windows 11* — Codex has no built-in ACP support: no `acp` command, and no ACP feature flag among its 154.
  - *Stated for the adapter, not run* — `@agentclientprotocol/codex-acp` 2.1.1 is a Node package under the Apache-2.0 licence, maintained under the ACP organisation. It starts the Codex App Server and supports `session/new`, `session/load`, permission requests and four modes. PowerAtlas has not installed or run it.
  - *Unknown* — whether it runs on Windows, whether it supports `session/list`, and how a session it drives would appear in the Codex store and the live dot. All three are unmeasured.
  - *What it would cost* — the supervisor in `acp.py` is kiro-cli-specific, so this needs a driver abstraction, and a permission model for Codex's approval policy and sandbox. Always blocked and Protected are compiled into kiro-cli's derived agent and would not reach a Codex session either.
  - *Reopen when* — the adapter's Windows behaviour and its `session/load` are measured. A spike, as for Claude Code, would exit with a go or no-go.

- **Revisit `None` → `"working"` fallback** — unclassifiable sessions show as working; may warrant an explicit "unknown" state now that the fallback fires rarely.

- **Reconsider the Yolo default** — Yolo is the default permission mode (`acp_permission_mode = "yolo"` in `config.py`), and in Yolo `compile_block` in `agent_profile.py` returns the Always blocked rules plus one `all: allow` before any Protected rule is emitted. So Protected folders ask only in Manual, and the posture most sessions actually run under is Always blocked plus allow-all. Found by the 2026-09-28 council, where it cut both ways: PowerAtlas's security edge over Kiro Crew is smaller than the Manual design suggests.
  - *To decide* — whether the default should become Manual, or Yolo should keep the Protected rules. The cost of each is prompt volume in everyday sessions. The deliberate choice recorded in `load_config` (an unreadable config falls back to defaults) must still hold.

- **Crew verification spikes** — five one-off checks, each a trigger for reopening the 2026-09-28 keep-and-borrow decision. Not a trial and not a freeze. Run them in a throwaway VM or a separate Windows user profile: installing Crew writes about 10 agent files into the shared `~/.kiro/agents` and injects MCP servers.
  1. A phone-driven kiro-cli session works on Windows through Tailscale or cloudflared.
  2. A kiro-cli session under Crew is refused a read of a planted dummy `~/.ssh` file. This measures what kiro-cli's built-in sandbox enforces on Windows, which nobody has established.
  3. A project skill loads in a Windows project through kiro-cli's native `skill://` loader, given the per-session cwd Crew passes.
  4. Playbook skills run when invoked as `$name` with Crew's lesson injection off, and the playbook's gates still win over Crew's `prompt.md`.
  5. A hooks-only App can list sessions from outside Crew on Windows.

---

## Misc

- **[SECURITY — accepted, 2026-08-03] No NetBird access policy restricts this host, and that is now a decision rather than an oversight.** Measured 2026-07-31: `netbird status -d` enumerates **all 17** account peers in this host's network map, including machines belonging to other people (`akita`, `paros-g`, `nuc-chicago`, `ec2amaz-tv495hp`, `macbook-air-de-polestar`, …), so the stock `Default` (All → All) policy is still enabled.
  - *Measured 2026-08-03* — all inbound File and Printer Sharing rules (TCP 139, TCP 445) are **disabled** at the Windows Firewall level. The genuine exposure was UDP 137/138 (Network Discovery), admitted by two rules scoped to the Private profile. The WireGuard tunnel is classified Private, so those rules apply to NetBird peers.
  - *Decision* — the device cookie is the sole authorization layer for `/acp`. Creating a NetBird access policy scoped to this host's own devices restores the intended second layer (5 minutes in the NetBird console). The implementation does not depend on that policy being in force, so it is worth doing but not blocking.

- **Claude Code sidecar fields inventory** — full table of every field PowerAtlas reads (or could read) from `~/.claude/sessions/<pid>.json`.

- **Public-ids hook coverage gaps** — this repository is public, and its pre-commit and commit-msg hooks (`_check_public_ids.py`) block a session id found in a local session store. The Codex plan (`261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB`, Follow-up Work 11) hardened them and left these open by the user's scope choice on 2026-10-05.
  - *What it cannot see* — home paths and user names; an id whose session was already deleted; an id that exists only in Codex's state database; ids written without hyphens; the stores it does not walk (Claude Code `todos`, `file-history`, `session-env`, `shell-snapshots` and `debug`; Codex `shell_snapshots`, `log` and `thread_history_*.sqlite`; the Kiro IDE `workspace-sessions` folder).
  - *What skips both hooks* — cherry-pick, rebase and revert. A `pre-push` check over the pushed range would cover them.
  - *Fails open, with a warning* — the 64 MiB wide-blob cap, a `git cat-file` timeout and an unreadable store.
  - *Done when* — a home path or user name in an added line blocks the commit, and the hook set covers a push of rebased commits. `_check_public_ids_scenarios.py` is where each new case goes.
