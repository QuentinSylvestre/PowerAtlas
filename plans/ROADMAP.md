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
- **Session status extensions** — "stale /qdev never completed" heuristics, partly addressed by the Overview's plan `stale` badge, and detecting fresh terminal sessions (live status dots and notifications both shipped)

### Platform
- **Secret-aware env vars for custom launchers** *(shape a still open)* — credentials in launcher env blocks are in cleartext; serving them was fixed, storing them safely is not yet
- **Parked items** — creating a session in a workspace with no prior sessions · two SECURITY items
- **Separate the ACP half into its own module** — the atlas side imports `agent_profile` and `acp` directly today, so retiring or replacing the ACP half would be surgery rather than a delete
- **Adapter hooks for provider enumeration** — a provider id is spelled out in about 35 places, so each new provider repeats a hand sweep
- **Shared `_normalize_path` UNC gap** — the shared helper can block on an unreachable network share; the Codex adapter already avoids it
- **`launch_custom` env scrub excluded (follow-up)**: CLAUDE_CODE_* markers are not scrubbed from `launch_custom`-launched sessions — user-defined scripts may rely on inherited environment. See `plans/done/260818_ACP_ENV_MARKER_AND_OVERLAY_STEERING.md` Follow-up #2.
- **`launch_terminal` env scrub excluded (follow-up)**: `launch_terminal` (~`launcher.py:595`) opens a bare shell without env scrubbing — the user manually starts a process inside it. Follow-up #5 of the same plan.

### Session Control & Integration
- **Creating a session in a workspace that has none** — cut from the picker because PowerAtlas has no folder browser; two candidate shapes described
- **Reach the operator away from the machine** — the desktop half shipped 2026-09-19; reaching a phone needs a secure context on the remote bind, i.e. the TLS decision
- **Decide permission requests by rule for unattended sessions** — the real Automation keystone, split out 2026-09-21; an `ask` rule alone leaves an unattended session waiting on the 30-minute silence ceiling rather than deciding
- **A lean dispatch agent** — strip the full interactive-developer context before dispatching a narrow task; saves ~27k tokens per session (measured); now the agent-definition half of *Decide permission requests by rule for unattended sessions* (the interactive permission item shipped 2026-09-23 in `260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL`)
- **A "needs you" inbox** — one cross-session list of pending permission requests, unanswered clarifying questions and finished turns, borrowed from Kiro Crew's activity view
- **A fresh git worktree per session** — a checkbox in the new-session picker; table stakes in Conductor, Crystal and Vibe Kanban, and two `/acp` sessions in one workspace share a working tree today
- **Spike: drive Claude Code over ACP through an adapter** — would make `/acp` multi-provider; Kiro Crew already does this (corrected 2026-09-28), so it is a capability, not a differentiator; not covered by the closed takeover investigations, which concern sessions already live in a terminal
- **Revisit `None` → `"working"` fallback** — unclassifiable sessions show as working; may warrant an explicit "unknown" state now that the fallback fires rarely
- **Reconsider the Yolo default** — in Yolo, Protected emits no rules, so the default posture is Always blocked plus allow-all, weaker than the Manual design suggests
- **Crew verification spikes** — five one-off checks, run in a throwaway VM or Windows profile, each a trigger for reopening the 2026-09-28 keep-and-borrow decision
- **[P2b] Session stores PowerAtlas cannot see** — closed; sqlite `conversations_v2` sessions permanently inaccessible post-v2-removal (2026-09-17); v3 covered

### Misc
- **[SECURITY] `/partials/launchers` leaks custom-launcher `env`** — any signed-in local caller's GET returns the credentials `_launchers_without_env` exists to strip, because the tile partial renders them after all (the loopback gate shipped in `260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL` removed anonymous access, not the leak)
- **Claude Code sidecar fields inventory** — full table of every field PowerAtlas reads (or could read) from `~/.claude/sessions/<pid>.json`


- **Adapter hooks for provider enumeration** — a provider id is spelled out in about 35 places (launcher tables, web tables, presence specs, overview dispatch, JS maps, CSS and templates), so every new provider repeats the sweep the Codex plan did by hand. Found when the Codex provider was added (`plans/done/261002-1113_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW.md`, Follow-up Work 16). Worth doing before a fifth provider is planned.
  - *Shape* — optional hooks on the adapter module (display name, default colour, helper subcommands, usage parser) so that a registration replaces most of the sites. Which sites can really move is not measured: start from a grep of an existing provider's id.
  - *Done when* — a new provider needs one adapter module and one registration, and a test fails when a site is missed.

- **Shared `data._normalize_path` UNC gap** — the shared helper expands 8.3 short names through `GetLongPathNameW`, which can block on an unreachable network share. Found in the Codex final review and **not reproduced**. The Codex adapter already avoids it by keying a network or device cwd (UNC, `\\?\`, `//`) by case-fold only; the Claude Code and kiro-cli paths still call the helper (Follow-up Work 17 of the same plan).
  - *Shape* — the same guard inside the shared helper, so the adapter-side special case can go.
  - *Done when* — a cwd on an unreachable share does not stall discovery for any provider, shown by a test with a stubbed slow resolver.
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
  - *Quota-limit auto-schedule* — detect session-limit messages in the transcript ("You've hit your session limit · resets 11:50am (America/Chicago)") and surface a one-click "Resume at reset time" action; parse the reset time from the message, convert from named timezone to local, and pre-populate a timed-prompt for the interrupted session with the last user prompt as the default payload. Works for any provider that embeds a reset timestamp in the limit message.

- **Chained launches** — when a session finishes, automatically start the next one; works for sessions PowerAtlas drives, not for terminal sessions.

- **Skills support spike** — understand how argument passing works in both kiro-cli and Claude Code, unblocking three items above.

- **Plan-file shortcuts** — detect `plans/*.md` files and offer one-click `/qdev` buttons. Same unblock as *Template prompts*: the plan path rides the prompt line, not `$ARGUMENTS`.

---

## Workspace Intelligence

- **Session status extensions** — "stale /qdev never completed" heuristics, partly addressed by the Overview's plan `stale` badge, and detecting fresh terminal sessions. Live status dots shipped in `260712_LIVE_SESSION_STATUS`; notifications shipped 2026-09-19. What is left is below.
  - *Stale /qdev detection* — partly addressed by the Overview's plan `stale` badge (no activity > 7 days; activity is the plan's latest slug-scoped commit, else the file's mtime), shipped in `260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE`; the session-level > 24 h marker heuristic remains open. `/qdev` writes a progress marker into the plan file on each phase; a session that last wrote a marker >24 h ago with a non-complete status is "stale". Would require reading plan files on every status poll — expensive. Deferred until status poll performance is better understood.
  - *Notifications for terminal-hosted sessions* — the ACP half shipped; this half did not, and cannot take the same route. PowerAtlas does not host a terminal session, so there is no frame to fire from: the only signal is the `messages.jsonl` tail the status poll already reads, which makes a poll-side hook the sole option. Nothing reads it today.
  - *Fresh terminal sessions* — sessions started in a terminal after PowerAtlas was launched are picked up on the next `refresh_stale_entries` tick (15–30 s). No gap for ACP sessions (PowerAtlas creates them). Terminal-session detection latency is bounded by the refresh interval, not by process monitoring.

- **Plan progress overlay** and **kiro-cli usage stats** — shipped in `260924_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE` as the dashboard Overview's *Active plans* and *Usage* sections, not as an overlay on rail rows. Active plans shows each In Progress or not-yet-archived Complete plan with its tracker progress; Usage covers 14 days of sessions, agent time, tool reliability, context pressure (kiro-cli only), model mix and Claude Code tokens (Codex tokens followed in `261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW`).

- **LLM-generated session name alias** — when kiro-cli has not set a session title, call `claude-haiku-4-6` with the first user message to generate a short, task-oriented label; shown in the rail and transcript panel without a user action. Keeps the existing kiro-cli-written title where one exists; only fills the gap where the title is absent or equals the session id.

---

## Platform

- **Secret-aware env vars for custom launchers** *(shape a still open)* — credentials in launcher env blocks are in cleartext in `config.toml`; shape (a) is an OS keystore reference, shape (b) is an encrypted-at-rest blob. Both require a UI decision about how the user enters/updates credentials.

- **[P2b] Session stores PowerAtlas cannot see — closed, permanently inaccessible.** "Classic" sqlite conversations in `conversations_v2` (`%LOCALAPPDATA%\Kiro-Cli\data.sqlite3`) have no file on disk. PowerAtlas does not read this store (v2 support removed 2026-09-17, `data_kiro.py` deleted). `kiro-cli chat --list-sessions -f json` remains the only surface that exposes these sessions, tagging each entry with `source: "classic"` — cost ~2.13 s per query, cwd-scoped (not global). Adding a sqlite reader would require reintroducing v2 infrastructure for sessions kiro-cli itself no longer creates; the question is closed rather than parked. *(v3 sessions covered by the `kiro-cli-v3` provider, shipped 2026-08-18.)*

- **Electron app** — package PowerAtlas as an Electron desktop app, replacing the current Python/pywebview stack. Removes the Python runtime dependency for end users, enables a distributable binary (no venv setup), and gives native access to the Chromium renderer without the pywebview abstraction layer. The web UI (`src/power_atlas/`) is already framework-free HTML/JS/CSS and would port directly; the Python backend logic (`data.py`, `web.py`, `acp.py`, etc.) would need a rewrite in Node.js or a bundled subprocess boundary to be decided at spike time. Not a near-term item — the current stack works and the rewrite cost is substantial.

- **Separate the ACP half into its own module** — make retiring or replacing the ACP half a delete, not surgery. Follows from the 2026-09-28 keep-and-borrow decision (see the positioning note under the ranking).
  - *What exists* — `web.py` imports `agent_profile` at module level (L48) and `acp` (L62), and mentions `acp` 286 times. That includes the session gate that waits on `agent_profile`'s generation lock and the permission-settings routes. The atlas modules (`data*.py`, `presence.py`, `status_classifier.py`, `launcher.py`, `overview.py`) do not depend on the ACP half. The coupling sits in `web.py`, `index.html` (the dashboard ACP panel) and `config.py`.
  - *Shape* — the ACP routes, the WebSocket handlers and the permission-settings routes move behind one registration call, for example an `acp_web` module that `web.py` mounts if present. The dashboard panel loads as its own script.
  - *Done when* — the atlas starts, serves the dashboard and passes its tests with the ACP modules absent.

---

## Session Control & Integration

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
