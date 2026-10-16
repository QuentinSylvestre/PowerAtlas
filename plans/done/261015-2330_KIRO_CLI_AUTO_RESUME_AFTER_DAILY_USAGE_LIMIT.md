# kiro-cli — Auto-Resume After a Daily Usage Limit

> **Date**: 2026-10-16
> **Status**: Complete  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: 2026-10-15 23:29
> **Scope**: Give kiro-cli v3 sessions that a daily usage limit stopped the same Resume now, Schedule, auto-schedule, watch and retry support that Claude Code and Codex sessions have
> **Memory relied on**: none

---

## Completion Summary

Implemented directly after exploration, without `/qplan` (the user's choice). Code and tests are in `05cca44`; this file's exploration record is `199bb31`.

**What shipped.** All ten success criteria below are met. kiro-cli v3 sessions stopped by the daily usage limit get Resume now, Schedule, auto-schedule, a watch and an hourly retry ladder (8 attempts, then given up on until the owner acts). The reset time is assumed (`Config.kiro_daily_reset_minutes_utc`, default 00:00 UTC, in Settings > General). Stop detection no longer loses a stop to kiro's own `steer` user records. Workflow children are never listed or resumed, and a session held on the /acp page is refused.

**Verification.**
- Full pytest suite (5502 passed, 2 skipped), `node tests/acp_page.test.mjs` (1120 passed) and `_check_test_names.py`. The two regression tests for the changes found in review and in exploration were run against the unfixed code and fail there.
- Live, on a restarted PowerAtlas: a throwaway session given a fake stop was auto-scheduled, rescheduled to 15 s out, fired by the scheduler, answered, and logged "answered 1 min after the stop (attempt 1)". The throwaway session and its processes were then removed.
- Live, success criterion 10: the real stopped session (`sess_21f8fb19`) was resumed through **Resume now** and answered 555 minutes after its stop (attempt 1); its row left the list.
- Real browser (Playwright): the Overview row, the Settings field (a clipped "12:00 AM" was found and fixed) and saving the setting round-trip.
- Measured facts are recorded in `docs/KNOWLEDGE.md` ("kiro-cli daily usage limit"), including the 22-of-22 single-error finding and the reset window (20:54Z, 02:39Z].

**Independent review (a separate agent, scored against the success criteria; these are the author's dispositions, not user approvals).**
- Fixed: with Auto-schedule on, "giving up" re-armed the next day's first attempt. Giving up now records a skip for that stop (test: `test_a_kiro_resume_gives_up_after_the_last_attempt_and_stays_given_up`, both Auto-schedule settings).
- Fixed: an explicit Schedule time on an old assumed-reset row was bounded from the reset; it is now bounded from now.
- Not changed, measured: a second limit error on one stop would drop a schedule keyed to the stop time. In the real store 0 of 22 stops have one. Recorded in `docs/KNOWLEDGE.md` under "Invalidated by".
- Not changed, documented: an already-armed schedule keeps its time when the reset setting changes (README states it); an odd `sess_` folder name could show buttons that fail (every real session folder is a lowercase `sess_<uuid>`; the 272 other folders are `.history` entries); unattended resumes run with all tools trusted, as the rail's Resume does (decided in Q2).

**Roadmap.** `plans/ROADMAP.md` is trimmed to unshipped items on the three entries this change touched (Timed prompts, Auto-resume, Quota-limit auto-schedule), at the user's direction. The long Auto-resume entry further down still carries an unrelated shipped note from 2026-10-14; it was left alone.

**Left open.**
- Whether `kiro-cli chat --resume-id` refuses a session another process holds (needs two live processes).
- Overview Usage totals still count child sessions' tokens (`docs/KNOWLEDGE.md`, "Not checked").
- Resuming a held session through the ACP supervisor (option C) was deferred; the general one-shot timed prompt remains a roadmap item.
- This file kept its transient `## Exploration Discovery` section, because it is the only record of the decisions made; `/qplan` never ran to fold it.

---

## Intent

### Problem statement & desired outcomes

On 2026-10-15 three kiro-cli v3 sessions ended on `UsageLimitReachedError` ("You've reached your daily usage limit. Please return tomorrow to continue building."). PowerAtlas lists such sessions in the Overview's "Stopped by a quota limit" block, but only with a Dismiss button: `resume.py` gates every action on a reset time and on a bare-UUID session id, and kiro supplies neither (its ids are `sess_<uuid>` and the error names no time).

Desired outcome: a stopped kiro session can be resumed from the Overview (Resume now, Schedule, Cancel schedule), is resumed automatically after the daily limit lifts when Auto-schedule resumes is on, is watched afterwards, and is retried hourly if the limit is still in force. The unknown reset time is an explicit, configurable assumption that the feature helps to measure.

### Success criteria

1. A kiro session whose last turn ended on a limit error is listed, even when kiro wrote its automatic `steer` user records after the stop (no false clear).
2. Kiro rows carry an assumed reset time (next occurrence of the configured daily reset time, default 00:00 UTC) flagged as assumed, and show Resume now, Schedule, Cancel schedule and Dismiss.
3. With Auto-schedule resumes on, a kiro stop that is before its assumed reset is scheduled at reset + 60 s and resumed in a terminal tab (`kiro-cli chat --agent-engine v3 --trust-tools * --resume-id <id> "<prompt>"`, the same arguments the rail's Resume uses).
4. If the resumed session hits the limit again, a retry is scheduled 1 h after the new limit error, up to 8 attempts in all, then the run ends `gave_up` with a toast. A reply ends the watch as done.
5. Resume now works for kiro at any time, including before the assumed reset.
6. A session PowerAtlas itself holds (the /acp page) is not resumed; the refusal says to continue it from /acp. Auto-schedule skips held sessions.
7. Workflow child sessions (`rootConversationId` set and not equal to the session's own `id`) are never listed as stopped sessions and never offered for resume. A child's stop is represented by its parent's own row.
8. After a successful kiro resume, `orchestrator.log` records the minutes between the stop and the answer and the attempt number, so the reset time can be narrowed from real data.
9. Claude Code and Codex behavior is unchanged (two attempts, reset-time driven).
10. Tests pass (`pytest tests/test_web.py --timeout=300`, `node tests/acp_page.test.mjs`), docs are updated, and a live end-to-end resume of a real stopped session (`sess_21f8fb19`, the PA store-backed plan session) works through the feature.

### Scope boundaries & non-goals

In scope: `quota.py`, `resume.py`, `config.py`, `web.py` (settings and held-session glue), `presence.py` (a held-session accessor), `templates/index.html` (rows and the setting), tests, README, `docs/KNOWLEDGE.md`.

Out of scope: resuming through the ACP supervisor (`session/load` plus `session/prompt`); typing into an open kiro terminal (there is no kiro equivalent of the Claude path and presence cannot identify a plain kiro terminal); changing the Overview Usage totals, which still count a child session's tokens (`docs/KNOWLEDGE.md:152` lists this as not checked); the kiro Pro/credit-based limit variants (only the daily message has been seen).

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

Step 1.5 dispatched the code-tracing trio (directed, subsystem, mutation-finder), all three on sonnet, because every in-scope file is Python or HTML/JS. Read-only probes were run before the interview (full scan of 753 `session.json` files unless stated).

### 4. Existing patterns & constraints

- **Kiro rows today**: `quota._scan_kiro` (`quota.py:371-404`) lists a session whose last turn ended on a `displayError` with `errorType == "UsageLimitReachedError"` (`_kiro_stop`, `quota.py:337-368`). Row: `id` = folder name `sess_<uuid>`, `provider "kiro-cli-v3"`, `window "daily"`, `resets_at None`, `state "limited"`, kept 48 h (`KIRO_KEEP_SECONDS`, `quota.py:43`). Dismiss already works for kiro rows (`resume.dismiss` uses `quota.interrupted`, `resume.py:422`).
- **Gates that block kiro** (`resume.py`): `_UUID_RE`/`_valid_session_id` bare-UUID only (`:63`, `:164`), also in the state loaders for `schedules` and `skipped` (`:107-123`) and `cancel_with_skip` (`:374`); `resets_at is None` refused in `resume_now` (`:237`), `schedule` (`:311`), `decorate` (`:453`), `_auto_schedule_new` (`:556`); `quota.find_hit` scans only Claude and Codex (`quota.py:294`); `resume_now`'s terminal gate hard-codes `"claude-code"` (`:250-253`); `_watch` has only Claude and Codex progress sources (`:488-492`); `MAX_ATTEMPTS = 2` (`:58`), loader accepts `attempt in (1, 2)` (`:110`), UI special-cases `attempt === 2` (`index.html:1634`); `schedule` clamps to `max(when, resets_at, now+5)` (`:324`); `resume_now(expect_resets_at=...)` compares the row's `resets_at` (`:234-236`).
- **Provider-generic parts**: `_relaunch` (`resume.py:200-208`) and `launcher.launch_session` / `_build_provider_args` (`launcher.py:118-124`, `:314-321`) already build `kiro-cli chat --agent-engine v3 --trust-tools * --resume-id <id> [prompt]` and accept `sess_` ids (`launcher._SESSION_ID_RE`, `:26`).
- **Child test**: `data_kiro_v3._is_subagent_session(data)` (`data_kiro_v3.py:82-99`): `rootConversationId` non-empty and different from `data["id"]`. Applied by the rail, workspace counts and Overview live tiles; not by `quota._scan_kiro`, `overview._usage_files`, or id lookups (by design).
- **Presence**: kiro has no sidecar; `pid_for("kiro-cli-v3", ...)` is always None; a kiro terminal is visible only when started with `--resume-id` (`presence.py:71`, `:703`, `:712-713`). The ACP supervisor's held ids are published into `presence._acp_live` (`presence.py:209-228`) but not exposed by `Snapshot`. `acp._lock_holder_v3` answers "not held" for a `failed` session (`acp.py:3406`, `:3427-3537`).
- **Config path**: `Config.auto_schedule` is a bool stored in `config.toml`, read each tick via `load_config()` (`config.py:164`, `resume.py:540`). Numeric settings go through `POST /api/save-setting` with `_SETTING_TYPES` and `_SETTING_BOUNDS` (`web.py:6425-6459`, `:6528-6676`); a setting read live per tick is not in `_RESTART_TO_APPLY`. `load_config` never raises and drops a wrong-typed value to the default (`config.py:876-881`).
- **Tests that pin "kiro is not resumable"**: `tests/test_web.py:46123-46132`, `:45658-45691`, `:45488` (context unread), `tests/acp_page.test.mjs:23376-23377`. Fixtures use non-UUID folder names (`sess_1`, `sess_k1`). The `isolated_config` autouse fixture redirects quota roots and no-ops `resume.start_scheduler`.
- **Governance** (`AGENTS.md` Doc & Test Guidelines): update existing docs and tests, no new doc files; run `node tests/acp_page.test.mjs` for template script changes (no `innerHTML`, no CSS engine); Python changes need a restart (granted for this task); `_check_public_ids.py` runs on every commit and blocks full session ids from the local session stores, so this file and the tests name sessions by their first 8 characters only (`sess_21f8fb19`), never by full id.

### 5. Risks & mitigations

- **False clear of a stopped session (measured, bug in current code).** After a limit stop kiro writes `user` records with `source: "steer"` and empty content (`steeringClearedIds`), and workflow notification records. `_kiro_stop` breaks on any `user` record (`quota.py:359-360`). Parent `e6cd599b` had an empty steer record right after the limit's `turn_end`; it was listed only because a second limit error landed later. Mitigation: "stopped" = last limit error with no `turn_start` after it; ignore `user` records.
- **`turn_start` is not a reply.** In the resumed parent the prompt, `turn_start` (+23 ms) and the limit error (+3 s) came in quick succession. A watch that counted `turn_start` as a reply would end before the retry. Mitigation: reply = `assistant` or `tool_call` record after the prompt; hit = limit error after the prompt.
- **Stop identity.** `_key_at` uses `resets_at` when set (`resume.py:168`); an assumed R shared by all stops of a day would make dismiss/skip of one stop hide another. Mitigation: kiro key = `hit_at`. Armed schedules must also survive an R edit; the fire-time check for kiro compares the stop time (stored on the schedule), not R.
- **Hourly retry vs. `schedule()` clamp.** Writing the retry record directly (as `_watch` does at `resume.py:508-512`) avoids the clamp to `resets_at`. The loader's `attempt in (1, 2)` would silently drop attempts 3-8 on restart; it must accept `1..MAX` per provider.
- **Two processes on one session.** Whether `kiro-cli chat --resume-id` runs when another process holds the session is unverified. A held-by-PowerAtlas session is refused; a plain terminal cannot be detected. A resume that fails is caught by the watch (`no_activity` after 180 s / 600 s) with a toast.
- **Unattended trust.** The relaunch uses `--trust-tools *`, as the rail's Resume does. Decided (Q2): keep, document in the README.
- **Wrong reset guess.** R is only known to lie in (20:54Z, 02:39Z]. A wrong default costs a few failed hourly retries (one terminal tab each). Mitigation: configurable R, 8-attempt ladder, measurement log.
- **Children in the usage totals** (not addressed here): `overview._usage_files` globs every `sess_*` folder (`overview.py:1902`, `:1455-1456`).

### 6. Resolved decisions

- Q0 (pre-exploration): reset time — A: probe at 2026-10-16 02:39Z succeeded after stops at 2026-10-15 19:13-19:32Z (also 2026-10-07 20:54Z to 2026-10-08 14:14Z) — Decision: daily reset R lies in (20:54Z, 02:39Z] UTC; local midnight and a rolling 24 h window are ruled out; default R = 00:00 UTC, configurable; first auto-schedule R + 60 s; retry hourly, 8 attempts.
- Q0b (pre-exploration): workflow children — A: they must never be top-level sessions, appear only in a parent's transcript view or while a workflow runs — Decision: exclude children from the stopped-session list and from resume; the parent carries the stop (children's limit errors are mirrored in the parent within 3 s, 6 of 6 measured). Rail/counts/live tiles already exclude them.
- Q1: Resume scope for sessions that are open elsewhere — A: option B — Decision: refuse sessions PowerAtlas holds (message points to /acp), skip them in auto-schedule, launch everything else in a terminal. Option C (resume through the ACP supervisor) is deferred.
- Q2: Permissions of an unattended relaunch — A: ok to the recommended option A — Decision: same launch arguments as the rail's Resume (`--trust-tools *`); documented in the README.
- Q3: Assumptions checkpoint — A: "Alright for me, let's go" — Decision: assumptions 1-8 below stand.
- Decisions recorded at the checkpoint: kiro "stopped" rule = last limit error with no `turn_start` after it; kiro row gets an assumed `resets_at` plus a flag, key stays `hit_at`; reply = `assistant`/`tool_call` after the prompt; kiro retry ladder separate from Claude/Codex (2 attempts unchanged); Resume now allowed before R; setting stored as minutes after 00:00 UTC (default 0, 0..1439, read per tick, no restart) with a local-time hint in the settings panel; success log line with stop-to-answer minutes and attempt number.
- Terminology (record for a glossary): **assumed reset** — the daily reset time PowerAtlas assumes for kiro-cli, because kiro does not report one; not "reset time" alone, which for Claude Code and Codex is reported.

### 7. Open items

- Whether a terminal `kiro-cli chat --resume-id` refuses a session that another process holds (execution-contingent; needs two live processes; first real occurrence will tell).
- `overview._usage_files` counting child sessions (not changed here; reopen if the user wants Usage totals to match the rail).
- `tests/test_web.py:45488` asserts `can_resume is False` on a row whose context was not read; check when updating tests.
- Static `.dash-ov-int*` CSS and `notifications.py` were not read; expected to need no change.

### Assumptions (unconfirmed)

None unconfirmed at the checkpoint: the user confirmed the consequence-significant assumptions (1-8) and the minor batch.

### 8. Recommended approach

1. `quota.py`: `_kiro_stop` returns the stop time when the last limit error has no later `turn_start`; `_scan_kiro` skips child sessions, sets the assumed `resets_at` (new helper `assumed_reset(hit_at, minutes_utc)`), `reset_assumed: True`, `state "ready"` once past it, and an explicit stop-time key; `find_hit` scans kiro too; new `kiro_progress(session_id, since)` returning `{kind: hit|reply|pending, at, hit}` for the watch; module docstring updated.
2. `resume.py`: `_valid_session_id` and loaders accept `sess_<uuid>` as well as a bare UUID; `MAX_ATTEMPTS` per provider (2, kiro 8) and the loader accepts attempts up to the provider's maximum; `_key_at` uses `hit_at` for kiro; `resume_now`: kiro branch (no resets-before check, held-session refusal via a presence accessor, no typed mode), run and schedule records carry the stop time; `schedule`: allow kiro with the assumed `resets_at`; `decorate`: `can_resume` true for kiro; `_watch`: kiro progress source, retry at new-hit time + 1 h written directly, success log line; `_auto_schedule_new`: skip held kiro sessions, keep skipping `ready` rows.
3. `config.py` / `web.py`: new int `kiro_daily_reset_minutes_utc` (default 0), added to `_SETTING_TYPES` and `_SETTING_BOUNDS`, surfaced in `api_settings`; `presence.py`: small accessor for the ACP-held set.
4. `index.html`: kiro branch of the explanatory note, "attempt N of M" label, assumed-reset wording on the row ("resets about <time>"), setting field with the local-time hint.
5. Tests: rewrite the two "kiro is not resumable" tests; add tests for the false-clear fix, child exclusion, id validation, assumed reset, retry ladder, held refusal; extend `acp_page.test.mjs` for kiro rows.
6. Docs: README "Stopped by a quota limit" and Auto-schedule paragraphs, `docs/KNOWLEDGE.md` (kiro limit facts, measured reset window, child stop mirroring), module docstrings.

### 9. QA environment

- Unit/integration: `.venv-PowerAtlas/Scripts/python -m pytest tests/test_web.py --timeout=300`; page script: `node tests/acp_page.test.mjs`.
- Live: restart granted for this task; sign in with the `pa_local` cookie recipe in `AGENTS.md`; the Overview shows the real stopped sessions in the store. End-to-end: resume `sess_21f8fb19` with the feature (its stop is past the assumed reset, so it reads "ready" and Resume now applies). A limit cannot be induced on demand, so the retry ladder is covered by tests only.
- Evidence: `%LOCALAPPDATA%\power-atlas\orchestrator.log` for the success log line.
