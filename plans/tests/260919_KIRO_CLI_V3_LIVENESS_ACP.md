# Test Plan: kiro-cli v3 liveness + ACP v3 multi-session/subagents

created: 2026-09-19
last_executed: 2026-09-19T13:40:00+02:00
approach: organic
target: kiro-cli-v3 liveness detection; kiro-cli ACP v3 supervisor with multiple sessions and subagents

## Topology note

A live PowerAtlas instance was running throughout planning (PID 41776, `python -m power_atlas --foreground`,
supervising `kiro-cli.exe acp --agent-engine v3` PID 37024). Its one ACP session
(`sess_57628995-8c05-4530-9f0b-cb31222fb858`) was idle (last turn ended 13:01:40, no activity since) at
research time. Per user decision (2026-09-19): **use the live instance directly** rather than standing up a
second isolated instance, including for the supervisor crash/respawn probe (kill the supervised `kiro-cli.exe`
child, not PowerAtlas itself — restarting PowerAtlas is never done autonomously, per this project's `AGENTS.md`).

## Environment & Resource Constraints

Inherits `plans/tests/HARNESS.md`. Additional resources for this plan:

| Name | Type | Availability | Constraints |
|---|---|---|---|
| live-powerAtlas-instance | environment | present at plan time (PID 41776) | do not restart/stop; read-only probing safe at any time; the supervised `kiro-cli.exe` child (PID 37024) may be killed once, deliberately, to test crash-recovery |
| kiro-cli-acp-v3-protocol | agent | via the live instance's `/acp` WS | no direct wire access from a test agent — all interaction goes through PowerAtlas's own `/acp` page/API |
| existing-subagent-fixtures | tool | always | `tests/test_web.py` (`_notify`, `TestAcpSubagent*` classes, `_crew_entry` helper) and `tests/acp_page.test.mjs` (`_testSetCrew`/`_testCrews`) drive crew/subagent code paths without a real kiro-cli fan-out |

## Risk hotspots (priority probe targets)

1. **Dashboard liveness gap for ACP-held kiro-cli-v3 sessions** (candidate, needs runtime confirmation) — `presence.py`'s
   only two liveness signals for kiro-cli-v3 (exact `--resume-id` argv match; cwd+recent-mtime fallback) may never
   match a session held by PowerAtlas's own single long-lived ACP agent process, which never carries `--resume-id`
   on argv and whose one OS `cwd()` cannot equal every workspace it serves. `/acp`'s own rail uses a separate,
   unaffected mechanism (`_lock_holder_v3`, reads `session.json` directly) so the two surfaces could disagree.
2. **Confirmed: `acp_prompt_silence_seconds` write/load bound mismatch** — `web.py:3839` accepts 60-86400 on write
   and reports `restart_required`; `acp.py:6417-6419`'s `apply_config()` clamp only accepts 60-7200 on load, silently
   keeping the old value (`log.error`, no UI surface) for anything above 7200. The other two ACP tunables
   (`acp_max_sessions`, `acp_idle_ttl_seconds`) have matching bounds on both paths — this one drifted.
3. **Dead code: kiro D32 orphan-lock guard in `presence.py`** — `_sidecar_records()` (309-353) only reads
   `~/.claude/sessions/`; no kiro-cli-v3 record is ever produced (confirmed via `git show 2e42cdf`, which removed
   the v2 `.lock` scan and left the `_KIRO_PROVIDERS` membership checks at lines 611-613/646/655-657 unreachable).
   The removed comment itself notes kiro-cli v3 never wrote `.lock` files even before removal — this was already
   dead for v3, not a regression from the v2 cutover.
4. **Supervisor crash recovery** — killing the supervised `kiro-cli.exe` process: does `_on_agent_death` fire
   promptly, do all in-flight sessions get `agent_died`, does presence.py's published set clear, does a subsequent
   session request cleanly respawn, and do the recovered session's transcripts remain resumable?
5. **Orphaned shell subprocesses** — README states `session/cancel` and the idle sweeper's `terminate` both leave
   agent-spawned shell subprocesses running until PowerAtlas exits. Verify this is still true and check whether the
   crash-kill path (item 4) behaves the same way or differently (a live child of a now-dead parent).
6. **Session-limit message accuracy** — `_session_limit_message()` has reportedly been wrong three times before
   (named a removed remedy). Verify the current text names a real, currently-working remedy.

## 1. kiro-cli-v3 liveness / status detection (`presence.py`, `status_classifier.py`, `data_kiro_v3.py`, `acp.py::_lock_holder_v3`)

### 1.1 Dashboard status dot for a kiro-cli-v3 session held by the ACP agent
- **what**: The index.html dashboard's Working/Waiting/Errored/closed dot for a session currently open in `/acp`.
- **how-to-reach**: Open the dashboard (`/`) for the workspace containing the live instance's open ACP session; observe its dot.
- **probes**: Compare the dashboard dot against `/acp`'s own rail dot for the *same* session at the *same* moment. Start a turn in `/acp` (or wait for the idle one to resume) and see whether the dashboard dot ever shows "working" while the turn runs.
- **oracle**: README lines ~81 ("Detected by matching the working directory of running claude/kiro-cli processes... also supports v3 kiro-cli sessions") implies this should work; source reading in risk hotspot #1 suggests it may not, for ACP-held sessions specifically.
- **risks**: False "closed"/idle reading while the session is genuinely active is the failure mode — a silent under-report, not a crash.

### 1.2 `/acp` rail availability (available / held-by-PowerAtlas / locked-by-other-process)
- **what**: Per-row availability computed by `_lock_holder_v3`, independent of presence.py.
- **how-to-reach**: `/acp` rail, any workspace with sessions.
- **probes**: Confirm the live session shows "held" while open here; confirm a session in a workspace with no PowerAtlas activity shows "available"; check behavior when `session.json`'s status field is stale (>1800s) but still nominally "held" — should self-heal to available.
- **oracle**: README "The left rail lists workspaces with their sessions... marks every visible row *available*, *held by PowerAtlas*, or *locked* by another process."
- **risks**: A live external process parked on an unanswered permission prompt past 1800s is a documented, accepted false-"available" — not a bug, but worth observing once if a natural opportunity arises.

### 1.3 Cross-provider dot consistency (claude-code vs. kiro-cli-v3)
- **what**: Both providers feed the same `_STATUS_PRIORITY` workspace-level rollup but via structurally different classifiers (`stop_reason` vs. `payload.type`).
- **how-to-reach**: A workspace with both a claude-code and a kiro-cli-v3 session.
- **probes**: Compare dot behavior/timing across providers for equivalent working/waiting/errored states.
- **oracle**: `status_classifier.py` `classify_claude` vs `classify_kiro_v3`.
- **risks**: A fix validated against one provider's tail format may not generalize to the other.

### 1.4 Semantic status classifier tail-read edge cases
- **what**: WORKING/WAITING/ERRORED/CLOSED derivation from the last ~64KB (widening to 2MB) of `messages.jsonl`.
- **how-to-reach**: Library-level — existing unit tests in `tests/test_web.py` / classifier-specific tests.
- **probes**: Confirm the documented "mid-line read defaults to working" fallback and the "2 of last 5 failures without recovery → errored" rule still hold; run existing tests rather than re-deriving.
- **oracle**: `status_classifier.py` docstrings (lines ~135-186, ~224-392).
- **risks**: Cache staleness (5s classifier cache + 3s presence snapshot TTL) compounds to ~8s worst-case dot lag — acceptable per design, just document if observed.

### 1.5 Dead-code cleanup candidate: kiro D32 guard in presence.py
- **what**: `_sidecar_records()` never emits a kiro-cli-v3 record; the `_KIRO_PROVIDERS` membership checks inside `_scan()`'s sidecar loop (lines 611-613, 646, 655-657) are unreachable.
- **how-to-reach**: Code inspection only (confirmed, see Risk hotspot #3).
- **probes**: N/A — already confirmed via `git show 2e42cdf`. Scoped-out from runtime probing.
- **oracle**: The removed comment's own empirical note ("the installed build never writes a `.lock` file for a v3 session").
- **risks**: None from removing dead code; fails safe today (unreachable branch just never runs).

## 2. ACP v3 Supervisor + multi-session lifecycle (`acp.py::_Supervisor`)

### 2.1 Session-slot capacity enforcement (`acp_max_sessions`)
- **what**: `at_capacity()` gate on `session/new` and `session/load`, counting `len(sessions) + _reserved`.
- **how-to-reach**: `/acp` — open sessions up to the configured cap (default 8; check live instance's actual `config.toml` value first, do not assume 8).
- **probes**: Confirm both create controls disable at cap with the documented reason; confirm the reservation (not just committed count) blocks a concurrent second `session/new` race if two can be fired close together from two tabs.
- **oracle**: README "at the session limit both create controls are disabled and say why, rather than failing after the press."
- **risks**: This is a shared, currently-active resource (the live instance's real session slots) — do not leave test sessions open at the end; close everything this plan creates.

### 2.2 Session-limit message accuracy
- **what**: `_session_limit_message()` — has been wrong in the past per its own docstring.
- **how-to-reach**: Trigger the at-capacity state (2.1) and read the exact message shown.
- **probes**: Verify the remedy it names is a real, currently-available control (not "close from its tab" if that's since become self-defeating, not a removed restart flow).
- **oracle**: README's create-picker paragraph; `_session_limit_message()`'s own docstring history.
- **risks**: Low severity (UX text only) but user-facing and has drifted before.

### 2.3 Supervisor crash detection + recovery
- **what**: `_on_agent_death` → `_discard`/`_detach`/`_dispose` → next request respawns fresh.
- **how-to-reach**: Live instance. **Heads-up given to user before executing.**
- **probes**: Kill the supervised `kiro-cli.exe` (PID captured fresh at execution time, not assumed to still be 37024) directly (not via PowerAtlas). Observe: (a) `orchestrator.log` for the `_on_agent_death` path firing, (b) whether the open `/acp` tab receives an `agent_died` frame, (c) whether `presence.publish_acp_sessions` clears (published set empty, pid None), (d) whether opening/resuming a session afterward cleanly respawns a new `kiro-cli.exe`, (e) whether the pre-crash session's transcript is still resumable afterward (on-disk data untouched).
- **oracle**: acp.py `_on_agent_death` (3657), `_publish_live` (4743); README implies transcripts persist independent of process life.
- **risks**: **This is the single most disruptive probe in this plan.** It will interrupt the live session's process (already idle — see Topology note) but not PowerAtlas itself. Any shell subprocess the agent had spawned (should be none, since idle) would be orphaned per the known residual (Risk hotspot #5) — check for orphans before and after via `tasklist`.

### 2.4 Idle session reclaim (sweeper)
- **what**: `_sweepable`/`_sweep_once`, 60s tick, `acp_idle_ttl_seconds` (default 1800s) since last *use* (not last activity).
- **how-to-reach**: Library-level primarily (a live 30+ minute wait is expensive) — read `tests/test_web.py` sweeper tests; only observe live behavior opportunistically if a session naturally crosses the threshold during this run.
- **probes**: Confirm the six preconditions in `_sweepable` (registered, idle past TTL, no subscriber, no turn/close/load in flight) via existing tests rather than a real 30-minute wait.
- **oracle**: acp.py `_sweepable` (6207), README "Idle sessions are reclaimed."
- **risks**: Full e2e wait is a ≥30-minute cost — mark library-level (partially-verified) unless a natural opportunity arises.

### 2.5 Config bound mismatch: `acp_prompt_silence_seconds`
- **what**: Write-path validator (web.py) accepts 60-86400; load-path clamp (acp.py `apply_config`) only accepts 60-7200.
- **how-to-reach**: Code inspection — **already confirmed**, no runtime probe needed (see Risk hotspot #2 for exact citations).
- **probes**: N/A, confirmed via direct source read.
- **oracle**: `web.py:3839` vs `acp.py:6417-6419`; the other two ACP tunables' bounds match exactly, so this is a drift, not an intentional design difference.
- **risks**: Silent misconfiguration — a value the settings UI accepted and reported `restart_required: true` for is dropped on that very restart with only a `log.error`, no UI feedback.

### 2.6 Cross-process workspace lock detection (`_lock_holder_v3`)
- **what**: Reads `session.json`'s `status` field directly (no pid/lock file for v3), with an 1800s staleness self-heal.
- **how-to-reach**: `/acp` rail (also covered from the liveness-surface side in 1.2 — this entry is the supervisor-side mechanics).
- **probes**: Confirm malformed/unreadable-but-fresh `session.json` reads as held (fail-safe direction); confirm a session with no `session.json` at all reads as available unconditionally.
- **oracle**: acp.py `_lock_holder_v3` (2339-2450) docstring.
- **risks**: Callers must test `is not None`, not truthiness (`_V3_HOLDER_PID_UNKNOWN` sentinel is `-1`) — a regression here would be silent.

## 3. ACP v3 subagents / fan-out crew panel (`acp.py` crew machinery + `acp.html`)

### 3.1 Crew panel live population during a fan-out (library-level)
- **what**: `_on_agent_subtask_open`/`_on_agent_subtask_update` → `_emit_subagents_frame` → client `setCrew`/`renderCrewPanel`.
- **how-to-reach**: Library-level via `tests/test_web.py`'s existing subagent fixtures (`_notify`, `_crew_entry`) and `tests/acp_page.test.mjs`'s `_testSetCrew`. A real live fan-out is nondeterministic and token-costly — **flagged cost, not pursued live** unless the idle session naturally produces one during this run.
- **probes**: Run the existing pytest/`node` suites covering `TestAcpSubagent*` and crew-panel JS tests; confirm they currently pass (regression check) rather than re-deriving new cases.
- **oracle**: README's "inline crew panel appears directly below the spawner tool call... updates live, mid-fan-out" paragraph; `plans/done/260909-1127_ACP_V3_PRODUCTION_HARDENING.md` Phase 4 (SC-4, SC-5) and its review-log findings (BUG-3 fan-out filtering, #1-#6/#10/#16/#18/#20-21).
- **risks**: v3 has no wire-level batch/fan-out-id — the fan-out id is a synthesized heuristic; two overlapping fan-out waves (both spawned before either finishes) are documented to merge into one panel (accepted simplification, not a bug — do not re-report as new).

### 3.2 Crew finalization on cancel / turn-end / session-close
- **what**: Three independent backstops (`_mark_crew_done` on cancel, the turn-end `finally` block, `_evict_crew_children` on close) that force any still-running crew entry to `done` so nothing hangs the panel forever.
- **how-to-reach**: Library-level, existing tests (`test_cancel_skips_cascade_when_no_crew`, `test_closing_the_parent_tears_down_its_crew`, etc.).
- **probes**: Run existing tests; confirm all three backstops are independently exercised (a parent turn that itself hangs with no end_turn/error is the one documented gap — code-inspection only, not practically triggerable in this run).
- **oracle**: acp.py comments at the three call sites; README's cancel-does-not-kill-subprocess paragraph (adjacent risk).
- **risks**: None new — this is a regression check against already-hardened, well-tested logic.

### 3.3 Crew panel reconnect mid-fan-out
- **what**: Subagents frames are not recorded in replay history; a dedicated resend-on-reconnect path exists specifically to cover a WS drop during an active fan-out.
- **how-to-reach**: Library-level — existing regression test for this exact scenario (was itself a review-cycle fix).
- **probes**: Run the existing test; do not attempt to reproduce live (requires timing a WS drop against a real in-flight fan-out).
- **oracle**: acp.py `_handle_subscribe`/reconnect gate (~5440-5469), README's "the page reconnects automatically with exponential backoff" paragraph.
- **risks**: None new beyond what's already covered by the cited regression test.

### 3.4 Client-side crew UI: multiple concurrent fan-outs, elapsed-timer lifecycle, no-auto-dismiss
- **what**: Independent per-toolCallId crew slots; timer starts only while ≥1 entry is running and freezes on all-done; panel persists after completion (explicitly not auto-dismissed, a past regression).
- **how-to-reach**: `tests/acp_page.test.mjs` (`node tests/acp_page.test.mjs`) — per this project's `AGENTS.md`, this suite is not part of pytest/CI and must be run separately when touching `acp.html`'s inline script.
- **probes**: Run the suite; confirm the "crew timer does not fire after session release" and "no-auto-dismiss" regression tests pass.
- **oracle**: `acp_page.test.mjs` test names/comments themselves state the past bug each guards against.
- **risks**: None new — regression check.

## Scoped-out (not given full briefs — reason noted)

- **Second standalone PowerAtlas instance for crash testing** — user explicitly chose to use the live instance instead (2026-09-19 decision); no second instance stood up.
- **Full 30+ minute idle-sweep e2e wait** — cost disclosed in 2.4; library-level only this run.
- **Triggering a real multi-stage kiro-cli fan-out live** — nondeterministic and token-costly (disclosed in 3.1); library-level via existing fixtures unless one occurs naturally.
- **Remote access (`/remote-auth`, NetBird) interaction with ACP** — out of scope for this plan's two named targets; covered separately by `plans/tests/260701_POWERATLAS.md` if needed.
- **presence.py's claude-code sidecar path** (`_SIDECAR_SKEW_S`, `_PROC_START_TOLERANCE_S`, FILETIME conversion) — out of scope; this plan targets kiro-cli-v3 specifically.

## Coverage manifest (for run mode)

| # | Feature | Status |
|---|---|---|
| 1.1 | Dashboard dot for ACP-held session | covered — live probe, refuted (dot logic correctly bypasses `live` for held rows) |
| 1.2 | `/acp` rail availability | covered — live probe confirmed `held`/self-heal behavior |
| 1.3 | Cross-provider dot consistency / unread marking | fixed — root-caused the per-tick `_prevSessionStatus` eviction bug (see acp.html commit) |
| 1.4 | Classifier tail-read edge cases | covered (library) — existing tests pass, no new probes needed |
| 1.5 | Dead-code cleanup candidate | fixed — stale comments corrected in `presence.py` |
| 2.1 | Session-slot capacity enforcement | scoped-out — no capacity probe run against the live instance (would have consumed real session slots the user is using); library tests pass |
| 2.2 | Session-limit message accuracy | covered — code inspection, current text is accurate, no defect |
| 2.3 | Supervisor crash recovery | covered — live probe (killed supervised `kiro-cli.exe`), confirmed a real gap (see findings report) |
| 2.4 | Idle session reclaim | partially-verified (library) — existing tests pass; no 30+ min live wait attempted |
| 2.5 | Config bound mismatch | fixed — `acp.py` bound widened to match write path |
| 2.6 | Cross-process workspace lock | covered — exercised as part of 2.3's live probe (`_lock_holder_v3` correctly reported `held`/self-heal) |
| 3.1 | Crew panel live population | partially-verified (library) — existing pytest + JS fixtures pass; no live fan-out triggered (cost disclosed) |
| 3.2 | Crew finalization backstops | covered (library) — existing tests pass |
| 3.3 | Crew panel reconnect | covered (library) — existing tests pass |
| 3.4 | Client-side crew UI | covered (library) — existing `acp_page.test.mjs` tests pass |

### Run status (2026-09-19)
Plan authored via 3 parallel research agents (one per component), then executed in the same session: 2 live-instance probes (dashboard-dot check, supervisor crash-kill), a full pytest + `acp_page.test.mjs` regression pass, and a git-bisect of pre-existing JS test failures via a sparse worktree.

**Follow-up pass (same session, user requested "fix all findings unless they require a design decision"):** root-caused and fixed the "mark unread on turn end" failure the initial pass had only reported as pre-existing — `railRefreshStates()`'s per-call `_prevSessionStatus` eviction was wiping entries written by sibling calls (once per workspace group, plus pinned) within the same poll tick, so the unread indicator almost never fired for a real account with more than one workspace or any pinned session. Also root-caused and fixed/updated the remaining 4 pre-existing `acp_page.test.mjs` failures and the 1 pre-existing `test_web.py` failure: 3 were stale tests asserting behavior that later, deliberate commits had already replaced (skill-selection autocomplete, dashboard-link removal, group-toggle auto-expand-children, `openInAcp` removal) and were updated or removed to match; 1 was a genuine test-harness gap (`setSelectionRange` never stubbed on the fake DOM element) and was fixed in the harness. Full pytest (1895 passed, 2 skipped) and `acp_page.test.mjs` (450 passed) are both green.

**Left unfixed, by design:** finding #1 (supervisor crash-detection blind spot when the wrapped `kiro-cli.exe` dies but its `node.exe` child survives) — genuinely needs a design decision (proactive watchdog vs. accepting lazy recovery) and was intentionally left for `/qexplore` → `/qplan`.
