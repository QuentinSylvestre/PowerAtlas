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
- **Session status extensions** — "stale /qdev never completed" heuristics and detecting fresh terminal sessions (live status dots and notifications both shipped)
- **Plan progress overlay** — show phase completion (e.g. "Phase 3/5") on workspace cards by reading plan files
- **kiro-cli usage stats** — dashboard showing session counts, durations, and tool-usage patterns over time

### Platform
- **Secret-aware env vars for custom launchers** *(shape a still open)* — credentials in launcher env blocks are in cleartext; serving them was fixed, storing them safely is not yet
- **Parked items** — usage stats · plan-progress overlay · creating a session in a workspace with no prior sessions · two SECURITY items
- **`launch_custom` env scrub excluded (follow-up)**: CLAUDE_CODE_* markers are not scrubbed from `launch_custom`-launched sessions — user-defined scripts may rely on inherited environment. See `plans/done/260818_ACP_ENV_MARKER_AND_OVERLAY_STEERING.md` Follow-up #2.
- **`launch_terminal` env scrub excluded (follow-up)**: `launch_terminal` (~`launcher.py:595`) opens a bare shell without env scrubbing — the user manually starts a process inside it. Follow-up #5 of the same plan.

### ACP v3 Follow-up

> Items from 260819_ACP_V3_SPIKE.md (spike-validated feasibility, `[POST-SPIKE]`-tagged) and from
> 260908_ACP_V3_PRODUCTION_HARDENING.md (which brought the then-separate v3 protocol route to
> production quality — its own deferred Follow-up Work items are migrated here on plan close). Each
> requires design decisions or product-shape choices beyond either plan's own scope.
> **`260911_ACP_V2_TO_V3_ENGINE_CUTOVER`** has since retired that separate route and folded the v3
> engine into `/acp` as the sole engine; the items below are updated to the current, single-engine
> naming. Three items tracked in `plans/260911_ACP_V3_FOLLOWUP_FEATURES.md` reached final
> disposition there and have been removed from this list as done: the mode-switcher UI (Phase 3),
> and D32's orphan-lock fix (Phase 1) — D32 survives below only for the live caveat it uncovered,
> not as outstanding work. The steering-palette item was spiked and found not shippable as scoped
> (Phase 4); it stays, because a negative result nobody records gets re-attempted.

- **[POST-SPIKE] MCP OAuth in /acp — corrected.** The real signal is `session_info_update` → `_meta.kiro.displayError` (a human-readable message), not `_kiro/mcp/status` → `failedAuthorization`/`authorizationUrl` as originally guessed here — no `authorizationUrl`-bearing signal has ever been observed. `260908_ACP_V3_PRODUCTION_HARDENING`'s SC-3 surfaces the message; a full OAuth "Connect" completion flow isn't buildable from what's confirmed. Revisit if an `authorizationUrl` signal is ever found.

- **[POST-SPIKE] _kiro/spec/* and _kiro/workflow/* notification handling — corrected.** No such dedicated notification methods exist (confirmed via `260908_ACP_V3_PRODUCTION_HARDENING` Phase 0/6 research) — spec mode only adds a `"spec"` tag to the builtin-tools list and relies on ordinary `session/update` subtypes plus `session/request_permission` (see that plan's SC-9, implemented in Phase 6: a structured clarifying-question request that blocks the turn on an answer).

- **kiro-cli v3 writes no `.lock` file, so the D32 orphan-lock guard has no reachable input.** Kept as a live gotcha, not as outstanding work — the fix itself shipped. `_publish_live()` now publishes the real agent pid (`self.agent_pid()`) instead of the `pid=0` sentinel, and `presence._scan()`'s own-agent orphan-lock guard now checks `provider in _KIRO_PROVIDERS` (covering both `kiro-cli` and `kiro-cli-v3`), so the guard's logic now works correctly for v3. **Caveat (found in the same plan's Phase 1 QA pass, 2026-09-16, confirmed through kiro-cli build 2.21.4)**: kiro-cli v3 never writes a `.lock` file under `~/.kiro/sessions/cli/`, so this guard's `kiro-cli-v3` branch currently has no reachable input — the fix is correct but unobservable until/unless a future kiro-cli build writes v3 locks. The operative v3 session-availability/self-orphan-recovery mechanism today is `acp.py`'s `_lock_holder_v3()` (`session.json`'s own `status` field, independent mtime-staleness self-heal, no pid involved), unaffected by this fix either way. Fixed in `plans/260911_ACP_V3_FOLLOWUP_FEATURES.md` Phase 1. Source: `260908_ACP_V3_PRODUCTION_HARDENING` Phase 1 review findings (Reliability engineer), Follow-up Work item 6.

- **3 steering-type slash-command palette entries have no working trigger in `/acp` — spiked, no working mechanism found; not shippable as scoped.** `architecture-selection`/`quick-spec`/`bug-fix` are excluded from both dropdown buckets by the existing `steering` filter. `plans/260911_ACP_V3_FOLLOWUP_FEATURES.md` Phase 4's live probe (kiro-cli 2.21.4) tested three ordered hypotheses, cheapest-first, and all three failed with distinct, diagnosable negative signals (not silent timeouts): **H1** (plain-chat dispatch, mirroring how skills resolve) — the agent explicitly denies having the skill and cites its real, unrelated catalogue, no resource-fetch signal anywhere in the trace. **H2** (`_kiro.dev/commands/execute` called directly) — fails with a server-side `-32603` naming the RPC method itself unregistered (`"Ext method ... has no persistence classification"`), not a command-not-found refusal; whether this is specific to this command name or a build-wide gap in the method couldn't be settled (no known-good control command exists in the current catalogue to test against). **H3** (`_kiro/knowledge`) — the method exists (confirmed present in `initialize`'s `extensionMethods` list) but rejects the plan's specified params shape with `"Unknown subcommand: undefined"`; 6 plausible `subcommand` guesses were all rejected identically, with no valid-value enumeration ever returned by the server. Whoever revisits this needs kiro-cli's own source/docs for `_kiro/knowledge`'s real params contract — further blind probing is not productive. Filed under `plans/260911_ACP_V3_FOLLOWUP_FEATURES.md`'s Follow-up Work (Deferred). Source: `260908_ACP_V3_PRODUCTION_HARDENING` Phase 0 rounds 3-4, Follow-up Work item 8.

- **Supervisor crash detection has a blind spot on Windows — found 2026-09-19, needs a design decision.** `kiro-cli.exe` is a thin wrapper that spawns a `node.exe` child, and the child inherits the stdout pipe's write handle. Killing the wrapper therefore does not close the pipe, so the supervisor's blocking reader thread never sees EOF and its crash path never fires; recovery happens only when the next unrelated request's `proc.poll()` notices the dead process. Attended, that is a delayed error. Unattended, it is a silent stall for as long as nobody sends anything. Candidate fixes are a periodic `poll()` watchdog on the supervisor, or a Windows job object so the child dies with the wrapper; which one depends on whether an orphaned `node.exe` should be reaped or adopted. Observed with a live probe on kiro-cli 2.22.0 (`/qtest` run, 2026-09-19).

- **ACP session close has no wire-level confirmation — permanent kiro-cli limitation, now universal.** v2's `close_session` used to send `_kiro.dev/session/terminate` and wait for the agent's acknowledgment before dropping local state; that call — and the rest of v2's live-protocol code — was deleted by `260911_ACP_V2_TO_V3_ENGINE_CUTOVER`, so the gap that used to be scoped only to v3 sessions now applies to every ACP session. The sole surviving `close_session` (`CLOSE_METHOD = None`) drops local state with no wire call — every terminate method tried during the original v3 spike returned `-32603`/`-32601`. Mitigated (local cleanup + `session_closed` broadcast still fire), but the server-side release of resources for a closed session is never confirmed; not fixable from the PowerAtlas side without kiro-cli binary support. Accepted as a permanent, out-of-scope limitation by `260911_ACP_V2_TO_V3_ENGINE_CUTOVER`'s Scope boundaries. Source: v2-vs-v3 feature comparison, 2026-09-11.

### Session Control & Integration
- **Creating a session in a workspace that has none** — cut from the picker because PowerAtlas has no folder browser; two candidate shapes described
- **Reach the operator away from the machine** — the desktop half shipped 2026-09-19; reaching a phone needs a secure context on the remote bind, i.e. the TLS decision
- **Permission policy for unattended sessions** — the keystone; measured 2026-09-19, an unattended ACP session runs shell and writes arbitrary paths with **no** permission gate, and `acp`+v3 accepts no trust flag. First question is whether any gate exists at all
- **A lean dispatch agent** — strip the full interactive-developer context before dispatching a narrow task; saves ~27k tokens per session (measured); now the agent-definition half of the permission-policy item
- **A "needs you" inbox** — one cross-session list of pending permission requests, unanswered clarifying questions and finished turns, borrowed from Kiro Crew's activity view
- **A fresh git worktree per session** — a checkbox in the new-session picker; table stakes in Conductor, Crystal and Vibe Kanban, and two `/acp` sessions in one workspace share a working tree today
- **Spike: drive Claude Code over ACP through an adapter** — would make `/acp` multi-provider, which Kiro Crew is not; not covered by the closed takeover investigations, which concern sessions already live in a terminal
- **Revisit `None` → `"working"` fallback** — unclassifiable sessions show as working; may warrant an explicit "unknown" state now that the fallback fires rarely
- **[P2b] Session stores PowerAtlas cannot see** — closed; sqlite `conversations_v2` sessions permanently inaccessible post-v2-removal (2026-09-17); v3 covered

### Misc
- **[SECURITY] Loopback API token** — any local process can create sessions and run shell commands via `/api/*`; proposed fix is a startup-generated secret injected into the page
- **Claude Code sidecar fields inventory** — full table of every field PowerAtlas reads (or could read) from `~/.claude/sessions/<pid>.json`

---

> Non-executed ideas and future features, organized by theme. Shipped items are removed rather than
> struck through — `git log -- plans/ROADMAP.md` carries their history.
>
> **Paths already investigated and rejected live in `plans/CLOSED_INVESTIGATIONS.md`**, with the
> measurements that decided them and the condition that would reopen each one. Read it before
> proposing `kiro-cli serve`, `_kiro.dev/session/list`, or kiro-cli remote control again.
>
> **Provider measurements live in `docs/KNOWLEDGE.md`** — how kiro-cli and Claude Code actually
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
> on a premise the code no longer has (see item 1 below). The 2026-09-19 ranking opened with the
> notification item; it shipped the same day and has been removed from the table.
>
> **Positioning, so the borrowing is deliberate.** Kiro Crew (AWS, open-sourced 2026-08-04) is one
> long-running gateway driving a single `kiro-cli` process over ACP for many sessions — the same shape
> as `acp.py`'s supervisor — and adds persistent memory, scheduled jobs, webhook triggers, chat-surface
> integrations and an app SDK on top. The parallel-agent desktop tools (Conductor, Crystal, Vibe Kanban,
> Claude Squad) converge on worktree isolation per agent, a diff view, and an at-a-glance
> blocked/active/ready board. PowerAtlas is neither: its value is machine-wide visibility over sessions
> it did not start, across three providers. What it should take from them is narrow — Crew's
> execution-boundary permission model, Crew's single "needs you" view, and the tools' worktree per
> session. Memory, lessons, apps and chat integrations stay out.
>
> **What would invalidate it**: the keystone shipping (it gates all six `## Automation & Workflows`
> items); or kiro-cli gaining — or being found to already have — a per-request permission mechanism on
> `acp` + v3, which would restore the keystone to a design problem rather than a question for the
> vendor. The `$ARGUMENTS` re-check has been run (negative, 2026-09-19) and is no longer pending.

| # | Item | Tier | Why here |
|---|---|---|---|
| 1 | *[SECURITY] Loopback API token* | days | **Promoted 2026-09-19 on measured evidence.** An ACP session executes shell and writes arbitrary absolute paths with no permission gate (see item 2), so an unauthenticated loopback API is arbitrary code execution as the user for any local process. This was ranked below the keystone while the exposure was hypothetical; it is now measured, and unlike the keystone it is entirely within PowerAtlas's control |
| 2 | *Permission policy for unattended sessions* | **keystone** | Still gates all six Automation items, but its premise was falsified on 2026-09-19 and its first step is now a question for kiro-cli, not a design: **is there any per-request permission mechanism on `acp` + v3 at all?** Nothing was observed gating anything. Until that is settled the only lever PowerAtlas holds is the agent's `tools:` inventory |
| 3 | *A fresh git worktree per session* | days | A checkbox in the picker that shipped 2026-09-19; removes the working-tree collision between parallel sessions in one workspace. Sharper now that an ungated agent writes wherever it likes |
| 4 | *A "needs you" inbox* | days | Status grouping already buckets Working/Waiting/Errored; this adds clarifying questions across every session in one place. Now also where a notification lands you — the toast tells you *a* session needs you, not *which*. Note the "pending permissions" half may have nothing to show (item 2) |
| 5 | *Spike: Claude Code over ACP through an adapter* | week | The one thing Kiro Crew structurally cannot do. A spike, not a commitment: the supervisor is kiro-cli-specific in its `initialize`/`session/new` shapes and would need a second driver |

**Parked, deliberately**: usage stats · plan-progress overlay · creating a session in a workspace that
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
  - *Unattended posture, corrected twice — 2026-09-19* — this bullet first said an unattended session runs with `-a`; that was corrected to "it stalls at its first shell or write request, waiting for a human". **Direct measurement falsified the correction too.** `acp.py` indeed never passes `-a` (the v3 engine rejects it), but nothing else gates the session either: a probe drove shell, read and write — plus a write to an absolute path outside the cwd — with zero `session/request_permission` frames. A headless session today does not stall and does not wait. It executes. See the permission-policy item under `## Session Control & Integration` for the full measurement.
  - *Exit condition for this item* — the permission-policy item under `## Session Control & Integration` (item 1 in the ranking): a per-tool auto-approve rule with deny patterns and a bounded wait. Once that exists, dispatch is a prompt plus a session close on turn end.

- **Open session with a prompt or skill** — prompt delivery and skill loading are proven; passing skill arguments (`$ARGUMENTS`) was negative on 2.14.2 and is due a one-prompt re-check on the current build.
  - *`$ARGUMENTS` — measured on kiro-cli 2.14.2, 2026-07-26.* Slash-command argument passing uses `$ARGUMENTS` in the SKILL.md body (e.g. `/qdev plans/my-plan.md` expands to `$ARGUMENTS` → `plans/my-plan.md`). The expansion is handled by kiro-cli's own command parser, not by PowerAtlas. Whether a prompt string containing `$ARGUMENTS` is expanded by the model or by the CLI is unverified — a test session reliably received the literal string `$ARGUMENTS`. Until this is verified, skill invocations with arguments are not reliably deliverable.
  - *Re-checked 2026-09-19 on kiro-cli 2.22.0 — still negative, and there is a workaround.* Probed over a disposable `acp --agent-engine v3` subprocess with a scratch skill whose body contained `$ARGUMENTS`, invoked as ordinary prompt text (`/qa-argtest <value>`), which is how `/acp`'s palette sends it. The skill loads, but the placeholder is **not** substituted: asked to report what it literally saw, the agent answered `EXPANDED=NO`, with and without an argument.
  - *The naive version of this test gives a false positive — do not repeat it.* Simply asking the skill to echo its arguments returns the right value, because the agent can read it off the user's own prompt line and infer it. Only a skill that asks the agent to report **what it literally sees** at the placeholder discriminates expansion from inference. The first run of this probe passed and was wrong.
  - *Why this is not a hard block.* The argument text does reach the agent — it is right there in the prompt — so a skill authored to read its arguments from the user's message works today, with no CLI-side expansion. The blocker is the `$ARGUMENTS` **mechanism**, not the capability. That is a skill-authoring convention, and it is what *Template prompts* and *Plan-file shortcuts* should be built on rather than waiting for kiro-cli.

- **Template prompts** — save reusable per-workspace prompts. No longer blocked on `$ARGUMENTS`: author the template to read its arguments from the prompt line (see *Open session with a prompt or skill*).

- **Scheduled tasks** — cron-like recurring kiro-cli launches; mechanism is measured and process cost is known, but the auto-permissions gate must come first.
  - *Process cost* — measured 2026-07-26: each kiro-cli ACP session costs ~161 MB RSS and 3 processes. A scheduled task that accumulates open sessions will exhaust memory; sessions need to be closed when their tasks complete. Closure via `session/terminate` or idle-TTL is measured and works.
  - *Auto-permissions gate* — a scheduled task has no human watching, and as measured on 2026-09-19 there is nothing to watch: no permission dialog is raised at all, so the task runs its tools unsupervised rather than stalling. The permission-policy item under `## Session Control & Integration` is still the gate, but its own first question is now whether kiro-cli offers any per-request mechanism on `acp`+v3.

- **Chained launches** — when a session finishes, automatically start the next one; works for sessions PowerAtlas drives, not for terminal sessions.
  - *For ACP sessions* — `get_semantic_status` returns `WAITING` when an assistant turn completes. PowerAtlas can watch for that transition and immediately send the next prompt. No open technical question for ACP-driven sessions.
  - *For terminal sessions* — the session status is detected via `messages.jsonl` tail in the v3 session store, but PowerAtlas has no way to inject a new prompt into a terminal session it did not start. This half remains closed.

- **Skills support spike** — understand how argument passing works in both kiro-cli and Claude Code, unblocking three items above.

- **Plan-file shortcuts** — detect `plans/*.md` files and offer one-click `/qdev` buttons. Same unblock as *Template prompts*: the plan path rides the prompt line, not `$ARGUMENTS`.

---

## Workspace Intelligence

- **Session status extensions** — "stale /qdev never completed" heuristics and detecting fresh terminal sessions. Live status dots shipped in `260712_LIVE_SESSION_STATUS`; notifications shipped 2026-09-19. What is left is below.
  - *Stale /qdev detection* — `/qdev` writes a progress marker into the plan file on each phase; a session that last wrote a marker >24 h ago with a non-complete status is "stale". Would require reading plan files on every status poll — expensive. Deferred until status poll performance is better understood.
  - *Notifications for terminal-hosted sessions* — the ACP half shipped; this half did not, and cannot take the same route. PowerAtlas does not host a terminal session, so there is no frame to fire from: the only signal is the `messages.jsonl` tail the status poll already reads, which makes a poll-side hook the sole option. Nothing reads it today.
  - *Fresh terminal sessions* — sessions started in a terminal after PowerAtlas was launched are picked up on the next `refresh_stale_entries` tick (15–30 s). No gap for ACP sessions (PowerAtlas creates them). Terminal-session detection latency is bounded by the refresh interval, not by process monitoring.

- **Plan progress overlay** — show phase completion (e.g. "Phase 3/5") on workspace cards by reading plan files.

- **kiro-cli usage stats** — dashboard showing session counts, durations, and tool-usage patterns over time.

---

## Platform

- **Secret-aware env vars for custom launchers** *(shape a still open)* — credentials in launcher env blocks are in cleartext in `config.toml`; shape (a) is an OS keystore reference, shape (b) is an encrypted-at-rest blob. Both require a UI decision about how the user enters/updates credentials.

- **[P2b] Session stores PowerAtlas cannot see — closed, permanently inaccessible.** "Classic" sqlite conversations in `conversations_v2` (`%LOCALAPPDATA%\Kiro-Cli\data.sqlite3`) have no file on disk. PowerAtlas does not read this store (v2 support removed 2026-09-17, `data_kiro.py` deleted). `kiro-cli chat --list-sessions -f json` remains the only surface that exposes these sessions, tagging each entry with `source: "classic"` — cost ~2.13 s per query, cwd-scoped (not global). Adding a sqlite reader would require reintroducing v2 infrastructure for sessions kiro-cli itself no longer creates; the question is closed rather than parked. *(v3 sessions covered by the `kiro-cli-v3` provider, shipped 2026-08-18.)*

---

## Session Control & Integration

- **Creating a session in a workspace that has none** — cut from the picker because PowerAtlas has no folder browser; two candidate shapes described.
  - *Shape A* — an inline text field in the "new session" dialog for entering a path manually. Simple, but not discoverable for paths the user doesn't have memorized.
  - *Shape B* — a separate "add workspace" flow that opens a native folder browser and writes the path into `config.toml` as a pinned folder. More discoverable but adds a new surface.

- **Reach the operator away from the machine** — the desktop half shipped 2026-09-19 (`plans/done/`); what remains is the phone, and it is parked behind the TLS decision on measured rather than assumed grounds.
  - *The limit is measured, and stricter than this item used to assume.* The old wording accepted losing the phone case to a **sleeping** tab. Measured on the live deployment: on `http://<netbird-ip>:4915` the page is not a secure context, so `Notification.permission` reads `"denied"` — hard-denied, unpromptable, no user gesture can rescue it — and `navigator.serviceWorker` is absent. An **awake** remote tab cannot notify either, and Web Push is independently foreclosed. Both shipped surfaces reach the machine running PowerAtlas and nothing else.
  - *What it would take* — a secure context on the remote bind, nothing less: Web Push needs HTTPS and so does the plain `Notification` API. The reasoning against TLS holds only while WireGuard is the sole remote path; the cost of that choice is now precisely known rather than estimated.

- **Permission policy for unattended sessions** — the keystone. Decide `session/request_permission` by rule when nobody is watching, gated at the tool boundary rather than the prompt.
  - *Premise, corrected TWICE — read this before designing anything.* The item began as "drop `-a` and decide each request automatically". That was corrected on 2026-09-19 to "`-a` is gone and every request waits for a human, so an unattended session **stalls**". **Direct measurement the same day falsified that too: it does not stall, it runs.** An ACP session under the shipped `kiro_default` executed a shell command, read a file, and wrote a file — then, in a second probe, wrote to an **absolute path outside its working directory** — with `session/request_permission` raised **zero times across 5 sessions and ~10 tool invocations**. The failure mode of an unattended session is not a wasted slot. It is an agent running commands and writing anywhere on disk with nothing watching.
  - *What the ACP path actually offers, measured on kiro-cli 2.22.0 / KAS 0.66.0* — `acp --agent-engine v3` **rejects `-a`, `--trust-tools` and `--agent` alike** ("the following arguments are not supported with --agent-engine=v3"). There is no CLI trust knob on this path at all. Note the asymmetry with the terminal launcher, which is a different subcommand: `launcher.py:112` ships `chat --agent-engine v3 --trust-tools *`, and `chat` accepts the flag. So PowerAtlas's two spawn paths have different permission postures — the terminal one trusts everything explicitly, the ACP one trusts everything by default.
  - *Of the agent-definition levers, exactly one works* — both tested with the resulting `modeId` read back off `config_option_update`, so neither silently fell through to `vibe`. `allowedTools: ["read"]` alongside `tools: ["*"]` had **no effect** — shell and write still ran unprompted. `tools: ["read"]` **works**: the shell and write tools are removed from the inventory entirely and the agent reports having neither. That is binary availability per agent, not per-request approval — but it is the only containment measured working, and PowerAtlas can already select it per session through the `modeId` channel the task-mode picker uses.
  - *The old "what is settled" line is retired, and its provenance is why.* It read: "`session/request_permission` round-trips work in both directions; deny is genuinely enforced (measured 2026-07-31 on kiro-cli 2.16.0)". That measurement **predates the v2→v3 cutover** (`260911_ACP_V2_TO_V3_ENGINE_CUTOVER`), so it was almost certainly taken against the v2 engine. Whether the inline SC-9 UI was ever observed firing on v3 in practice — as opposed to being implemented against it — is unverified, and should be checked against `260908_ACP_V3_PRODUCTION_HARDENING`'s own evidence before anyone calls this a regression rather than a never-was.
  - *Not covered by the probe* — MCP tools, web/network tools, `delegate`, and deletion were not exercised; the finding is "nothing gated the three tool classes tested, on any config reachable", not "no gate exists anywhere".
  - *Shape, borrowed from Kiro Crew* — Crew gates at execution, not at the prompt: per-tool auto-approve gates, a bundled deny-pattern list for destructive shell commands, sensitive-path guards, and the rule that the layered checks still apply when approvals are set to auto. For PowerAtlas that is two halves. **The agent half**: a dedicated dispatch agent definition whose tool allow-list and shell allow/deny settings are narrowed at the source, so most routine requests never reach PowerAtlas at all — this is what the *lean dispatch agent* item below becomes. Verify the exact custom-agent config keys against the current kiro-cli build before designing on them. **The PowerAtlas half**: for requests that do arrive on a session marked unattended, approve read-class tools, deny shell and write unless an explicit pattern allows them, and after a bounded wait cancel the turn and mark the session as needing attention rather than leaving it hanging until the silence timeout.
  - *What is not settled* — the allow/deny pattern set itself, and whether an "unattended" flag is per session, per dispatch, or per agent definition. Neither needs a measurement; both need a decision.
  - *This item gates the entire Automation & Workflows section* — all six items there assume the session can run unattended. It also raises the priority of the `[SECURITY]` loopback token item: an auto-approving session behind an unauthenticated loopback API is a different proposition from an attended one.

- **A lean dispatch agent** — strip the full interactive-developer context before dispatching a narrow task; saves ~27k tokens per session (measured). As of 2026-09-19 this is the agent-definition half of the permission-policy item above, not a separate deliverable: the same agent file that narrows `resources` is where the tool allow-list lives.
  - *What is measured* — a `resources: []` agent costs ~46k tokens (the floor from cwd-driven context), versus ~73k for `kiro_default`. The delta is ~27k tokens, confirmed on kiro-cli 2.16.0. **Stale-when**: kiro-cli moves off 2.16.0 — re-measure before building on this figure (currently at 2.22.0).
  - *One open question* — whether skills the dispatched task invokes (e.g. `/qplan`, `/qdev`) still load correctly with a stripped `resources` list. Untested; the skills themselves arrive via `skill://` resolvers and may not depend on the resources list.

- **A "needs you" inbox** — one cross-session list of everything waiting on a human: pending permission requests, unanswered clarifying questions, and turns that finished while unwatched.
  - *What exists* — the rail's status grouping buckets Working / Waiting / Errored / Available / Locked; the static-green dot marks an unwatched turn end (its state-loss bug was fixed 2026-09-19, `ad4e8f9`); permission requests and clarifying questions render inline in the transcript of the session that raised them, and nowhere else.
  - *What is missing* — a permission request in a session you are not looking at is invisible until you open that session. Kiro Crew's activity view is the reference: every agent as one card, with its pending approval on the card. For PowerAtlas the cheapest shape is a "Needs attention" bucket in status grouping, fed by the supervisor's `_pending_permission` map and the clarifying-question state, with a count in the page title. This is now the missing half of the notifications that shipped 2026-09-19: a toast tells you *a* session needs you and lands you on the page, but nothing yet tells you *which* one, so the inbox is where that notification should lead.

- **A fresh git worktree per session** — a checkbox in the new-session picker that creates `git worktree add` under the workspace and starts the session there.
  - *Why* — worktree isolation per agent is the one feature Conductor, Crystal, Vibe Kanban and Claude Squad all share. Two `/acp` sessions created in the same workspace today edit the same working tree, and nothing warns.
  - *What it touches* — the picker (`acp.html` and the dashboard copy shipped 2026-09-19), `_handle_new`/`new_session` for the cwd, and the rail: a worktree path is a distinct cwd, so it becomes its own workspace group in the rail and in kiro-cli's session store unless grouped back under its parent. Decide the grouping before building; cleanup of finished worktrees is a second decision.

- **Spike: drive Claude Code over ACP through an adapter** — make `/acp` a second-provider surface by running a Claude Code ACP adapter as a second supervised process.
  - *Why it is open* — `plans/CLOSED_INVESTIGATIONS.md` closes *taking over* a Claude Code session already live in someone's terminal (the `messagingSocketPath` and remote-control entries). Starting a **new** Claude Code session over ACP from PowerAtlas is a different question, and one nobody has asked of the code. Kiro Crew is kiro-only by design, so this is where PowerAtlas's multi-provider shape would show.
  - *What it would cost* — the supervisor is kiro-specific in more than its binary: `_build_kas_session_params`, the `_kiro/*` extension methods, the token fulfilment, the session-store paths for `_lock_holder_v3`. A second provider means a driver abstraction, not a config change. Spike first: confirm an adapter exists for the installed Claude Code, that it answers `initialize`/`session/new`/`session/prompt` over stdio, and what its permission and session-persistence story is. Budget a week; exit with a go/no-go, not a feature.

- **Revisit `None` → `"working"` fallback** — unclassifiable sessions show as working; may warrant an explicit "unknown" state now that the fallback fires rarely.

---

## Misc

- **[SECURITY] Loopback API token — authenticate local callers to the `/api/*` and `/ws/acp` surfaces.** Today any local process that can reach `127.0.0.1:<port>` can create sessions, send prompts, delete sessions, and read the full session list, with no credential required. The `same_origin_guard` is CSRF defence only (POST-scoped, passes a loopback `Host` unconditionally) and `RemoteAccessGuard` is non-loopback only — neither checks whether the caller is PowerAtlas's own browser UI or a rogue script. Anything that reaches the API can create a session, send it a prompt and answer its own permission requests over `/ws/acp`, so it can execute arbitrary shell commands as the user; the inline permission prompt gates the person holding the page, not a script speaking the same protocol. (This sentence used to say `/acp` hardcodes `-a`; it does not, see the permission-policy item.) **Priority rises with the permission-policy item**: once a session can auto-approve, an unauthenticated loopback API in front of it is the sharpest surface on the machine.
  - *Proposed shape*: a secret written to `%LOCALAPPDATA%\power-atlas\local-secret` at first startup (same pattern as `remote-secret`). Required as a header (`X-PowerAtlas-Token`) on all `/api/*` and `/ws/acp` requests. Browser clients (the dashboard, `/acp` page) receive the token injected into the HTML at page-load time so they need no manual handling. Non-browser callers (agents, scripts, `Invoke-RestMethod`) must supply it explicitly — which is what "only allow what we choose" means in practice.
  - *What this does not change*: the browser UI works transparently; the remote surface is unaffected (it already has the device cookie); the loopback split between dashboard and `/acp` is unaffected.
  - *What this enables*: a deliberate opt-in for agent access — an agent that knows the token can drive PowerAtlas; one that does not cannot. The token is readable from disk by any process running as the same user, so this is not a hard security boundary against a fully-compromised session, but it raises the bar from "any process that makes an HTTP request" to "any process that reads a specific file first", and it makes the access explicit and auditable.
  - *Interaction with the sync-prompt endpoint*: if that endpoint is added (see *Reach the operator away from the machine* and the dispatch-agent item), it should require the token too — it is the sharpest surface in the API.
  - *Severity is measured, not projected — promoted to rank 1 on 2026-09-19.* This item was ranked below the keystone while its worst case read "an auto-approving agent behind an unauthenticated API", i.e. something the keystone would *create*. Measurement that day removed the conditional: an ACP session already executes shell commands and writes arbitrary absolute paths with **zero** permission prompts (see the permission-policy item). So the exposure is not a future consequence of shipping auto-approve — it is the current state, and the composition is arbitrary code execution as the user for any local process that can open a socket to the loopback port. Unlike the keystone, nothing here waits on kiro-cli: the fix is entirely PowerAtlas's.

- **[SECURITY — accepted, 2026-08-03] No NetBird access policy restricts this host, and that is now a decision rather than an oversight.** Measured 2026-07-31: `netbird status -d` enumerates **all 17** account peers in this host's network map, including machines belonging to other people (`akita`, `paros-g`, `nuc-chicago`, `ec2amaz-tv495hp`, `macbook-air-de-polestar`, …), so the stock `Default` (All → All) policy is still enabled.
  - *Measured 2026-08-03* — all inbound File and Printer Sharing rules (TCP 139, TCP 445) are **disabled** at the Windows Firewall level. The genuine exposure was UDP 137/138 (Network Discovery), admitted by two rules scoped to the Private profile. The WireGuard tunnel is classified Private, so those rules apply to NetBird peers.
  - *Decision* — the device cookie is the sole authorization layer for `/acp`. Creating a NetBird access policy scoped to this host's own devices restores the intended second layer (5 minutes in the NetBird console). The implementation does not depend on that policy being in force, so it is worth doing but not blocking.

- **Claude Code sidecar fields inventory** — full table of every field PowerAtlas reads (or could read) from `~/.claude/sessions/<pid>.json`.
