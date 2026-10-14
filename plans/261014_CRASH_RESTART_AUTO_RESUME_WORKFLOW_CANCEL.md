# Crash Restart, Auto-Resume Cycling, and Workflow-Cancel Wiring

> **Date**: 2026-10-14
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Three PowerAtlas reliability improvements: Task Scheduler crash restart (Windows), hands-free quota-limit cycling, and PA-side workflow-cancel wiring

---

## Intent

### Problem statement & desired outcomes

PowerAtlas crashes and stays dead until the next login. When a quota limit resets, PA can resume a session once, but if the resumed session hits a second limit the user must intervene again. When Stop is pressed during a session with active workflow children, PA updates its own view but the kiro-cli step process keeps running.

### Success criteria

1. **(Supervisor)** A PowerAtlas crash is detected and the process is restarted without a login cycle on Windows via Task Scheduler RestartOnFailure. The restart is logged; repeated crashes within a short window surface a tray notification rather than looping silently.
2. **(Auto-resume)** A session stopped twice in a row by a quota limit resumes both times without user interaction when the auto-schedule toggle is on (default: on). A warning appears when PA cannot access the session.
3. **(Workflow-cancel)** Stop on a session with live workflow children sends `workflow-cancel` to kiro-cli. The call degrades gracefully on kiro-cli 2.31.0 (method not yet classified) and activates automatically when kiro-cli ships the fix.

### Scope boundaries & non-goals

- Supervisor: Windows only. Linux crash restart is out of scope.
- Auto-resume: Claude Code and Codex only. kiro-cli sessions have no `resets_at` and remain excluded.
- Workflow-cancel: PA-side wiring only. The kiro-cli fix (`KnownExtMethod` in `persistence-classification.ts`) is external and out of scope.

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

- `autostart.py:enable()` already branches Windows/Linux and creates a `.lnk`; Task Scheduler task registration follows the same Windows branch.
- `resume.py:_watch()` verdict `"end"` leaves the run in `_runs` permanently (`resume.py:631`). The fix adds an `auto_schedule`-gated removal on this verdict path.
- `_auto_schedule_new()` checks `in_runs = {r["session_id"] for r in _runs}` and skips any session in `_runs` regardless of state (`resume.py:564`). Removing a "gave_up" run unblocks this skip.
- `_handle_cancel` in `acp.py:10074` already calls `_workflow_stop_children()` (`acp.py:5738`). The workflow-cancel `_request` goes immediately after that call.
- `_kiro.dev/commands/execute` returns `-32603 PersistenceClassification` on kiro-cli 2.31.0 for all commands including `workflow-cancel` (confirmed by probe 2026-10-14). PA's `_request()` raises `AcpError` on error responses.
- `acp.py` isolation boundary: only three package imports allowed (`config`, `launcher`, `permission_rows`). `AcpError` is defined inside `acp.py` itself.
- Step 1.5: code-tracing trio dispatched; reports at `.agents/tasks/qexplore-supervisor-resume-workflow/`.

### 5. Risks & mitigations

- `_kiro.dev/commands/execute` -32603 on 2.31.0: caught as AcpError with a logged warning; zero behavior change until kiro-cli fixes the defect.
- `schtasks /create` may fail if the user lacks permission or the task already exists: wrap in try/except and log; the `.lnk` continues to work.
- Changing `auto_schedule` default to True affects users who have never explicitly toggled it (field absent from `config.toml`). Mitigated by the per-stop "Cancel schedule" button and the opt-out toggle.
- "gave_up" removal means `MAX_ATTEMPTS=2` no longer caps multi-cycle within a process lifetime. A broken session could cycle indefinitely. Mitigated by the existing skip list and `LATE_SECONDS=600` gate.

### 6. Resolved decisions

- Q1: Combined or split project files? A: single file, parallel phases where possible. Decision: one project file for all three items.
- Q2: Supervisor mechanism? A: Task Scheduler (same as Obsidian iCloud sync daemon, referenced in ROADMAP.md). Decision: `schtasks /create` in `autostart.py:enable()`, Windows-only, targeting `--foreground` directly.
- Q3: Linux crash restart? A: Windows-only. Decision: no Linux implementation in this project.
- Q4 (auto-resume "gave_up" fix): A: ok. Decision: when verdict is "end" and `auto_schedule` is on, remove the run from `_runs` immediately (same as "done"), enabling reschedule on next tick.
- Q5 (auto-resume default): A: ok. Decision: change `auto_schedule: bool = False` to `True` in `config.py:164`.
- Q6 (workflow-cancel wiring): A: wire now, degrade gracefully. Decision: confirmed by probe; add `_request` call in `_handle_cancel` with AcpError catch.

### 7. Open items

- kiro-cli must add `_kiro.dev/commands/execute` to `KnownExtMethod` in `persistence-classification.ts` for workflow-cancel to actually stop the step process. Re-probe on each minor kiro-cli release per KNOWLEDGE.md.
- Task Scheduler task name collision edge case: if `enable()` is called when the task already exists, `schtasks /create` fails unless `/f` (force) is used. Decide in implementation whether to force-overwrite or pre-check.

### 8. Recommended approach

Three independent phases, all parallelizable (disjoint file sets):

**Phase 1 — Supervisor / crash restart (Windows, `autostart.py`, `__main__.py`)**

In `autostart.py:enable()`, after creating the `.lnk`, register a Task Scheduler task via `subprocess.run(["schtasks", "/create", ...])` with action `pythonw.exe -m power_atlas --foreground`, restart delay 1 minute, 3 attempts, `/f` to overwrite. In `autostart.py:disable()`, delete the task via `schtasks /delete /f`. On startup in `_run_foreground()`, if a stale PID file exists (crash detected), log "Restarted after crash" to `orchestrator.log` and emit a tray toast.

**Phase 2 — Auto-resume cycling (`resume.py`, `config.py`)**

In `resume.py:_watch()`, on verdict `"end"` check `load_config().auto_schedule`. If True, remove the run from `_runs` (same removal as "done"/"retry") and return instead of leaving it as "gave_up". Change `auto_schedule: bool = False` to `True` in `config.py:164`.

**Phase 3 — Workflow-cancel wiring (`acp.py`)**

In `acp.py:_handle_cancel()`, after the `_workflow_stop_children()` call (`acp.py:10139`), add a try/except block that calls `await self._supervisor._request("_kiro.dev/commands/execute", {"sessionId": session_id, "command": {"command": "workflow-cancel", "args": {}}})` and catches `AcpError` with a `log.warning`. Update the comment at `acp.py:5751`. Update `ROADMAP.md` to reflect the wiring status.

### 9. QA environment

- **Supervisor**: Windows. Verify via `schtasks /query /tn "PowerAtlas Crash Restart"` after `enable()`. Force-kill PowerAtlas (`taskkill /f /pid <pid>`) and observe automatic restart within ~1 min. Check `orchestrator.log` for restart log line and observe tray notification.
- **Auto-resume**: pytest suite in `tests/test_web.py` and `tests/test_data.py` with `isolated_config` fixtures. Test the "gave_up" removal path by injecting a "gave_up" run with `auto_schedule=True` and asserting the run is removed and rescheduled on the next tick. Integration: trigger a real or simulated quota limit cycle via the Overview.
- **Workflow-cancel**: unit-test `_handle_cancel` with a mock `_supervisor._request` that raises `AcpError`; assert (a) `_workflow_stop_children` still fires, (b) broadcast still happens, (c) no exception propagates. Integration: live PA session with a running workflow, press Stop, check `orchestrator.log` for the warning.

## Harness Improvement Opportunities

- Batched multiple interview questions into one turn despite the Kiro /qexplore overlay requiring one question per turn — cost: user correction round; needed a full re-read of the skill — suggested change: load and check the Kiro overlay section before asking the first interview question.
- Multi-domain decomposition check should fire as Q1 before any other questions — cost: questions were asked before decomposition was resolved — suggested change: add a checklist item in the Kiro overlay that fires the decomposition check when 3+ independent domains are detected.
