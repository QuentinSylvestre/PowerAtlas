# Dashboard Overview Rehaul: Quota Meters, Interrupted-Session Resume and Git Status

> **Date**: 2026-10-09
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Rehaul the dashboard Overview; add Claude/Codex quota meters, a quota-interrupted session list with quick and scheduled resume, repo change status (branch in the rail, status in the Overview); fix the stray copy glyph in the panel header

---

## Intent

### Problem statement & desired outcomes

The Overview answers what is running, which plans are in flight and how work went over 14 days. It cannot say how much of the Claude or Codex quota is left, which sessions stopped because a quota ran out, or which projects hold uncommitted or unpushed work. PowerAtlas has no quota or limit handling at all: a Claude Code limit hit is rendered as an ordinary assistant reply, and Claude Code tiles carry no status.

Desired outcome, in the owner's words: a complete rehaul and polish of the Overview, drawing on the best existing usage-monitor projects, with these additions:

- The 5-hour and weekly quota status with the reset date and time.
- A quick view of sessions interrupted by a quota limit, with the reset time and "quick resume" and "schedule resume" buttons (send "resume", or a custom prompt, one minute after the reset).
- A quick view of which projects have uncommitted or unpushed changes.
- Removal of a stray copy icon that appears at the left of the "Debug log" pill in the transcript view.

### Success criteria

- SC-1 (artifact): the copy glyph is hidden whenever no session id is attached, on both `/` and `/acp`. A source-reading test pins the `[hidden]` rule.
- SC-2 (meters): a quota strip at the top of the Overview shows Claude 5-hour and weekly and Codex 5-hour and weekly. Each meter shows percent used, an elapsed-time tick, a pace marker, a threshold cue that does not rely on color, and the reset as both a countdown and a clock time (with the date for weekly). Unknown or stale data reads "no live data" or "stale since HH:MM", never 0%.
- SC-3 (Claude source): PA reads a snapshot file written by the owner's statusline script and supplies the snippet to add. PA never edits `~/.claude/settings.json` and never reads credentials.
- SC-4 (interrupted list): a Claude Code session is listed when its last conversation line is a quota hit. The row shows the window, the reset clock time and countdown, Quick resume and Schedule resume. At the reset time the row reads "Quota reset, ready to resume". It leaves on continuation, dismissal, or 48 hours after the reset.
- SC-5 (quick resume): with the terminal closed, PA opens a Windows Terminal tab running `claude --resume <id> "<prompt>"` in the session's folder. With the terminal still open, PA types the prompt into that console, but only when the gates in Q11 hold.
- SC-6 (schedule): a schedule fires at reset plus 60 seconds, survives a PA restart if the fire time is still ahead, and is dropped (status refreshed) if the deadline passed while PA was down. A failed resume that shows a later reset time re-arms once. A launch error does not retry. No new conversation line within 3 minutes of a launch or injection shows "no activity since resume" and a toast.
- SC-7 (kiro-cli): a kiro-cli usage-limit stop is detected and shown with "daily limit, resets tomorrow". No countdown, no auto-resume.
- SC-8 (git): the rail workspace header shows the branch. The Overview shows status chips per repo: changed, untracked (dim), ahead, and "no upstream" only when the branch holds commits on no remote.
- SC-9 (layout): the Overview order is quota strip, "Needs attention" row, Live now, then Active plans and Usage side by side when wide. The prior Overview invariants hold (Home button, Escape, 2 s tile polling gated on visibility, `textContent` only, the signed-out check).
- SC-10 (extras): toasts on a hit and on a reset with waiting sessions (existing notifications switch, deduplicated); a 7-day quota history strip.
- SC-11 (security): all new routes are loopback-only (cookie plus Origin check), session ids validated, prompts length-capped and passed as an argument list (never through a shell), injection only into the PID the presence data gives for that session and only if the image is `claude.exe`.
- SC-12 (verification): `node tests/acp_page.test.mjs` and the pytest suite pass; new behavior has tests in existing files; live QA against a restarted instance with synthetic transcripts.

### Scope boundaries & non-goals

- Out: an Anthropic status badge, a token-based burn-rate forecast.
- Out: typing into an open Codex terminal (a thread that holds its writer lock is refused, and the owner continues it there). Codex hit detection was out until a real record was observed; see Q12.
- Out: in-place resume for kiro-cli sessions (detect-and-show only).
- Out: reading the Claude OAuth token or calling the undocumented usage endpoint.
- Out: running resumes headless (`claude -p`).

---

## Exploration Discovery

<!-- Direct implementation was chosen by the owner (no /qplan): this section is the implementation record. -->

### Existing patterns & constraints

- The Overview is `#dashOverview` in `src/power_atlas/templates/index.html` (three sections: Live now, Active plans, Usage), styled by `.dash-ov-*` in `style.css`, fed by `/api/dashboard/overview/summary` (60 s) and `/api/dashboard/overview/live` (2 s). Backend in `overview.py` and `web.py`. Prior design: `plans/done/260928-1249_DASHBOARD_OVERVIEW_LIVE_TAILS_PLANS_USAGE.md` (SC-1 to SC-10).
- Tile payloads are an explicit allow-list (`overview.py` tile dict); a new field is dropped unless named there. Per-file parse results are memoised on `(mtime, size)`. New provider-specific markers need the Python and JS provider maps kept in step.
- `classify_claude` has no production caller; held sessions are kiro-only, so Claude Code tiles have status "". `_claude_events` turns a hit line into plain assistant text.
- No `[hidden]` rule exists for `.acp-copy-btn`, whose `display: inline-flex` (style.css) beats the attribute. Any class that sets `display` on an element toggled with `hidden` needs its own `[hidden]` rule (AGENTS.md).
- Persisted user state lives under `%LOCALAPPDATA%\power-atlas`; `config.toml` is rewritten whole on every save, so new state goes in its own JSON file. `_CodexTurnWatcher` in `web.py` is the template for a periodic daemon thread. Notifications go through `notifications._fire_toast`.
- The rail serves workspaces in pages of 10 groups (`_ACP_GROUPS_PER_PAGE`), so a rail-only git view cannot be an overview; hence branch in the rail, status in the Overview.
- The tool surface for this repo is public: no session ids, home paths or user names in files, tests or commit messages.

### Risks & mitigations

- Injecting keystrokes into another program's console: gate on the presence-data PID, `claude.exe` image, a transcript that still ends at the hit, and a passed reset; send text and Enter as two writes; skip and notify otherwise; independent review before it ships.
- An unattended send into a freshly reset window (all schedules fire at once by owner choice): the "no activity" backstop and the single re-arm cap the damage.
- Reset-time drift reported by others (resets taking effect late): handled by re-arming from the new `resetsAt` once.
- Locked screen or sleeping PC at fire time: the backstop reports a launch that produced no activity; a sleeping PC cannot fire (schedule dropped on wake).
- Statusline snapshot staleness and concurrent writers: atomic write, newest timestamp wins, absent window means unknown.
- Git sweep cost: about 23 repos, median 0.24 s, maximum 1.8 s; run in a background pass about every 60 s, never per request.
- Git Bash in the Bash tool rewrites arguments that start with `/`; use the PowerShell tool for such commands (recorded in user memory).

### Resolved decisions

- Q1: Which providers? — A: ok to the recommendation — Decision: Claude Code full; Codex meters only; kiro-cli detect-and-show.
- Q2: Source of live Claude percentages? — A: ok (statusline snapshot) — Decision: opt-in snapshot file plus transcript hit lines; no credentials.
- Q3: Visible terminal or headless resume? — A: ok (visible terminal) — Decision: relaunch in a Windows Terminal tab with the prompt as an argument; headless excluded.
- Q4: Persistence of schedules? — A: persisted, fires only if PA is back before the deadline; otherwise reset and refresh status, user re-clicks — Decision: separate JSON file; drop on missed deadline.
- Q5: Overview structure? — A: ok (restructure) — Decision: quota strip, Needs attention, Live now, Plans and Usage side by side.
- Q6: Meaning of uncommitted and unpushed? — A: ok to the recommendation — Decision: tracked changes, dim untracked count, ahead, no-upstream flag only with local-only commits.
- Q7: Where does git state live? — A: branch in the rail, status in the Overview, no duplicates — Decision: as stated.
- Q8: When does a session enter and leave the interrupted list? — A: ok — Decision: last conversation line is a hit; leaves on continuation, dismissal or 48 h after reset; "ready to resume" state after reset.
- Q9: Which polish extras? — A: ok — Decision: meters with ticks and dual reset formats, pace marker, non-color cue, stale states, toasts, 7-day history; no status badge or forecast.
- Q10: Concurrency and failure policy? — A: all at once; re-arm once from the new time; no retry for launch errors — Decision: as stated.
- Q11: Live terminal? — A: ok for Option C — Decision: gated injection with relaunch for closed terminals and a 3-minute no-activity backstop.
- Q12: Codex resume? — A: "please implement codex support, yeah!" (to: detection plus relaunch, no typing into an open Codex terminal) — Decision: a real Codex stop was observed on 2026-10-09: a `task_complete` event with `error.codex_error_info` `usage_limit_exceeded`, the event before it carrying null `rate_limits`. A thread is interrupted while that is its last turn record. Reset time: the last real full window before the stop, else the message's "try again at" clock time, else unknown (no resume buttons). Resume is `codex resume <id> "<prompt>"` in a new terminal; a thread holding its writer lock is refused. A started turn counts as the reply when watching a resume.

### Open items (execution-contingent)

- Synthetic hit transcripts for tests (no unresumed hit exists on disk).
- A real statusline payload (needs the owner to add the snapshot line to the statusline script).
- Behavior at a real hit: does the process stay alive, does the presence sidecar exist, is the prompt box usable.
- `claude.exe` injection on a locked screen, and a single-burst Enter write (only the split write is verified).
- Whether Codex `rate_limits` are account-wide across threads.

### Recommended approach

Build in slices, each live-QA'd: (1) artifact fix; (2) git sweep, rail branch, Overview status; (3) quota backend (snapshot reader, Codex reader, hit detector, interrupted scanner, history); (4) resume backend (state file, launcher with prompt, injector, scheduler thread, routes); (5) Overview restructure and UI; (6) toasts, docs, tests. One independent review of slice 4 before it is considered done.

### QA environment

PowerAtlas listens on loopback port 4915 and needs the `pa_local` cookie (AGENTS.md "Verification Setup"). Drive pages with standalone Playwright from `.venv-PowerAtlas`. The owner granted PA restarts for this task's QA only (`POST /api/restart`, then wait for a fresh "Server ready" line in `orchestrator.log`). Real hits are rare, so tests use synthetic transcripts in temporary folders. Injection was probed against a throwaway `claude.exe` session and a Node raw-input receiver in Windows Terminal; both probes are deleted.

### Assumptions (unconfirmed)

Default prompt "resume" (editable); reset shown as countdown plus clock time, with date for weekly; toasts reuse the existing notifications switch with a 62 s dedupe; quota history keeps 14 days; Codex values treated as account-wide until checked; new routes need the cookie and Origin check; injection and relaunch are Windows-only; tests extend existing files; docs updates go into existing docs only.

---

## Harness Improvement Opportunities

- `/qexplore` Step 3 tells the file's reader that `/qplan` will fold the Discovery section away, which is false when the user chooses direct implementation — cost: the line had to be rewritten by hand — suggested change: word the marker so it also covers the skip-`/qplan` case.
- The Bash tool on Windows rewrites arguments that start with `/` — cost: one wrong injected string and one wrong "closed" claim, both caught — suggested change: keep the user-memory entry; consider a note in the Windows shell guidance.
