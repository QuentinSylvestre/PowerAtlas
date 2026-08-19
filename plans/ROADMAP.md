# PowerAtlas — Roadmap

## Table of Contents

### Priority ranking
- [Ranking table](#where-to-start--ranked-2026-08-04) — 8 items ranked by payoff/effort, with tier and one-line rationale each

### Automation & Workflows
- **Dispatch no-interactive tasks** — fire a kiro-cli task without a terminal; `--no-interactive` leaves no session trail so ACP is the right path, but unattended safety is still unsettled
- **Open session with a prompt or skill** — prompt delivery and skill loading are proven; passing skill arguments (`$ARGUMENTS`) is not yet verified
- **Template prompts** — save reusable per-workspace prompts; blocked on the same `$ARGUMENTS` question as above
- **Scheduled tasks** — cron-like recurring kiro-cli launches; mechanism is measured and process cost is known, but the auto-permissions gate must come first
- **Chained launches** — when a session finishes, automatically start the next one; works for sessions PowerAtlas drives, not for terminal sessions
- **Skills support spike** — understand how argument passing works in both kiro-cli and Claude Code, unblocking three items above
- **Plan-file shortcuts** — detect `plans/*.md` files and offer one-click `/qdev` buttons; same `$ARGUMENTS` blocker

### Workspace Intelligence
- **Session status extensions** — "stale /qdev never completed" heuristics, sound notifications, and detecting fresh terminal sessions (base already shipped)
- **Plan progress overlay** — show phase completion (e.g. "Phase 3/5") on workspace cards by reading plan files
- **kiro-cli usage stats** — dashboard showing session counts, durations, and tool-usage patterns over time

### Platform
- **Secret-aware env vars for custom launchers** *(shape a still open)* — credentials in launcher env blocks are in cleartext; serving them was fixed, storing them safely is not yet
- **Parked items** — invisible sqlite sessions · usage stats · plan-progress overlay · creating a session in a workspace with no prior sessions · two SECURITY items
- **`launch_custom` env scrub excluded (follow-up)**: CLAUDE_CODE_* markers are not scrubbed from `launch_custom`-launched sessions — user-defined scripts may rely on inherited environment. See `plans/done/260818_ACP_ENV_MARKER_AND_OVERLAY_STEERING.md` Follow-up #2.
- **`launch_terminal` env scrub excluded (follow-up)**: `launch_terminal` (~`launcher.py:595`) opens a bare shell without env scrubbing — the user manually starts a process inside it. Follow-up #5 of the same plan.
- **`kiro-cli-v3` liveness attribution (follow-up)**: `presence.py`'s `_match_provider` always returns `"kiro-cli"` (first dict hit) for resumed v3 sessions — live status dot appears on the v2 provider row, not the v3 row. Fix: check whether session ID starts with `sess_` in `_scan()` and route to `"kiro-cli-v3"`. Tracked as Follow-up #2 of `260818-2227_KIRO_CLI_V3_DASHBOARD_SUPPORT.md`.

### Session Control & Integration
- **Creating a session in a workspace that has none** — cut from the picker because PowerAtlas has no folder browser; two candidate shapes described
- **Tell the operator a turn ended** — push notification when a long task finishes; cheapest version uses the existing WebSocket but fails when the phone sleeps the tab
- **Auto-mode for `/acp` permissions** — drop `-a` and decide each request automatically; latency is measured and fine, accuracy against adversarial inputs is the open question
- **A lean dispatch agent** — strip the full interactive-developer context before dispatching a narrow task; saves ~27k tokens per session (measured)
- **Revisit `None` → `"working"` fallback** — unclassifiable sessions show as working; may warrant an explicit "unknown" state now that the fallback fires rarely
- **[P2b] Session stores PowerAtlas cannot see** — 11 classic sqlite sessions still invisible; v3 now covered

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

## Where to start — ranked 2026-08-04

> A ranking by payoff per unit of effort, not a plan. **It is a snapshot and it decays**: it was
> written when 21 items were live, and every shipped or closed item shifts it. Re-rank rather than
> trusting a stale order — the reasoning for each item lives in the item itself, and this table only
> records the comparison between them.
>
> **What would invalidate it**: anything under Tier 3 shipping (it gates six items); a re-measurement
> that moves the dispatch-agent figure; or the auto-mode accuracy work returning a bad result, which
> would push every unattended item from "blocked" to "abandoned".

| # | Item | Tier | Why here |
|---|---|---|---|
| 1 | *Revisit the `None` → `"working"` fallback* | hours | Small, self-contained, last survivor of the transcript-tail fix |
| 2 | *A PowerAtlas dispatch agent* | days | Now sized: 27,223 tokens/session, and MCP turned out to cost zero context so the change is `resources` alone. One open question left |
| 3 | *Secret-aware handling for custom-launcher env vars* — shape (a) | week | Ambient exposure is closed; this is the durable answer, not the urgent one |
| 4 | *Tell the operator a turn ended* | week | Cheap version fails when the phone sleeps the tab; the real one reopens the declined-TLS decision |
| 5 | *An auto-mode for `/acp` permissions* | **keystone** | Gates all six `## Automation & Workflows` items. Latency settled; **accuracy entirely unmeasured**, and that is the real gate |

**Parked, deliberately**: [P2b] invisible stores · usage stats · plan-progress
overlay · creating a session in a workspace that has none · the accepted `[SECURITY]` item (carries its
own reopen condition).

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
  - *Unattended posture* — an unattended session runs with `-a` (trust all tools). Any prompt it receives is executed verbatim, with no confirmation gate. The questions are whether a kiro-cli session dispatched headlessly can be trusted to stay in-scope, and how badly it behaves when the task goes wrong (loops, eats resources, calls external APIs unexpectedly).
  - *Exit condition for this item* — a framework for evaluating prompt-execution risk, analogous to what `allowedTools` provides in MCP. The plan for the auto-mode item below (item 5 in the ranking) is the most likely path.

- **Open session with a prompt or skill** — prompt delivery and skill loading are proven; passing skill arguments (`$ARGUMENTS`) is not yet verified.
  - *`$ARGUMENTS` — measured on kiro-cli 2.14.2, 2026-07-26.* Slash-command argument passing uses `$ARGUMENTS` in the SKILL.md body (e.g. `/qdev plans/my-plan.md` expands to `$ARGUMENTS` → `plans/my-plan.md`). The expansion is handled by kiro-cli's own command parser, not by PowerAtlas. Whether a prompt string containing `$ARGUMENTS` is expanded by the model or by the CLI is unverified — a test session reliably received the literal string `$ARGUMENTS`. Until this is verified, skill invocations with arguments are not reliably deliverable.

- **Template prompts** — save reusable per-workspace prompts; blocked on the same `$ARGUMENTS` question as above.

- **Scheduled tasks** — cron-like recurring kiro-cli launches; mechanism is measured and process cost is known, but the auto-permissions gate must come first.
  - *Process cost* — measured 2026-07-26: each kiro-cli ACP session costs ~161 MB RSS and 3 processes. A scheduled task that accumulates open sessions will exhaust memory; sessions need to be closed when their tasks complete. Closure via `session/terminate` or idle-TTL is measured and works.
  - *Auto-permissions gate* — a scheduled task has no human watching the permission dialogs. Whether `-a` is acceptable for scheduled work is the same question as the dispatch item above.

- **Chained launches** — when a session finishes, automatically start the next one; works for sessions PowerAtlas drives, not for terminal sessions.
  - *For ACP sessions* — `get_semantic_status` returns `WAITING` when an assistant turn completes. PowerAtlas can watch for that transition and immediately send the next prompt. No open technical question for ACP-driven sessions.
  - *For terminal sessions* — the session status is detected via `~/.kiro/sessions/cli/<id>.jsonl` tail, but PowerAtlas has no way to inject a new prompt into a terminal session it did not start. This half remains closed.

- **Skills support spike** — understand how argument passing works in both kiro-cli and Claude Code, unblocking three items above.

- **Plan-file shortcuts** — detect `plans/*.md` files and offer one-click `/qdev` buttons; same `$ARGUMENTS` blocker.

---

## Workspace Intelligence

- **Session status extensions** — "stale /qdev never completed" heuristics, sound notifications, and detecting fresh terminal sessions.
  - *Base shipped.* Live status dots (working/waiting/errored) shipped in `260712_LIVE_SESSION_STATUS`. The heuristics and notifications below are incremental.
  - *Stale /qdev detection* — `/qdev` writes a progress marker into the plan file on each phase; a session that last wrote a marker >24 h ago with a non-complete status is "stale". Would require reading plan files on every status poll — expensive. Deferred until status poll performance is better understood.
  - *Sound notifications* — OS toast on Working→Waiting/Errored; `260731_ACP_REMOTE_CLIENT_PRODUCTIZATION` shipped Windows WinRT + Linux `notify-send` for the browser surface; a generic hook for terminal sessions is unimplemented.
  - *Fresh terminal sessions* — sessions started in a terminal after PowerAtlas was launched are picked up on the next `refresh_stale_entries` tick (15–30 s). No gap for ACP sessions (PowerAtlas creates them). Terminal-session detection latency is bounded by the refresh interval, not by process monitoring.

- **Plan progress overlay** — show phase completion (e.g. "Phase 3/5") on workspace cards by reading plan files.

- **kiro-cli usage stats** — dashboard showing session counts, durations, and tool-usage patterns over time.

---

## Platform

- **Secret-aware env vars for custom launchers** *(shape a still open)* — credentials in launcher env blocks are in cleartext in `config.toml`; shape (a) is an OS keystore reference, shape (b) is an encrypted-at-rest blob. Both require a UI decision about how the user enters/updates credentials.

- **[P2b] Session stores PowerAtlas cannot see** — 11 classic sqlite conversations in `conversations_v2` (`%LOCALAPPDATA%\Kiro-Cli\data.sqlite3`) have no file on disk and appear in neither candidate. PowerAtlas already merges their *cwds* for workspace discovery but not the sessions themselves. `kiro-cli chat --list-sessions -f json` is the only source that unifies all three stores (`v2`/`v3`/`classic`), tagging each entry with `source` — cost ~2.13 s per query, cwd-scoped (not global), so covering 58 workspaces means 58 spawns. *(v3 sessions now covered by the `kiro-cli-v3` provider, shipped 2026-08-18.)*

---

## Session Control & Integration

- **Creating a session in a workspace that has none** — cut from the picker because PowerAtlas has no folder browser; two candidate shapes described.
  - *Shape A* — an inline text field in the "new session" dialog for entering a path manually. Simple, but not discoverable for paths the user doesn't have memorized.
  - *Shape B* — a separate "add workspace" flow that opens a native folder browser and writes the path into `config.toml` as a pinned folder. More discoverable but adds a new surface.

- **Tell the operator a turn ended** — push notification when a long task finishes; cheapest version uses the existing WebSocket but fails when the phone sleeps the tab.
  - *Cheapest version* — ACP already emits Working→Waiting transition events; the `/acp` WebSocket client receives them. A notification banner on the `/acp` page works while the tab is open. When the phone sleeps the tab, the WebSocket drops and the notification is lost.
  - *Durable version* — requires a push service (Web Push API with a service worker, or a native OS push channel). Both reopen the TLS decision (the remote bind uses plain HTTP over WireGuard; Web Push requires HTTPS; the reasoning against TLS holds only while WireGuard is the sole remote path).

- **Auto-mode for `/acp` permissions** — drop `-a` and decide each request automatically; latency is measured and fine, accuracy against adversarial inputs is the open question.
  - *What is settled* — `session/request_permission` round-trips work in both directions; deny is genuinely enforced (measured 2026-07-31 on kiro-cli 2.16.0); latency is ~1.2 s per permission prompt, acceptable for interactive use.
  - *What is not settled* — whether a policy that approves/denies based on tool name and input is accurate enough for unattended use. A false positive approves a destructive command; a false negative blocks a routine read. Neither failure mode has been evaluated against a realistic workload.
  - *This item gates the entire Automation & Workflows section* — all six items there assume the session can run unattended.

- **A lean dispatch agent** — strip the full interactive-developer context before dispatching a narrow task; saves ~27k tokens per session (measured).
  - *What is measured* — a `resources: []` agent costs ~46k tokens (the floor from cwd-driven context), versus ~73k for `kiro_default`. The delta is ~27k tokens, confirmed on kiro-cli 2.16.0.
  - *One open question* — whether skills the dispatched task invokes (e.g. `/qplan`, `/qdev`) still load correctly with a stripped `resources` list. Untested; the skills themselves arrive via `skill://` resolvers and may not depend on the resources list.

- **Revisit `None` → `"working"` fallback** — unclassifiable sessions show as working; may warrant an explicit "unknown" state now that the fallback fires rarely.

---

## Misc

- **[SECURITY] Loopback API token — authenticate local callers to the `/api/*` and `/ws/acp` surfaces.** Today any local process that can reach `127.0.0.1:<port>` can create sessions, send prompts, delete sessions, and read the full session list, with no credential required. The `same_origin_guard` is CSRF defence only (POST-scoped, passes a loopback `Host` unconditionally) and `RemoteAccessGuard` is non-loopback only — neither checks whether the caller is PowerAtlas's own browser UI or a rogue script. Because `/acp` hardcodes `-a`, anything that reaches the API can execute arbitrary shell commands as the user with no confirmation.
  - *Proposed shape*: a secret written to `%LOCALAPPDATA%\power-atlas\local-secret` at first startup (same pattern as `remote-secret`). Required as a header (`X-PowerAtlas-Token`) on all `/api/*` and `/ws/acp` requests. Browser clients (the dashboard, `/acp` page) receive the token injected into the HTML at page-load time so they need no manual handling. Non-browser callers (agents, scripts, `Invoke-RestMethod`) must supply it explicitly — which is what "only allow what we choose" means in practice.
  - *What this does not change*: the browser UI works transparently; the remote surface is unaffected (it already has the device cookie); the loopback split between dashboard and `/acp` is unaffected.
  - *What this enables*: a deliberate opt-in for agent access — an agent that knows the token can drive PowerAtlas; one that does not cannot. The token is readable from disk by any process running as the same user, so this is not a hard security boundary against a fully-compromised session, but it raises the bar from "any process that makes an HTTP request" to "any process that reads a specific file first", and it makes the access explicit and auditable.
  - *Interaction with the sync-prompt endpoint*: if that endpoint is added (see *Tell the operator a turn ended* and the dispatch-agent item), it should require the token too — it is the sharpest surface in the API.

- **[SECURITY — accepted, 2026-08-03] No NetBird access policy restricts this host, and that is now a decision rather than an oversight.** Measured 2026-07-31: `netbird status -d` enumerates **all 17** account peers in this host's network map, including machines belonging to other people (`akita`, `paros-g`, `nuc-chicago`, `ec2amaz-tv495hp`, `macbook-air-de-polestar`, …), so the stock `Default` (All → All) policy is still enabled.
  - *Measured 2026-08-03* — all inbound File and Printer Sharing rules (TCP 139, TCP 445) are **disabled** at the Windows Firewall level. The genuine exposure was UDP 137/138 (Network Discovery), admitted by two rules scoped to the Private profile. The WireGuard tunnel is classified Private, so those rules apply to NetBird peers.
  - *Decision* — the device cookie is the sole authorization layer for `/acp`. Creating a NetBird access policy scoped to this host's own devices restores the intended second layer (5 minutes in the NetBird console). The implementation does not depend on that policy being in force, so it is worth doing but not blocking.

- **Claude Code sidecar fields inventory** — full table of every field PowerAtlas reads (or could read) from `~/.claude/sessions/<pid>.json`.
