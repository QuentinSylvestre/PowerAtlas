# PowerAtlas — Roadmap

## Table of Contents

### Priority ranking
- [Ranking table](#where-to-start--ranked-2026-09-19) — 5 items ranked by payoff/effort, with tier and one-line rationale each, plus a positioning note against Kiro Crew and the parallel-agent tools

### Automation & Workflows
- **Dispatch no-interactive tasks** — fire a kiro-cli task without a terminal; `--no-interactive` leaves no session trail so ACP is the right path, but unattended safety is still unsettled
- **Open session with a prompt or skill** — prompt delivery and skill loading are proven; `$ARGUMENTS` expansion re-checked on 2.22.0 and still negative, but a skill can read its arguments off the prompt instead
- **Template prompts** — save reusable per-workspace prompts; build on the read-from-prompt convention rather than `$ARGUMENTS`
- **Scheduled tasks** — cron-like recurring kiro-cli launches; mechanism is measured and process cost is known, but the auto-permissions gate must come first
- **Chained launches** — when a session finishes, automatically start the next one; works for sessions PowerAtlas drives, not for terminal sessions
- **Skills support spike** — understand how argument passing works in both kiro-cli and Claude Code, unblocking three items above
- **Plan-file shortcuts** — detect `plans/*.md` files and offer one-click `/qdev` buttons; same read-from-prompt convention

### Workspace Intelligence
- **Session status extensions** — "stale /qdev never completed" heuristics, partly addressed by the Overview's plan `stale` badge, and detecting fresh terminal sessions (live status dots and notifications both shipped; terminal Codex sessions have a turn-end toast, kiro-cli and Claude Code terminal sessions do not)
- **Codex live-status follow-ups** — what `261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB` deferred: a Waiting verdict, a marker for threads held by the desktop app or VS Code, a Codex row in the Status-mode Working bucket, a POSIX lock-owner lookup, a re-diff of the helper deny-list
- **Codex usage follow-ups** — a live context bar on Overview tiles, and a per-day token split for Codex sub-agent threads

### Platform
- **Secret-aware env vars for custom launchers** *(shape a still open)* — credentials in launcher env blocks are in cleartext; serving them was fixed, storing them safely is not yet
- **Parked items** — creating a session in a workspace with no prior sessions · two SECURITY items
- **Separate the ACP half into its own module** — the atlas side imports `agent_profile` and `acp` directly today, so retiring or replacing the ACP half would be surgery rather than a delete
- **Adapter hooks for provider enumeration** — a provider id is spelled out in about 35 places, so each new provider repeats a hand sweep
- **Shared `_normalize_path` UNC gap** — the shared helper can block on an unreachable network share; the Codex adapter already avoids it
- **One shared Codex rollout record reader** — the Working/Idle verdict reader is the fourth reader of the rollout record format, and parity tests are the chosen guard for now against the readers drifting apart
- **`launch_custom` env scrub excluded (follow-up)**: CLAUDE_CODE_* markers are not scrubbed from `launch_custom`-launched sessions — user-defined scripts may rely on inherited environment. See `plans/done/260818_ACP_ENV_MARKER_AND_OVERLAY_STEERING.md` Follow-up #2.
- **`launch_terminal` env scrub excluded (follow-up)**: `launch_terminal` (~`launcher.py:595`) opens a bare shell without env scrubbing — the user manually starts a process inside it. Follow-up #5 of the same plan.

### Session Control & Integration
- **Manual compaction in ACP sessions (kiro-cli v3 compatibility check)** — kiro-cli v3 KAS does not intercept `_kiro.dev/commands/execute` for "compact" via ACP; the command is forwarded to the agent model instead of triggering real compaction. Auto-compaction still works. The `/compact` palette entry has been removed until a kiro-cli version exposes a native ACP compaction path. **Check each kiro-cli minor release for a `session/compact` ACP method or reinstatement of "compact" in `available_commands_update` with v3 semantics.** When found, reinstate the palette entry and wire it to the new path.
- **Align PA ACP session settings with kiro-cli** — systematic audit of `_meta.kiro.settings` keys PA omits vs TUI; `workflows.enabled` was the first gap found, likely more. **In progress** (`plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`): Phases 1-3 implemented and the final review fixes landed (dynamic settings block from `cli.json`; `workflows`/`goal` always follow `chat.enableWorkflows`, the old build gate is gone); Probe C is resolved (docs/KNOWLEDGE.md); the live `run_workflow` check in a PA session passed 2026-10-09; the mirror case of the resumed-session tool inventory (created with workflows on, loaded with none) is still unmeasured
- **Deferred kiro-cli settings** — `tangentMode`, `checkpoint`, `c2s`, `memory`, `disableAutoCompaction`; each needs PA UI/UX work before enabling; tracked as individual items
- **Full workflow support** — four layers: enable `run_workflow` tool on session/new; handle `_kiro/workflow/*` notifications in acp.py; workflow monitor UI; child session subscriptions. **In progress** (`plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`): Phases 1-3 implemented; live QA passed 2026-10-09 (Phase 2 handlers and row-keeping; Phase 3 rail, availability and Overview tile overlay, and the revert to waiting after the wake turn once the classifier fix landed). Leftovers are the Low items below
- **Workflow liveness** — sessions that launched a workflow show as idle; liveness should be the max of parent + running child sessions (rootConversationId correlation). **Part A (PowerAtlas-held sessions) code complete** under `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 3; Part B (terminal kiro-cli rows: live dot and Overview tile only, no Working verdict) **code complete** in the same phase, see the entry below
- **Clean close (`session/close` on the wire)** — issue the wire call on close; currently local-only, kiro-cli keeps sessions in memory until agent recycle
- **Increase session cap to match TUI** — default MAX_SESSIONS=8 is conservative vs TUI's soft 64; v3 single-process model makes per-session cost much lower
- **Spike: one shared process vs per-process (Kiro Crew model)** — determine whether to stay on the shared process model or adopt per-process isolation
- **Creating a session in a workspace that has none** — cut from the picker because PowerAtlas has no folder browser; two candidate shapes described
- **Reach the operator away from the machine** — the desktop half shipped 2026-09-19; reaching a phone needs a secure context on the remote bind, i.e. the TLS decision
- **Decide permission requests by rule for unattended sessions** — the real Automation keystone, split out 2026-09-21; an `ask` rule alone leaves an unattended session waiting on the 30-minute silence ceiling rather than deciding
- **A lean dispatch agent** — strip the full interactive-developer context before dispatching a narrow task; saves ~27k tokens per session (measured); now the agent-definition half of *Decide permission requests by rule for unattended sessions* (the interactive permission item shipped 2026-09-23 in `260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL`)
- **A "needs you" inbox** — one cross-session list of pending permission requests, unanswered clarifying questions and finished turns, borrowed from Kiro Crew's activity view
- **A fresh git worktree per session** — a checkbox in the new-session picker; table stakes in Conductor, Crystal and Vibe Kanban, and two `/acp` sessions in one workspace share a working tree today
- **Spike: drive Claude Code over ACP through an adapter** — would make `/acp` multi-provider; Kiro Crew already does this (corrected 2026-09-28), so it is a capability, not a differentiator; not covered by the closed takeover investigations, which concern sessions already live in a terminal
- **Drive Codex from `/acp` through `@agentclientprotocol/codex-acp`** *(deferred)* — Codex has no ACP support of its own; an adapter exists, but its Windows behaviour and `session/list` support are unmeasured, and it needs a driver abstraction and a Codex permission model
- **Revisit `None` → `"working"` fallback** — unclassifiable sessions show as working; may warrant an explicit "unknown" state now that the fallback fires rarely
- **Reconsider the Yolo default** — in Yolo, Protected emits no rules, so the default posture is Always blocked plus allow-all, weaker than the Manual design suggests
- **Crew verification spikes** — five one-off checks, run in a throwaway VM or Windows profile, each a trigger for reopening the 2026-09-28 keep-and-borrow decision
- **Expose the effort level** — every ACP session now starts at `max`; the agent offers low, medium, high and max, but `/acp` has no control to change it
- **[P2b] Session stores PowerAtlas cannot see** — closed; sqlite `conversations_v2` sessions permanently inaccessible post-v2-removal (2026-09-17); v3 covered

### Misc
- **[SECURITY] `/partials/launchers` leaks custom-launcher `env`** — any signed-in local caller's GET returns the credentials `_launchers_without_env` exists to strip, because the tile partial renders them after all (the loopback gate shipped in `260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL` removed anonymous access, not the leak)
- **Claude Code sidecar fields inventory** — full table of every field PowerAtlas reads (or could read) from `~/.claude/sessions/<pid>.json`
- **Public-ids hook coverage gaps** — what `_check_public_ids.py` still cannot catch in a public repository: home paths and user names, ids written without hyphens, and commits made by cherry-pick, rebase or revert


- **Adapter hooks for provider enumeration** — a provider id is spelled out in about 35 places (launcher tables, web tables, presence specs, overview dispatch, JS maps, CSS and templates), so every new provider repeats the sweep the Codex plan did by hand. Found when the Codex provider was added (`plans/done/261002-1113_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW.md`, Follow-up Work 16). Worth doing before a fifth provider is planned.
  - *Shape* — optional hooks on the adapter module (display name, default colour, helper subcommands, usage parser) so that a registration replaces most of the sites. Which sites can really move is not measured: start from a grep of an existing provider's id.
  - *Done when* — a new provider needs one adapter module and one registration, and a test fails when a site is missed.

- **Shared `data._normalize_path` UNC gap** — the shared helper expands 8.3 short names through `GetLongPathNameW`, which can block on an unreachable network share. Found in the Codex final review and **not reproduced**. The Codex adapter already avoids it by keying a network or device cwd (UNC, `\\?\`, `//`) by case-fold only; the Claude Code and kiro-cli paths still call the helper (Follow-up Work 17 of the same plan).
  - *Shape* — the same guard inside the shared helper, so the adapter-side special case can go.
  - *Done when* — a cwd on an unreachable share does not stall discovery for any provider, shown by a test with a stubbed slow resolver.
  - *Also waiting on this* — `261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB` leaves a network-shaped Codex cwd out of the sub-agent token total and out of the turn-end toasts, failing closed so that the hidden-workspace check never touches the share (its D5 and D13, Follow-up Work 13). Once the gap is fixed those threads can be counted and announced.
---

> Non-executed ideas and future features, organized by theme. Shipped items are removed rather than
> struck through — `git log -- plans/ROADMAP.md` carries their history.
>
> **Paths already investigated and rejected live in `plans/CLOSED_INVESTIGATIONS.md`**, with the
> measurements that decided them and the condition that would reopen each one. Read it before
> proposing `kiro-cli serve`, `_kiro.dev/session/list`, or kiro-cli remote control again.
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

## Where to start — ranked 2026-09-19

> A ranking by payoff per unit of effort, not a plan. **It is a snapshot and it decays**: every shipped
> or closed item shifts it. Re-rank rather than trusting a stale order — the reasoning for each item
> lives in the item itself, and this table only records the comparison between them. The previous
> ranking (2026-08-04) is in `git log -- plans/ROADMAP.md`; it was retired because its keystone rested
> on a premise the code no longer has (that sessions run with `-a`; see "The kiro-cli permission
> model" in `docs/KNOWLEDGE.md`). The 2026-09-19 ranking opened with the
> notification item; it shipped the same day and has been removed from the table. Items 1 and 2,
> the interactive permission policy and the `[SECURITY]` loopback token, shipped together on
> 2026-09-23 in `260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL` and have been removed too.
> The table has not been re-ranked since.
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
> `## Session Control & Integration`, which is not in this table and gates all six
> `## Automation & Workflows` items; or kiro-cli gaining — or being found to already have — a per-request permission mechanism on
> `acp` + v3, which would restore the keystone to a design problem rather than a question for the
> vendor. The `$ARGUMENTS` re-check has been run (negative, 2026-09-19) and is no longer pending.

| # | Item | Tier | Why here |
|---|---|---|---|
| 3 | *A fresh git worktree per session* | days | A checkbox in the picker that shipped 2026-09-19; removes the working-tree collision between parallel sessions in one workspace. Sharper now that an ungated agent writes wherever it likes |
| 4 | *A "needs you" inbox* | days | Status grouping already buckets Working/Waiting/Errored; this adds clarifying questions across every session in one place. Now also where a notification lands you — the toast tells you *a* session needs you, not *which*. Note the "pending permissions" half may have nothing to show (item 2) |
| 5 | *Spike: Claude Code over ACP through an adapter* | week | ~~The one thing Kiro Crew structurally cannot do~~ — false, corrected 2026-09-28: Crew already drives Claude Code over ACP, so this is worth doing only as a capability PowerAtlas wants for itself. A spike, not a commitment: the supervisor is kiro-cli-specific in its `initialize`/`session/new` shapes and would need a second driver |

**Parked, deliberately**: creating a session in a workspace that
has none · secret-aware custom-launcher env vars (shape (a) — durable, not urgent) · the `None` →
`"working"` fallback revisit · the accepted `[SECURITY]` NetBird item (carries its own reopen
condition).

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

- **Open session with a prompt or skill** — prompt delivery and skill loading are proven; passing skill arguments (`$ARGUMENTS`) was negative on 2.14.2 and is due a one-prompt re-check on the current build.
  - *`$ARGUMENTS` — measured on kiro-cli 2.14.2, 2026-07-26.* Slash-command argument passing uses `$ARGUMENTS` in the SKILL.md body (e.g. `/qdev plans/my-plan.md` expands to `$ARGUMENTS` → `plans/my-plan.md`). The expansion is handled by kiro-cli's own command parser, not by PowerAtlas. Whether a prompt string containing `$ARGUMENTS` is expanded by the model or by the CLI is unverified — a test session reliably received the literal string `$ARGUMENTS`. Until this is verified, skill invocations with arguments are not reliably deliverable.
  - *Re-checked 2026-09-19 on kiro-cli 2.22.0 — still negative, and there is a workaround.* Probed over a disposable `acp --agent-engine v3` subprocess with a scratch skill whose body contained `$ARGUMENTS`, invoked as ordinary prompt text (`/qa-argtest <value>`), which is how `/acp`'s palette sends it. The skill loads, but the placeholder is **not** substituted: asked to report what it literally saw, the agent answered `EXPANDED=NO`, with and without an argument.
  - *The naive version of this test gives a false positive — do not repeat it.* Simply asking the skill to echo its arguments returns the right value, because the agent can read it off the user's own prompt line and infer it. Only a skill that asks the agent to report **what it literally sees** at the placeholder discriminates expansion from inference. The first run of this probe passed and was wrong.
  - *Why this is not a hard block.* The argument text does reach the agent — it is right there in the prompt — so a skill authored to read its arguments from the user's message works today, with no CLI-side expansion. The blocker is the `$ARGUMENTS` **mechanism**, not the capability. That is a skill-authoring convention, and it is what *Template prompts* and *Plan-file shortcuts* should be built on rather than waiting for kiro-cli.

- **Template prompts** — save reusable per-workspace prompts. No longer blocked on `$ARGUMENTS`: author the template to read its arguments from the prompt line (see *Open session with a prompt or skill*).

- **Scheduled tasks** — cron-like recurring kiro-cli launches; mechanism is measured and process cost is known, but the auto-permissions gate must come first.
  - *Process cost* — measured 2026-07-26 on kiro-cli v2: each kiro-cli ACP session costs ~161 MB RSS and 3 processes. On **v3**, all sessions share ONE process tree (kiro-cli.exe → bun.exe → node.exe); no per-session processes exist. Close is local only: `session/delete` deletes the session from disk, so it is not used as a close (measured 2026-09-24, see `docs/KNOWLEDGE.md`); idle-TTL releases PowerAtlas's own state for unattended sessions, and an agent with no session for 15 min is stopped, which is what releases kiro-cli's copies.
  - *Auto-permissions gate* — a scheduled task has no human watching, and as configured today nothing prompts, so it runs its tools unsupervised rather than stalling. *Decide permission requests by rule for unattended sessions* under `## Session Control & Integration` is the gate. Its mechanism is measured working (agent-profile `ask` rules, shipped for interactive sessions 2026-09-23 in `260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL`), so this is waiting on that item shipping rather than on an unknown.

- **Timed prompts** — schedule a one-shot prompt for a specific wall-clock time; e.g. "at 11:50am, resume session X with prompt P"; cross-provider (kiro-cli ACP, Claude Code, kiro-cli terminal, Codex terminal); complements *Scheduled tasks* (recurring launches) but distinct: one-shot, prompt-delivery-focused, and works for sessions PowerAtlas does not own.
  - *Quota-limit auto-schedule* — **partly shipped 2026-10-09** in `261009_DASHBOARD_OVERVIEW_REHAUL_QUOTA_RESUME_AND_GIT_STATUS`, not yet tested live: the Overview lists Claude Code and Codex sessions stopped by a quota limit, with the reset time and Quick resume and Schedule resume buttons (a schedule fires at reset plus 60 seconds; the default prompt is "resume", editable). Still open: kiro-cli, which is detect-and-show only with no resume; defaulting the payload to the last user prompt instead of "resume"; and a general one-shot timed prompt at any wall-clock time, which this does not provide.

- **Chained launches** — when a session finishes, automatically start the next one; works for sessions PowerAtlas drives, not for terminal sessions.

- **Skills support spike** — understand how argument passing works in both kiro-cli and Claude Code, unblocking three items above.

- **Plan-file shortcuts** — detect `plans/*.md` files and offer one-click `/qdev` buttons. Same unblock as *Template prompts*: the plan path rides the prompt line, not `$ARGUMENTS`.

---

## Workspace Intelligence

- **Session status extensions** — "stale /qdev never completed" heuristics, partly addressed by the Overview's plan `stale` badge, and detecting fresh terminal sessions. Live status dots shipped in `260712_LIVE_SESSION_STATUS`; notifications shipped 2026-09-19. What is left is below.
  - *Stale /qdev detection* — partly addressed by the Overview's plan `stale` badge (no activity > 7 days; activity is the plan's latest slug-scoped commit, else the file's mtime), shipped in `260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE`; the session-level > 24 h marker heuristic remains open. `/qdev` writes a progress marker into the plan file on each phase; a session that last wrote a marker >24 h ago with a non-complete status is "stale". Would require reading plan files on every status poll — expensive. Deferred until status poll performance is better understood.
  - *Notifications for terminal-hosted sessions* — the ACP half shipped, and so did terminal Codex sessions, in `261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB`: a daemon thread watches the rollouts of Codex threads whose writer lock a terminal process holds and sends one toast per new `task_complete` record, through the existing `notifications.enabled` toggle. Terminal sessions of the other providers have no toast, and cannot take the ACP route. PowerAtlas does not host a terminal session, so there is no frame to fire from: for kiro-cli the only signal is the `messages.jsonl` tail the status poll already reads, which makes a poll-side hook the sole option. Nothing reads it today. The Codex notifier is event-shaped (an offset per rollout, one toast per record), not poll-shaped, because the poll-shaped notifier removed on 2026-09-19 never ran. Notifications for the other providers' terminal sessions were left out of the Codex plan on purpose (its Q6, option C).
  - *Fresh terminal sessions* — sessions started in a terminal after PowerAtlas was launched are picked up on the next `refresh_stale_entries` tick (15–30 s). No gap for ACP sessions (PowerAtlas creates them). Terminal-session detection latency is bounded by the refresh interval, not by process monitoring.

- **Plan progress overlay** and **kiro-cli usage stats** — shipped in `260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE` as the dashboard Overview's *Active plans* and *Usage* sections, not as an overlay on rail rows. Active plans shows each In Progress or not-yet-archived Complete plan with its tracker progress; Usage covers 14 days of sessions, agent time, tool reliability, context pressure, model mix and Claude Code tokens (Codex tokens followed in `261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW`). `261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB` then added two things. Context pressure now has a second, labelled row for Codex: an estimate, the day's peak of last input tokens over the model's context window, with the kiro-cli numbers unchanged. The Codex Tokens block gained a "Sub-agent threads" line, split into `thread_spawn` and `guardian` review threads. It is a lifetime total per thread from Codex's state database, attributed to the thread's creation day and including context inherited at spawn, so it is an upper bound. The line is absent when the database is unavailable, including while Codex is closed.

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

- **[P2b] Session stores PowerAtlas cannot see — closed, permanently inaccessible.** "Classic" sqlite conversations in `conversations_v2` (`%LOCALAPPDATA%\Kiro-Cli\data.sqlite3`) have no file on disk. PowerAtlas does not read this store (v2 support removed 2026-09-17, `data_kiro.py` deleted). `kiro-cli chat --list-sessions -f json` remains the only surface that exposes these sessions, tagging each entry with `source: "classic"` — cost ~2.13 s per query, cwd-scoped (not global). Adding a sqlite reader would require reintroducing v2 infrastructure for sessions kiro-cli itself no longer creates; the question is closed rather than parked. *(v3 sessions covered by the `kiro-cli-v3` provider, shipped 2026-08-18.)*

- **Electron app** — package PowerAtlas as an Electron desktop app, replacing the current Python/pywebview stack. Removes the Python runtime dependency for end users, enables a distributable binary (no venv setup), and gives native access to the Chromium renderer without the pywebview abstraction layer. The web UI (`src/power_atlas/`) is already framework-free HTML/JS/CSS and would port directly; the Python backend logic (`data.py`, `web.py`, `acp.py`, etc.) would need a rewrite in Node.js or a bundled subprocess boundary to be decided at spike time. Not a near-term item — the current stack works and the rewrite cost is substantial.

- **Separate the ACP half into its own module** — make retiring or replacing the ACP half a delete, not surgery. Follows from the 2026-09-28 keep-and-borrow decision (see the positioning note under the ranking).
  - *What exists* — `web.py` imports `agent_profile` at module level (L48) and `acp` (L62), and mentions `acp` 286 times. That includes the session gate that waits on `agent_profile`'s generation lock and the permission-settings routes. The atlas modules (`data*.py`, `presence.py`, `status_classifier.py`, `launcher.py`, `overview.py`) do not depend on the ACP half. The coupling sits in `web.py`, `index.html` (the dashboard ACP panel) and `config.py`.
  - *Shape* — the ACP routes, the WebSocket handlers and the permission-settings routes move behind one registration call, for example an `acp_web` module that `web.py` mounts if present. The dashboard panel loads as its own script.
  - *Done when* — the atlas starts, serves the dashboard and passes its tests with the ACP modules absent.

- **One shared Codex rollout record reader** — the same Codex record format is parsed in several places, and `data_codex.turn_state` (the Working/Idle verdict, `261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB`) is the fourth reader. It was Follow-up Work 18 of the archived Codex provider plan (`plans/done/261002-1113_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW.md`), which named the fourth reader as the trigger. The trigger is met, and the plan chose parity tests over a merge for now: a parity case compares `turn_state` with the usage parser's agent-time pairing on five record sets, and the new readers match records by key path, never by substring.
  - *Done when* — one reader sits behind all of them, and the parity cases are deleted because there is nothing left to compare.

---

## Session Control & Integration

- **Fix `/compact` in ACP sessions** — the `/compact` slash command (which summarizes conversation history to reduce context pressure) does not work correctly in kiro-cli sessions driven over ACP by PA. The compaction recap UI (`.acp-compaction-details` / `.acp-compaction-recap`) exists in `acp.html` but was deliberately excluded from the dashboard parity scope. The gap is likely in `acp.py`'s handling of the compaction lifecycle (`COMPACTION_STATUS_METHOD`) and/or in how the compacted session state is replayed to reconnecting tabs. Needs a probe to identify exactly where the flow breaks.
  - *Done when* — `/compact` in a PA-held ACP session compacts the session history, the recap is shown inline in the transcript, and a reconnecting tab replays the compacted state correctly.

- **Align PA ACP session settings with kiro-cli** — PA's `session/new` sends a minimal `_meta.kiro` block; the `settings` field (which KAS reads via a per-feature registry: `yR` / `kp` / `N6` pattern in `acp-server.js`) controls multiple capabilities beyond `workflows.enabled`. PA was built before many of these existed and has not been audited against the current KAS settings schema. The `workflows.enabled` investigation exposed the pattern; a systematic pass is needed.
  - *Shape* — enumerate every recognized `_meta.kiro.settings` key and its shipped default from the KAS bundle; compare against what PA currently sends; add capability-improving keys (like `workflows`) and document any deliberate omissions.
  - *Done when* — PA sessions receive the same capability set as an equivalently-configured TUI session; any divergence is documented in `docs/KNOWLEDGE.md`.
  - *Status* — in progress under `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`. Phases 1-3 implemented; the live `run_workflow` checks passed 2026-10-09 and Probe C is resolved. `acp.py` `_build_session_settings` builds the block from `cli.json` and forwards `thinking`, `knowledge` and `codeIntelligence` (TUI defaults when absent; `largeToolOutputHandler` is not forwarded, by user decision 2026-10-08); `workflows` and `goal` follow `chat.enableWorkflows` (the Phase 1/2 build gate was deleted in the final review). The mapping, the deferred keys and the probe results are in `docs/KNOWLEDGE.md` § "ACP session/new settings".

- **kiro-cli setting: `tangentMode`** — Enable `chat.enableTangentMode` in PA's dynamic settings sync once PA supports tangent-mode sessions. Currently deferred from `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 1 because enabling it may produce new notification types or UI states (sub-sessions, visual indicators) that PA has no handler for. Investigate what tangent mode produces in the TUI, implement PA support, then add to the `w1()` mapping.
  - *Done when* — PA handles all notification types tangent mode produces; a tangent session in PA is visually identifiable and controllable.

- **kiro-cli setting: `checkpoint`** — Enable `chat.enableCheckpoint` in PA's dynamic settings sync once PA supports checkpoint notifications. Deferred from `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 1. Checkpoints may create save-point/rollback UI in the TUI that PA would need to surface.
  - *Done when* — PA handles checkpoint notification types; checkpoints are visible in the transcript and actionable.

- **kiro-cli setting: `c2s`** — Enable `chat.enableC2s` (cloud-to-server?) in PA once its behavior and UI requirements are understood. Deferred from `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 1. Unknown feature — probe the TUI and KAS bundle to determine what it does before building PA support.
  - *Done when* — Feature is understood, PA handles its notification types, and any required UI is present.

- **kiro-cli setting: `memory`** — Enable `memory.enabled` / `userMemoryOptIn` in PA's dynamic settings sync once PA has a memory panel. Deferred from `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 1. Memory tools (indexing, search, recall) produce tool calls that PA renders, but the full memory experience likely requires dedicated UI (memory index browser, opt-in control).
  - *Done when* — PA has a memory panel or equivalent; `memory.enabled` and `userMemoryOptIn` are forwarded from `cli.json`.

- **kiro-cli setting: `disableAutoCompaction`** — Decide whether PA should forward `chat.disableAutoCompaction` from `cli.json` or pin it to a PA-specific value. Deferred from `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 1. Sending `disableAutoCompaction: true` would suppress kiro-cli's automatic compaction for PA sessions; may or may not be desirable depending on whether the user wants their PA sessions to auto-compact.
  - *Source* — separate from the `/compact` replay bug (broadcast→emit fix in `plans/261008_ACP_COMPACT_FIX.md`); this is about whether auto-compaction fires at all.
  - *Done when* — A deliberate decision is recorded in `docs/KNOWLEDGE.md` and implemented.

- **kiro-cli setting: `_subagent`** — Decide whether PA should forward `chat.enableSubagent` (KAS key `_subagent`) from `cli.json`. Deferred from `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 1. PA already renders `_kiro/subagent/list` crew entries, but nobody has audited what this setting changes in what KAS emits. It also interacts with `workflows`, which makes KAS suppress `invoke_sub_agent` (`suppressChatDelegationTool`); that interaction needs investigating before the setting is sent.
  - *Done when* — The setting's effect on tools and notifications is measured, its interaction with `workflows` suppressing `invoke_sub_agent` is understood, and a deliberate decision is recorded in `docs/KNOWLEDGE.md` and implemented.

- **kiro-cli setting: `_delegate`** — Decide whether PA should forward `chat.enableDelegate` (KAS key `_delegate`) from `cli.json`. Deferred from `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 1, for the same reason as `_subagent`: its effect on emitted notifications is unaudited, and it interacts with `workflows` suppressing `invoke_sub_agent`, which needs investigating first.
  - *Done when* — The setting's effect on tools and notifications is measured, its interaction with `workflows` suppressing `invoke_sub_agent` is understood, and a deliberate decision is recorded in `docs/KNOWLEDGE.md` and implemented.

- **Full workflow support** — kiro-cli 2.28.0 introduced a complete workflow system that PA does not support at any layer. Three converging gaps: PA sessions likely don't receive the `run_workflow` tool (kiro-cli may gate it on `workflowNotifications.enabled` in `session/new`), PA has no handler for the `_kiro/workflow/*` notification family (all fall to the INFO log), and PA has no workflow monitor UI. Net effect: sessions driven through PA use the older `subagent` tool for fan-outs while TUI sessions use `run_workflow` with parallel step sessions, a workflow monitor, and the ability to steer the parent session while steps run.

  **Layer 1 — Enable the tool (session/new, one-liner):**  
  Send `"settings": {"workflows": {"enabled": True}, "goal": {"enabled": True}}` in the `_meta.kiro` block of `session/new` and `session/load`. `_build_session_settings` in `src/power_atlas/acp.py` builds it. `workflowNotifications` is deliberately not sent (user decision 2026-10-08): KAS ignores it entirely (0 occurrences in acp-server.js). The real gate is `_meta.kiro.settings.workflows.enabled`. When absent, KAS uses its shipped default of `"off"`, so `run_workflow` is never instantiated. When `enabled: true`, KAS also suppresses `invoke_sub_agent` for that session (the model is expected to use `run_workflow` for fan-outs instead) and makes `run_workflow`, `inspect_workflow`, `update_workflow`, and `send_message` all available. Source: `.agents/tasks/run-workflow-tool-registration.md`.  
  **Note:** the TUI also has workflows off by default — users must enable them in `/settings → features → workflows`. PA can either mirror that opt-in (a per-session checkbox in the new-session picker) or enable it for all sessions. The behavioral side-effect (subagent suppression) means this should be a deliberate choice, not a silent default change.

  **Layer 2 — Receive workflow events (acp.py, medium):**  
  Handle the `_kiro/workflow/*` notification family in `_on_notification`. Ten methods: `run_start`, `node_start`, `node_complete`, `node_paused`, `loop_iteration`, `watch_poll`, `paused`, `run_complete`, `steps_queued`, `recipes_changed`. The `node_start` payload carries each step's `sessionId` (the full `sess_<uuid>/` sibling session). Populate `crews[parent_id]` with step entries on `node_start`; mark complete on `node_complete`/`run_complete`. Handle the steer/queue delivery toggle (`_kiro/session/setWorkflowNotificationDelivery`) from client requests.  
  Workflow progress is also embedded in `user_message_chunk` frames tagged `_meta.kiro.notification.kind == "workflow-progress"` (the persistence/replay path; measured 2026-10-08, they are replay-only, not on the live stream) — decoding these in the client would give basic status on session reload without subscribing to child sessions. Phase 2 has no handler for them; nothing is rendered or tracked from them.

  **Layer 3 — Workflow monitor UI (/acp and dashboard panel, larger):**  
  The TUI has a dedicated `workflow-monitor` mode separate from `crew-monitor`. Workflow nodes have a different shape from crew entries: `{ nodeId, nodePath, branchId, iteration, status, agentName, sessionId }` vs `{ role, task, status }`. PA needs either a unified panel that accepts both shapes or a parallel track. The TUI's inline status line ("handhyg-001-phase… running 0/2 · ctrl+x expand · ctrl+g monitor") is rendered from the workflow's `nodes` state array. The steer/queue send-mode selector already exists in `/acp`'s composer CSS; it needs to be wired to the `setWorkflowNotificationDelivery` call.

  **Layer 4 — Child session ACP subscriptions (architecture, later):**  
  Each workflow step session is a full `sess_<uuid>/` with its own ACP event stream. PA currently processes one connection per session. To show per-step tool activity and transcript in the panel, PA would need to subscribe to each child session's ACP stream as they appear in `node_start`. Cost: N+1 ACP connections per workflow (parent + all running steps). This intersects with the process-model spike below — if PA moves toward per-session processes, child workflow sessions become first-class PA sessions naturally.

  - *Sources* — workflow child-session investigation 2026-10-11, run-workflow tool registration investigation 2026-10-11 (both under `.agents/tasks/`).
  - *Done when (Layer 1)* — PA sessions receive the `run_workflow` tool and behave identically to TUI sessions when the model uses it; the parent session remains steer-capable while steps run.
  - *Done when (Layer 2)* — `_kiro/workflow/*` notifications are handled; crew panel shows running workflow steps with live status; `workflow-progress` chunks decode on reload.
  - *Done when (Layer 3)* — `/acp` and the dashboard panel show a workflow monitor with per-step progress, the steer/queue toggle, and Ctrl+G navigation; parity with TUI's `workflow-monitor` mode.
  - *Status* — Layers 1 and 2 are in progress under `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` (Layer 4 is out of scope there). Phases 1-3 implemented (see Workflow liveness below): the settings builder exists and `_Supervisor._on_workflow_notification` handles `node_start`, `node_complete`, `node_paused` and `run_complete` (the other methods are logged stubs). Workflow steps appear as crew entries; live QA in a PA session passed 2026-10-09. The `failed` mapping is inferred (no failing value was ever captured), and a run cancelled from the parent emits no `run_complete`, so liveness leans on a 600 s staleness bound (docs/KNOWLEDGE.md). `workflowNotifications` is deliberately not sent (KAS ignores it).
  - *Done when (Layer 4)* — per-step transcript and tool activity visible in the panel; architecture decision documented.

- **Workflow liveness** — a PA session that launches a workflow via `run_workflow` has no active turn of its own (the parent turn ends or idles while step sessions do the work), so `presence.py`'s liveness computation reads it as idle and shows no dot or Working status. The correct state should combine the parent session with its running workflow children: if any child `sess_<uuid>/` with a matching `rootConversationId` is `in_progress` or `waiting_on_user`, the parent session should show Working or Waiting. This requires `data_kiro_v3.py` to correlate child sessions to their parent by `rootConversationId`, and `presence.py` (or the session-status computation) to propagate the highest-priority child state up to the parent's rail row.
  - *Dependency* — Layer 2 of Full workflow support above (`_kiro/workflow/node_start` handling in `acp.py`) would supply child session IDs in real time for ACP-held sessions; for disk-based liveness (sessions PA isn't holding over ACP), `data_kiro_v3.py`'s session scan already reads `session.json` and can be extended to check child sessions in the same workspace hash directory.
  - *Done when* — a PA session with an active workflow shows Working (not idle) in the rail and Overview tiles, and returns to idle/waiting once all child sessions complete.
  - *Status* — Part A is code complete under `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 3 (`web.py` `_workflow_states` and `_workflow_overlay_status`, applied in `_acp_status_for_held` and `api_session_availability`); live QA in a PA session passed 2026-10-09 (rail, availability and Overview tile Working during a workflow, back to waiting after the wake turn, after the classifier fix). The Waiting half is unverifiable so far: the only pause signals are `node_paused` (never captured) and `_kiro/session/notify` (not handled). Part B (terminal kiro-cli rows) is built too, for the dot and the Overview tile only: a 2026-10-09 probe measured a step silent for 330 s (parent transcript unwritten 331 s, parent `session.json` `idle`, child `in_progress`), which drops a terminal row started without `--resume-id` from `_session_is_live`'s 300 s recency rule for about (step length - 300) s. `data_kiro_v3.active_workflow_children` (child `in_progress` with its files no more than 1800 s old, or `waiting_on_user` within 6 h; the stuck-child premise is assumed, not observed) now keeps it live, wired into `web._session_is_live` (`docs/KNOWLEDGE.md`, workflow liveness section). No Working verdict exists for a row this PowerAtlas does not hold, so none was added.
  - *Done when (Part B)* — **Done** (unit-tested only; the real-terminal live dot was not exercised): a terminal kiro-cli session whose workflow step runs longer than 300 s keeps its live dot and Overview tile. Out of scope, unchanged: a Working or Waiting verdict for a terminal row.

- **Verify routed workflow step approvals live in Manual mode** (Low) — a workflow step's `session/request_permission` used to be answered `cancelled` (the step's tool call denied, no card). Fixed 2026-10-09 in `acp.py` (`_on_permission_request`, `_workflow_step_parent`; user decision: fix now before archival): a request from a tracked step is routed to the parent's page, attributed to the step in the card title, and answered by the step's own request id. Unit-tested only. **Not verified:** no step has ever raised a permission request in a probe, so it is assumed, not measured, that the request carries the child's `sessionId`, that the answer reaches the step, and that a step under Manual asks at all. Details and limits: `docs/KNOWLEDGE.md` § ACP permission wire shapes, "Workflow step approvals". Found in the review of `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md` Phase 2.
  - *Done when* — in Manual mode with workflows on, a step whose tool call needs approval shows a `[workflow step ...]` card on the parent's page, Allow runs the call and Deny refuses it (with the denial notice and the row's 'tool call denied' text), and the Stop (including Stop with no turn active, which both pages now offer while a card is pending), close and turn-end behaviours in the knowledge doc are observed.

- **`_kiro/session/notify` has no handler** — a workflow step's `send_message` text, including a question put to the user (severity `warning`), arrives as `_kiro/session/notify` (`sessionId` the parent, `callerSessionId` the child). `_on_notification` only logs it at INFO, so the text never reaches the page. It is also the only pause signal the plan names (a step's `send_message` with severity `warning`; `node_paused`, the other candidate, was never captured and the probe saw only `success` and `error` severities), so Phase 3 / SC-5 "Waiting" stays unverified until this is wired. Found in the Phase 2 review of `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`.
  - *Done when* — a step's message is shown on the parent's transcript or the step's row, a waiting step says what it is waiting for, and a real pause is observed to produce the `waiting` state.

- **Wire `workflow-cancel` so Stop can end a running workflow and stop the lingering step process** — Stop sends `session/cancel` for the parent; it returns `cancelled` at once and no `run_complete` follows (measured 2026-10-08). Live QA 2026-10-09 showed the workflow then stalls (no later step) while the in-flight step's process lingers `in_progress` on disk; by user decision PowerAtlas now treats Stop as ending the workflow (row "stopped", tracking dropped, a transcript note), but it cannot stop that process. **The Stop outcome is condition-dependent** (Low): a second-process probe 2026-10-09 with quick steps ran all three steps to `run_complete` after the same cancel, so wiring `workflow-cancel` is what would make the outcome deterministic; until then the notice says the workflow "may not continue". `docs/KNOWLEDGE.md` has the detail. PowerAtlas has no other route to stop a workflow. kiro-cli lists a `workflow-cancel` slash command in `available_commands_update` (100 commands against 96 without workflows); it was never tried. Found in the Phase 2 review of `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`.
  - *Done when* — `workflow-cancel` is measured (does it stop the step, does it emit `run_complete`), and Stop either calls it for a parent with live workflow children or the UI offers a separate stop control. Partial: Stop already marks the rows "stopped", drops the tracking and posts a transcript note (an `agent_error` frame, no client change); that is PowerAtlas's view only and stops no process.

- **A step resuming after `node_paused` stays `waiting`** — `_on_workflow_node_done` sets `waiting` on `node_paused`, and nothing sets `running` again until `node_complete`, so a resumed step keeps its "waiting" row and the weaker "waiting" bound. `node_paused` was never captured and the transition back is unmeasured. Found in the final review of `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`.
  - *Done when* — a real pause and resume is captured, and the resume signal (a frame from the child, or the answer to the pause) moves the row back to running.

- **A `loop_iteration` that reuses a child id is ignored** — `_on_workflow_node_start` is terminal-sticky, so a repeat id-bearing `node_start` for a finished, non-reaped row changes nothing (now logged at WARNING). `loop_iteration` was never captured, so whether it reuses or mints child ids is unknown. Found in the final review of `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`.
  - *Done when* — a looping workflow is run and measured; if it reuses ids, a new iteration reopens the row.

- **The idle sweeper can release a workflow parent whose step is quiet for more than 10 minutes** — `_sweepable` condition 7 uses the supervisor's 600 s read of "live", while the Part B disk path keeps a child `in_progress` for 1800 s. A workflow that has outlived `ACP_IDLE_TTL_SECONDS` with one step quiet for over 10 minutes and no tab attached can have its parent swept. Low probability: the longest measured quiet step is 330 s. The clocks are tabulated in `docs/KNOWLEDGE.md` § Workflow liveness. Found in the final review of `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`.
  - *Done when* — a quiet step of 10 to 30 minutes is measured, and either the bounds agree or the sweeper treats a child still `in_progress` on disk as live.

- **Workflow bookkeeping has five hidden couplings** — the crew-row keys `workflow` and `reaped` would be lost if `_on_subagent_list` rebuilt a workflow row; workflow rows ride the legacy `_active_fan_out_wave` filter; there are two crew-order schemes; "is a workflow row" has two markers (`entry['workflow']` and `_workflow_child_meta`); and module functions reach `_workflow_*` privates. Future cleanup, not a defect today: one supervisor method `finalize_stale_workflow_rows` and a single per-child record. Found in the final review of `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`; the couplings are listed in `memory/MEMORY.md` under `_workflow_children`.
  - *Done when* — the parallel dicts are one record per child and the stale-row finalisation lives in one method.

- **A workflow running in a loaded session is not tracked** — `session/load` replays `workflow-progress` as `user_message_chunk` frames only; it never re-sends live `_kiro/workflow/*` frames, and PowerAtlas deliberately ignores those chunks. A workflow still running when a session is resumed therefore has no crew rows and no liveness until its next live frame (a `node_complete` or `run_complete` for an untracked child is ignored). Found in the Phase 2 review of `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`.
  - *Done when* — resuming a session with a running workflow shows its steps (Layer 4 of Full workflow support, or a rebuild from `session.json` of the child sessions).

- **`_note_subagent_action` filters its crew frame with the wrong map** — it emits `subagents` with `crew_spawn_toolcallids` (the v2 map) instead of `_active_fan_out_wave`, so after a fan-out wave exists a `tool_call_chunk` from a child can emit a frame that filters wave-tagged rows out and makes the client drop the panel until the next frame. Only reachable when a fan-out wave and a workflow (or v3 sub-agent rows) coexist. One-line fix in `acp.py`; needs a test that drives a `tool_call_chunk` after a prior fan-out wave. Found in the Phase 2 review of `plans/261008_ACP_SETTINGS_ALIGNMENT_AND_WORKFLOW_SUPPORT.md`.
  - *Done when* — the frame uses `_active_fan_out_wave` and the test passes.

- **Clean close (`session/close` on the wire)** — PA's close is local-only: it pops the session from its own state and broadcasts `session_closed`, but never issues `session/close` to kiro-cli. kiro-cli TUI does issue it. The kiro-cli process keeps closed sessions in memory until the agent is recycled (15 min after the last session is gone, `AGENT_IDLE_RECYCLE_SECONDS`). For a user closing and re-opening many sessions in one PA run, memory grows silently. The fix: issue `session/close` after removing the session from PA's own state.
  - *Why it was left out* — `session/close` returns `-32603` on v3 (measured 2026-09-24, `docs/KNOWLEDGE.md` § "Session close — no v3 method unloads..."), so the call has no protocol effect at present. The item is open for when kiro-cli exposes a proper unload path, or as a speculative call (harmless -32603, but kilo-cli can change behavior later).
  - *What to check before building* — confirm whether `session/close` semantics changed in current kiro-cli builds, and whether the call now has an effect without deleting history.

- **Increase session cap to match TUI** — the current default `MAX_SESSIONS = 8` (range 1-16) is more restrictive than the TUI's soft cap of 64 inactive conversations. A user who routinely has >8 kiro-cli sessions hits the PA limit in ways they would never hit in the TUI. Source of the 8: process-cost measurement on v2 (~161 MB/session); on v3, all sessions share one process tree, so the per-session cost is much lower. A reasonable new default is 16-32; the `acp_max_sessions` config key exists for per-machine tuning.
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

- **Decide permission requests by rule for unattended sessions** — the real Automation keystone, split out on 2026-09-21 of the interactive permission-policy item (shipped 2026-09-23 in `260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL`). When nobody is watching, a `session/request_permission` must be *decided*, not parked: approve read-class tools, deny shell and write unless an explicit pattern allows them, and after a bounded wait cancel the turn and mark the session as needing attention rather than leaving it to the silence ceiling.
  - *Why it is separate* — the interactive item gave the agent a mouth; this one gives PowerAtlas an answer. Without it an unattended session still stalls, so **this** is what the six `## Automation & Workflows` items actually wait on.
  - *What it inherits* — the whole mechanism is already proven: an agent-profile `ask` rule fires a real `session/request_permission` (measured 2026-09-21, `scope: agent`, `source: agent-profile`), and `_meta.kiro.consent` carries `capability`, `resource` and `matchedRule`, which is exactly the input a rule engine needs to decide on. The answering path (`_handle_permission_response`) already validates ownership and single-resolution.
  - *What it must decide* — the allow/deny pattern set, and whether "unattended" is a property of the session, the dispatch, or the agent definition. Also where the bounded wait lives, given a pending request today rides the prompt's own 30-minute silence ceiling (`PROMPT_SILENCE_SECONDS`) rather than a request-specific timer.
  - *Auto mode is where this lands — 2026-09-25.* `260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL` shipped the pattern set as Manual mode (a rule editor, and "Allow, and always in new sessions…" on prompt cards) and built the Auto slot, shown disabled as "coming soon — behaves like Manual". Auto's decider is this item. Its recorded design (that plan's D-5): answer Deny, send the reason to the agent as a steer message, and escalate to the user when the agent repeats the request. It can only decide `ask` prompts: Always blocked rules are refused by kiro-cli before any prompt exists. It still needs a probe of when a queued steer reaches the agent. The bounded wait is still missing too: an unanswered prompt is cancelled only by the 1800 s prompt-silence ceiling (that plan's R-9).
  - *It also absorbs the lean dispatch agent* (below). Narrowing `resources:` is right for a dispatched narrow task and wrong for an interactive session, so that folding belongs here rather than in the interactive item.

- **A lean dispatch agent** — strip the full interactive-developer context before dispatching a narrow task; saves ~27k tokens per session (measured). As of 2026-09-19 this is the agent-definition half of the permission-policy work, not a separate deliverable; since the interactive item shipped (2026-09-23, `260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL`) it belongs to *Decide permission requests by rule for unattended sessions* above, which absorbs it: the same agent file that narrows `resources` is where the tool allow-list lives.
  - *What is measured* — a `resources: []` agent costs ~46k tokens (the floor from cwd-driven context), versus ~73k for `kiro_default`. The delta is ~27k tokens, confirmed on kiro-cli 2.16.0. **Stale-when**: kiro-cli moves off 2.16.0 — re-measure before building on this figure (currently at 2.22.0).
  - *One open question* — whether skills the dispatched task invokes (e.g. `/qplan`, `/qdev`) still load correctly with a stripped `resources` list. Untested; the skills themselves arrive via `skill://` resolvers and may not depend on the resources list.

- **A "needs you" inbox** — one cross-session list of everything waiting on a human: pending permission requests, unanswered clarifying questions, and turns that finished while unwatched.
  - *What exists* — the rail's status grouping buckets Working / Waiting / Errored / Available / Locked; the static-green dot marks an unwatched turn end (its state-loss bug was fixed 2026-09-19, `ad4e8f9`); permission requests and clarifying questions render inline in the transcript of the session that raised them, and nowhere else.
  - *What is missing* — a permission request in a session you are not looking at is invisible until you open that session. Kiro Crew's activity view is the reference: every agent as one card, with its pending approval on the card. For PowerAtlas the cheapest shape is a "Needs attention" bucket in status grouping, fed by the supervisor's `_pending_permission` map and the clarifying-question state, with a count in the page title. This is now the missing half of the notifications that shipped 2026-09-19: a toast tells you *a* session needs you and lands you on the page, but nothing yet tells you *which* one, so the inbox is where that notification should lead.

- **A fresh git worktree per session** — a checkbox in the new-session picker that creates `git worktree add` under the workspace and starts the session there.
  - *Why* — worktree isolation per agent is the one feature Conductor, Crystal, Vibe Kanban and Claude Squad all share. Two `/acp` sessions created in the same workspace today edit the same working tree, and nothing warns.
  - *What it touches* — the picker (`acp.html` and the dashboard copy shipped 2026-09-19), `_handle_new`/`new_session` for the cwd, and the rail: a worktree path is a distinct cwd, so it becomes its own workspace group in the rail and in kiro-cli's session store unless grouped back under its parent. Decide the grouping before building; cleanup of finished worktrees is a second decision.

- **Spike: drive Claude Code over ACP through an adapter** — make `/acp` a second-provider surface by running a Claude Code ACP adapter as a second supervised process.
  - *Why it is open* — `plans/CLOSED_INVESTIGATIONS.md` closes *taking over* a Claude Code session already live in someone's terminal (the `messagingSocketPath` and remote-control entries). Starting a **new** Claude Code session over ACP from PowerAtlas is a different question, and one nobody has asked of the code. It is no longer a differentiator: Kiro Crew already drives Claude Code through `@agentclientprotocol/claude-agent-acp` (corrected 2026-09-28; this line used to say Crew was kiro-only by design). That package is the adapter to spike against. Crew's source shows how it launches it, with `CLAUDE_CODE_EXECUTABLE` pointing the adapter at the `claude` binary (`src/kiro_crew/acp/client.py` L365-376, L2138-2156).
  - *A security gap to close before shipping* — the Always blocked and Protected rules are compiled into kiro-cli's derived agent (`compile_block` in `agent_profile.py`), so a Claude Code session would not see them. Extend Always blocked to the second driver before it drives anything.
  - *What it would cost* — the supervisor is kiro-specific in more than its binary: `_build_kas_session_params`, the `_kiro/*` extension methods, the token fulfilment, the session-store paths for `_lock_holder_v3`. A second provider means a driver abstraction, not a config change. Spike first: confirm an adapter exists for the installed Claude Code, that it answers `initialize`/`session/new`/`session/prompt` over stdio, and what its permission and session-persistence story is. Budget a week; exit with a go/no-go, not a feature.

- **Drive Codex from `/acp` through `@agentclientprotocol/codex-acp`** *(deferred)* — make `/acp` start, resume and prompt Codex threads, as it does for kiro-cli. The user decided it out of scope while planning `261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB` (its D2, Follow-up Work 1); this entry keeps what was found.
  - *Measured, 2026-10-02, Codex 0.160.0 on Windows 11* — Codex has no built-in ACP support: no `acp` command, and no ACP feature flag among its 154.
  - *Stated for the adapter, not run* — `@agentclientprotocol/codex-acp` 2.1.1 is a Node package under the Apache-2.0 licence, maintained under the ACP organisation. It starts the Codex App Server and supports `session/new`, `session/load`, permission requests and four modes. PowerAtlas has not installed or run it.
  - *Unknown* — whether it runs on Windows, whether it supports `session/list`, and how a session it drives would appear in the Codex store and the live dot. All three are unmeasured.
  - *What it would cost* — the supervisor in `acp.py` is kiro-cli-specific, so this needs a driver abstraction, the same one the Claude Code spike above needs, and a permission model for Codex's approval policy and sandbox. Always blocked and Protected are compiled into kiro-cli's derived agent and would not reach a Codex session either.
  - *Reopen when* — the adapter's Windows behaviour and its `session/load` are measured. A spike, as for Claude Code, would exit with a go or no-go.

- **Expose the effort level** — let the user pick an ACP session's effort instead of the fixed default of `max`.
  - *What exists* — `DEFAULT_EFFORT_LEVEL` in `acp.py` is `"max"`, applied through the agent's `effortLevel` config option on `session/new`, and on `session/load` when the session has no stored level (`4a6902b`). kiro-cli's own default is `high`, and `kiro-cli acp` does not read the `chat.modelDefaults` effort the terminal UI uses, so before this an ACP session ran one level below the same terminal session (measured 2026-10-05, kiro-cli 2.27.1).
  - *What is missing* — a control. A fresh session advertises `effortLevel` as a select with low, medium, high and max, and `session/set_config_option` accepts it, but neither `/acp` nor the dashboard composer offers it, so `max` cannot be lowered for a cheap task. The constant also ignores the user's `cli.json`, which the tool-search forwarding does read.
  - *To decide* — a per-session picker in the composer chrome, a global setting, or mirroring `cli.json`. Higher effort costs more tokens and time per turn, so the picker is the shape that makes `max` safe as the default.

- **Revisit `None` → `"working"` fallback** — unclassifiable sessions show as working; may warrant an explicit "unknown" state now that the fallback fires rarely.

- **Reconsider the Yolo default** — Yolo is the default permission mode (`agent_profile.py` L1828-1829), and in Yolo `compile_block` returns the Always blocked rules plus one `all: allow` before any Protected rule is emitted (L1086-1089). So Protected folders ask only in Manual, and the posture most sessions actually run under is Always blocked plus allow-all. Found by the 2026-09-28 council, where it cut both ways: PowerAtlas's security edge over Kiro Crew is smaller than the Manual design suggests.
  - *To decide* — whether the default should become Manual, or Yolo should keep the Protected rules. The cost of each is prompt volume in everyday sessions. The deliberate choice recorded in `load_config` (an unreadable config falls back to defaults) must still hold.

- **Crew verification spikes** — five one-off checks, each a trigger for reopening the 2026-09-28 keep-and-borrow decision. Not a trial and not a freeze. Run them in a throwaway VM or a separate Windows user profile: installing Crew writes about 10 agent files into the shared `~/.kiro/agents` and injects MCP servers.
  1. A phone-driven kiro-cli session works on Windows through Tailscale or cloudflared.
  2. A kiro-cli session under Crew is refused a read of a planted dummy `~/.ssh` file. This measures what kiro-cli's built-in sandbox enforces on Windows, which nobody has established.
  3. A project skill loads in a Windows project through kiro-cli's native `skill://` loader, given the per-session cwd Crew passes.
  4. Playbook skills run when invoked as `$name` with Crew's lesson injection off, and the playbook's gates still win over Crew's `prompt.md`.
  5. A hooks-only App can list sessions from outside Crew on Windows.

---

## Misc

- **[SECURITY] `/partials/launchers` renders custom-launcher `env`, defeating `_launchers_without_env`.** Found 2026-09-21 during the permission/credential exploration; verified by reading both files, not inferred.
  - *The defect* — `partials_launchers` (`web.py:4280-4281`) renders `config.custom_launchers` **raw**, with no stripping call, and `templates/partials/launcher_tile.html:14` emits `{% if launcher.env %}…{{ launcher.env | tojson }}` whenever a launcher has a non-empty `env`. So one GET from any signed-in local caller returns the values as JSON inside a tooltip row.
  - *Why it survived* — `_launchers_without_env`'s docstring (`web.py:1331-1364`) enumerates the three payloads it fixed and justifies leaving this one alone with the assertion "the tile partial never renders `env`". That sentence is false. The defence was reasoned about and got one template line wrong.
  - *Why no test caught it* — the single test on this route configures its sample launcher with `env: {}`, which is falsy, so the `{% if %}` branch never executes. The test passes and observes nothing.
  - *Exposure* — the docstring records that the live config carries `AUTH_TOKEN_PRODUCTION` and `AUTH_TOKEN_STAGING`. `/partials/launchers` is a GET, and `same_origin_guard`'s Origin/Referer check is POST-only by design, so this path has no CSRF guard of its own. Since the loopback gate shipped it needs a valid `pa_local` cookie, so it is reachable by any signed-in local caller rather than by any local process; the cookie is `SameSite=Strict`, which keeps a page on another site from riding it.
  - *Relation to the loopback credential* — SC-5 of `plans/260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL.md` has shipped and gates this route as a side effect of default-deny, which removed the anonymous reachability. **It does not fix the leak**: any credentialed caller, and the page itself, still receive the credentials. The stripping fix is independent and should not wait on that plan.

- **[SECURITY — accepted, 2026-08-03] No NetBird access policy restricts this host, and that is now a decision rather than an oversight.** Measured 2026-07-31: `netbird status -d` enumerates **all 17** account peers in this host's network map, including machines belonging to other people (`akita`, `paros-g`, `nuc-chicago`, `ec2amaz-tv495hp`, `macbook-air-de-polestar`, …), so the stock `Default` (All → All) policy is still enabled.
  - *Measured 2026-08-03* — all inbound File and Printer Sharing rules (TCP 139, TCP 445) are **disabled** at the Windows Firewall level. The genuine exposure was UDP 137/138 (Network Discovery), admitted by two rules scoped to the Private profile. The WireGuard tunnel is classified Private, so those rules apply to NetBird peers.
  - *Decision* — the device cookie is the sole authorization layer for `/acp`. Creating a NetBird access policy scoped to this host's own devices restores the intended second layer (5 minutes in the NetBird console). The implementation does not depend on that policy being in force, so it is worth doing but not blocking.

- **Claude Code sidecar fields inventory** — full table of every field PowerAtlas reads (or could read) from `~/.claude/sessions/<pid>.json`.

- **Public-ids hook coverage gaps** — this repository is public, and its pre-commit and commit-msg hooks (`_check_public_ids.py`) block a session id found in a local session store. The Codex plan (`261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB`, Follow-up Work 11) hardened them and left these open by the user's scope choice on 2026-10-05.
  - *What it cannot see* — home paths and user names; an id whose session was already deleted; an id that exists only in Codex's state database; ids written without hyphens; the stores it does not walk (Claude Code `todos`, `file-history`, `session-env`, `shell-snapshots` and `debug`; Codex `shell_snapshots`, `log` and `thread_history_*.sqlite`; the Kiro IDE `workspace-sessions` folder).
  - *What skips both hooks* — cherry-pick, rebase and revert. A `pre-push` check over the pushed range would cover them.
  - *Fails open, with a warning* — the 64 MiB wide-blob cap, a `git cat-file` timeout and an unreadable store.
  - *Done when* — a home path or user name in an added line blocks the commit, and the hook set covers a push of rebased commits. `_check_public_ids_scenarios.py` is where each new case goes.
