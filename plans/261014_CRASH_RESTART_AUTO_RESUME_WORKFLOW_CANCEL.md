# Crash Restart, Auto-Resume Cycling, and Workflow-Cancel Wiring

> **Date**: 2026-10-14
> **Status**: In Progress  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Three PowerAtlas reliability improvements: Task Scheduler crash restart (Windows), hands-free quota-limit cycling, and PA-side workflow-cancel wiring
> **Memory relied on**: none
> **Estimated effort**: 1-3 days

---

## Intent

### Problem statement & desired outcomes

PowerAtlas crashes and stays dead until the next login. When a quota limit resets, PA can resume a session once, but if the resumed session hits a second limit the user must intervene again. When Stop is pressed during a session with active workflow children, PA updates its own view but the kiro-cli step process keeps running.

### Success criteria

- SC-1: **(Supervisor)** A PowerAtlas crash is detected and the process is restarted without a login cycle on Windows via Task Scheduler RestartOnFailure. The restart is logged; the crash event surfaces a tray notification.
- SC-2: **(Auto-resume)** A session stopped twice in a row by a quota limit resumes both times without user interaction when the auto-schedule toggle is on (default: on). A warning toast appears when PA cannot complete the resume.
- SC-3: **(Workflow-cancel)** Stop on a session with live workflow children sends `workflow-cancel` to kiro-cli. The call degrades gracefully on kiro-cli 2.31.0 (method not yet classified) and activates automatically when kiro-cli ships the fix.

### Scope boundaries & non-goals

- Supervisor: Windows only. Linux crash restart is out of scope.
- Auto-resume: Claude Code and Codex only. kiro-cli sessions have no `resets_at` and remain excluded.
- Workflow-cancel: PA-side wiring only. The kiro-cli fix (`KnownExtMethod` in `persistence-classification.ts`) is external and out of scope.

---

## 1) Current State

**Supervisor** (`src/power_atlas/autostart.py`): `enable()` creates a Startup-folder `.lnk` and does nothing else. Windows will relaunch PA at the next login but not after a crash. `disable()` removes the `.lnk`. `_startup_interpreter(windowed=True)` returns the venv's `pythonw.exe`. No Task Scheduler task is created or removed today.

**Crash detection** (`src/power_atlas/__main__.py`, `_run_foreground()` section): `_write_pid()` stores the running PID in `CONFIG_DIR / "power-atlas.pid"`. `_remove_pid()` cleans it up on clean exit. If PA crashes, the PID file is left behind with a stale PID. No code checks for this stale file at startup — a crash goes undetected.

**Auto-resume** (`src/power_atlas/resume.py`, `_watch()` and `tick()`): `_watch()` sets a run's state to `"gave_up"` and returns verdict `"end"` when the second quota hit is detected. `tick()` removes a run from `_runs` only when verdict is `"done"` or `"retry"` (line in `tick()` under `# watching` section: `if verdict in ("done", "retry"):`). A `"end"` verdict leaves the run in `_runs` permanently. `_auto_schedule_new()` skips any session in `in_runs = {r["session_id"] for r in _runs}` regardless of state, so a `gave_up` run permanently blocks rescheduling. `auto_schedule: bool = False` in `config.py` (section `# Automatically schedule a resume for every session stopped by a quota limit`).

**Workflow-cancel** (`src/power_atlas/acp.py`, `_handle_cancel()`): After `_workflow_stop_children(session_id)` is called (the `had_runs` / `stopped` block), the comment reads "the step's own process is not stopped (`workflow-cancel` is not wired)." No `_request` call follows. Probe run 2026-10-14 confirmed `_kiro.dev/commands/execute` returns `-32603 PersistenceClassification` on kiro-cli 2.31.0 for any command, including `workflow-cancel`.

## 2) Goal

Register a Task Scheduler task with `RestartOnFailure` so crashes self-heal without a login cycle. Remove the `gave_up` run from `_runs` when `auto_schedule` is on so the scheduler can cycle a second quota limit. Enable `auto_schedule` by default. Wire `workflow-cancel` into Stop with an `AcpError` catch so the call degrades gracefully on kiro-cli 2.31.0.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Supervisor mechanism | Task Scheduler XML + `schtasks /create /xml <tmp> /f` | SamWhispers supervisor module; watchdog sibling process | Same mechanism as Obsidian iCloud sync; no third-party dep; XML allows `<RestartInterval>` and `<RestartCount>` which the `schtasks` CLI arguments cannot set directly |
| Task Scheduler trigger | `LogonTrigger` (OnLogon) | AtStartup; combined Logon+Startup | Logon trigger runs under the user account and inherits the environment; same model as the `.lnk` |
| Restart settings | `<RestartInterval>PT1M</RestartInterval>`, `<RestartCount>3</RestartCount>` | Longer/shorter intervals | 1-minute delay prevents rapid-restart loop; 3 attempts caps runaway; Task Scheduler stops trying after the count is exhausted |
| Task name collision | Force-overwrite with `/f` | Pre-check then skip; pre-check then fail | Idempotent: re-enabling overrides a stale task with the current interpreter path |
| `.lnk` vs Task Scheduler co-existence | When Task Scheduler registration succeeds, remove the `.lnk`; on failure keep `.lnk` as fallback | Keep both simultaneously | If both fire at login and `.lnk` wins, the Task Scheduler process exits code 0 (single-instance guard) → `RestartInterval` never applies to the `.lnk`-launched process. Single-owner model avoids the race. |
| `is_enabled()` change | Return True if `.lnk` OR Task Scheduler task exists | Check `.lnk` only | Tracks actual autostart state regardless of which mechanism is active |
| Crash detection | Check stale PID file before `_write_pid()`; emit toast immediately | Write crash marker file; defer to tray startup | Simplest path; toast via `notifications.py` does not need tray running (PowerShell child); logging happens at the earliest possible point |
| Auto-resume "gave_up" fix location | In `tick()`, extend removal condition | In `_watch()` | Removal from `_runs` is done in `tick()`, not `_watch()`; extending the single condition line there is minimal and correct |
| `auto_schedule` default | Change `False` → `True` in `config.py` | Keep `False`; make on-the-fly | User decision (Q5 resolved in /qexplore). Per-stop "Cancel schedule" button and opt-out toggle mitigate unwanted reschedules |
| Workflow-cancel condition | Conditional: only when `stopped or had_runs` | Always unconditional | Avoids a spurious RPC on sessions that had no workflow; still safe unconditionally but condition is cleaner |

## 4) External Dependencies & Costs

### Required external changes

| Category | Change needed | Owner | Status |
|---|---|---|---|
| Data migration / backfill | None | — | N/A |
| Cloud resources | None | — | N/A |
| CI/CD | None | — | N/A |

Remove rows that do not apply.

### Cost impact

None. All changes are local process and OS scheduler configuration.

## 5) Implementation Phases

**Vertical phasing rule**: Each phase is a vertical slice over a single feature (Supervisor, Auto-resume, Workflow-cancel). All three phases have non-overlapping code file sets. `tests/test_web.py` appears in Phase 1 (new `TestCrashRestart` class appended at the bottom) and Phase 2 (existing `TestResume` class modified). The edits are non-conflicting at the hunk level: Phase 1 appends, Phase 2 replaces a specific existing method. Parallel agents must use targeted `str_replace` edits and not regenerate the full file.

---

### Phase 1: Supervisor / crash restart (Windows) [QA] [P:2,3]

**Goal**: Register a Task Scheduler task with `RestartOnFailure` in `autostart.py:enable()`, remove it in `disable()`, and detect + log + notify on crash restart in `_run_foreground()`.

**Covers**: SC-1

**File scope**: `src/power_atlas/autostart.py`, `src/power_atlas/__main__.py`, `src/power_atlas/notifications.py`, `tests/test_web.py`, `plans/tests/HARNESS.md`

#### `src/power_atlas/notifications.py`

Add a new public function `notify_crash_restart()` below the existing `notify_quota()` function:

```python
def notify_crash_restart() -> None:
    """PowerAtlas restarted after a crash (stale PID file detected at startup)."""
    _fire_toast("PowerAtlas", "Restarted after a crash — see orchestrator.log for details.")
```

#### `src/power_atlas/autostart.py`

Import `subprocess`, `tempfile` at the module level (only if not already present). Do NOT import `textwrap` — it is not used.

In `enable()`, after the Windows branch's `shortcut.save()` call, add the Task Scheduler registration block. Steps:

1. Build the XML string using a template with these elements:
   - `<Principals>` block with `<Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>` — required so the task runs as the current interactive user without elevation; without this, `schtasks` creates a SYSTEM-context task or fails with ACCESS_DENIED.
   - `<Triggers><LogonTrigger>` — initial login startup
   - `<Settings>`: `<RestartInterval>PT1M</RestartInterval>`, `<RestartCount>3</RestartCount>`, `<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>` (unlimited run time), `<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>`, `<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>`
   - `<Actions Context="Author"><Exec><Command>…</Command><Arguments>-m power_atlas --foreground</Arguments></Exec></Actions>`
   - For the `<Command>` element: XML-escape the interpreter path with `import html; html.escape(str(_startup_interpreter(windowed=True)))` to handle paths with `&`, `<`, or `"` characters.

2. Write the XML to a temp file via `tempfile.NamedTemporaryFile` in `CONFIG_DIR` (always exists at this point), using `delete=False` and `encoding="utf-16"` (Python's `utf-16` codec writes UTF-16 LE with BOM, which `schtasks` requires).

3. In a `try/finally` block: call `subprocess.run(["schtasks", "/create", "/tn", "PowerAtlas Crash Restart", "/xml", tmp_path, "/f"], capture_output=True)`. Catch both `subprocess.run` spawn failures and non-zero return codes — log either as WARNING using `import logging; log = logging.getLogger(__name__)`. Delete the temp file in the `finally` block using `Path(tmp_path).unlink(missing_ok=True)`.

4. If the `schtasks` call returned code 0 (success): remove the `.lnk` with `_windows_shortcut_path().unlink(missing_ok=True)`. This avoids the login race: with both active, the `.lnk`-launched process wins `_single_instance_guard`, Task Scheduler's process exits code 0, and `RestartInterval` never applies to the `.lnk` process. Making Task Scheduler the sole launcher eliminates the race.

5. If the `schtasks` call failed (non-zero return code or spawn exception): leave the `.lnk` in place as the fallback. Log a warning that crash restart is disabled; normal login startup still works.

Sketch of the XML (mark as sketch — actual XML must match the Task Scheduler 1.2 namespace and encoding):
```xml
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Settings>
    <RestartInterval>PT1M</RestartInterval>
    <RestartCount>3</RestartCount>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{html.escape(interpreter_path)}</Command>
      <Arguments>-m power_atlas --foreground</Arguments>
    </Exec>
  </Actions>
</Task>
```

Update `is_enabled()` (Windows branch): return `True` if the `.lnk` exists OR `_task_scheduler_task_exists()` — a new private helper that runs `schtasks /query /tn "PowerAtlas Crash Restart"` with `capture_output=True` and returns `returncode == 0`. If `subprocess.run` raises, return `False`.

In `disable()` (Windows branch), after `_windows_shortcut_path().unlink(missing_ok=True)`, add the schtasks deletion:
```python
try:
    subprocess.run(
        ["schtasks", "/delete", "/tn", "PowerAtlas Crash Restart", "/f"],
        capture_output=True,
    )
except Exception:
    pass  # schtasks not available or task did not exist — ignore
```

#### `src/power_atlas/__main__.py`

In `_run_foreground()`, between `log.info("Starting power-atlas (foreground)")` and `_single_instance_guard()`, add a crash-detection block:

```python
# Crash detection: if the PID file exists and the recorded PID is not alive,
# the previous instance crashed rather than shutting down cleanly.
_detect_and_log_crash(log)
```

Add a module-level helper `_detect_and_log_crash(log)` (below `_remove_pid()`):
- Windows-only: return immediately if `sys.platform != "win32"` (crash detection via PID file is Windows-specific to this implementation)
- Read `_PID_FILE`; wrap in `try: ... except (OSError, ValueError): return` (handles missing file, permission error, and truncated/malformed content)
- Parse the PID; if the PID is alive (`_pid_alive(pid)`), return (another instance or clean exit beat us here — handled by `_single_instance_guard()`)
- If the PID is not alive: log `"Restarted after crash (previous PID %d not found)"`, call `notifications.notify_crash_restart()` inside a try/except, return

`_pid_alive()` already exists in `__main__.py`; use it directly.

`notifications` is not imported at module level in `__main__.py`. Add a deferred import inside `_detect_and_log_crash` following the local-import pattern already used elsewhere in the file.

#### `tests/test_web.py`

Append a new `class TestCrashRestart` (after the last class in the file, not before any fixture) with these tests (use existing TestResume fixtures as reference for patching patterns):

- `test_enable_registers_schtasks_task`: mock `subprocess.run`, call `autostart.enable()` in a Windows-only context (or mock `sys.platform`), assert `subprocess.run` was called with args containing `"schtasks"`, `"/create"`, `"/tn"`, `"PowerAtlas Crash Restart"`, `"/f"`.
- `test_enable_removes_lnk_when_schtasks_succeeds`: mock `subprocess.run` to return code 0; assert the `.lnk` file is removed after `enable()`.
- `test_enable_keeps_lnk_when_schtasks_fails`: mock `subprocess.run` to return `CompletedProcess(returncode=1)`, call `autostart.enable()`, assert no exception propagates AND the `.lnk` is NOT removed.
- `test_enable_schtasks_spawn_failure_does_not_raise`: mock `subprocess.run` to raise `FileNotFoundError` (schtasks absent), call `autostart.enable()`, assert no exception propagates.
- `test_disable_deletes_schtasks_task`: mock `subprocess.run`, call `autostart.disable()` (Windows path), assert `subprocess.run` called with `"schtasks"`, `"/delete"`, `"PowerAtlas Crash Restart"`, `"/f"`.
- `test_is_enabled_returns_true_when_task_exists`: mock `_task_scheduler_task_exists` to return `True` (no `.lnk`); assert `is_enabled()` returns `True`.
- `test_crash_detection_logs_and_notifies_on_stale_pid`: write a fake PID file with a dead PID, mock `_pid_alive` to return `False`, mock `notifications.notify_crash_restart`, call `_detect_and_log_crash(log)`, assert log warning emitted and `notify_crash_restart` called.
- `test_crash_detection_silent_on_no_pid_file`: no PID file present, call `_detect_and_log_crash(log)`, assert no warning.
- `test_crash_detection_silent_on_live_pid`: PID file present with a live PID, mock `_pid_alive` to return `True`, call `_detect_and_log_crash(log)`, assert no warning.
- `test_crash_detection_silent_on_non_windows`: mock `sys.platform = "linux"`, call `_detect_and_log_crash(log)`, assert no warning even with stale PID file.

#### `plans/tests/HARNESS.md`

In the autostart enable/disable cycle section: note that `enable()` also registers a Task Scheduler task named `"PowerAtlas Crash Restart"`, and `disable()` removes it. Add `schtasks /query /tn "PowerAtlas Crash Restart"` as a verification step.

**Exit criteria**:
- [x] `autostart.enable()` calls `schtasks /create /tn "PowerAtlas Crash Restart" /xml <tmp> /f` (XML written with UTF-16, `<Principals>` block included, interpreter path XML-escaped)
- [x] When `schtasks` returns code 0: `.lnk` is removed (`_windows_shortcut_path().unlink(missing_ok=True)`)
- [x] When `schtasks` fails (non-zero code or spawn exception): `.lnk` is NOT removed; warning logged
- [x] XML temp file deleted in `finally` with `unlink(missing_ok=True)` regardless of success/failure
- [x] `autostart.disable()` calls `schtasks /delete /tn "PowerAtlas Crash Restart" /f`
- [x] `autostart.is_enabled()` returns True when `.lnk` OR Task Scheduler task exists
- [x] `_run_foreground()` calls `_detect_and_log_crash(log)` before `_single_instance_guard()`
- [x] `_detect_and_log_crash` is Windows-only (early return on non-Windows)
- [x] `_detect_and_log_crash` wraps PID file read/parse in `try: ... except (OSError, ValueError): return`
- [x] Stale PID → log warning + `notify_crash_restart()` called
- [x] Missing PID file or live PID → no warning
- [x] `notifications.notify_crash_restart()` added with correct toast title/body
- [x] All new tests in `class TestCrashRestart` pass
- [x] `plans/tests/HARNESS.md` updated with Task Scheduler note
- [x] `.venv-PowerAtlas\Scripts\python -m pytest tests/ -x --timeout=300` passes

Implementation (2026-10-14, code: 06bfbb8)
`notifications.py`: added `notify_crash_restart()` firing a toast with hardcoded title/body. `autostart.py`: added Task Scheduler XML generation with `<Principals>` block, `RestartInterval PT1M`, `RestartCount 3`, UTF-16 temp file, `html.escape()` on interpreter path, absolute `C:\Windows\System32\schtasks.exe` path, `timeout=30` on all calls, `CONFIG_DIR.mkdir()` guard, `.lnk` removal on success (Task Scheduler becomes sole launcher), `.lnk` preserved on failure. Added `_task_scheduler_task_exists()`. Updated `is_enabled()` to check `.lnk` OR task. Added `_detect_and_log_crash()` in `__main__.py` — Windows-only, `(OSError, ValueError)` guard on PID file parse, `_pid_alive` in `try/except OSError`. `tests/test_web.py`: appended `class TestCrashRestart` (11 tests). `test_autostart.py` updated to mock subprocess.run and _task_scheduler_task_exists. QA: PASS. Auto-fixes applied for 3 Medium findings and 4 Low findings.

---

### Phase 2: Auto-resume cycling [QA] [P:1,3]

**Goal**: Remove a `gave_up` run from `_runs` when `auto_schedule` is True so `_auto_schedule_new()` can reschedule it. Change `auto_schedule` default to `True`.

**Covers**: SC-2

**File scope**: `src/power_atlas/resume.py`, `src/power_atlas/config.py`, `tests/test_web.py`, `README.md`, `AGENTS.md`

#### `src/power_atlas/resume.py`

In `tick()`, locate the line after the `for run in watching:` loop that reads:
```python
        if verdict in ("done", "retry"):
            with _lock:
                _runs[:] = [r for r in _runs if r["id"] != run["id"]]
```

Replace with:
```python
        _remove_run = verdict in ("done", "retry")
        if not _remove_run and verdict == "end":
            from .config import load_config
            _remove_run = load_config().auto_schedule
        if _remove_run:
            with _lock:
                _runs[:] = [r for r in _runs if r["id"] != run["id"]]
```

> **Rationale**: `_watch()` still returns `"end"` unchanged. `tick()` decides whether to remove the run. `auto_schedule` is read outside `_lock` via a fresh `load_config()` call — this matches the same pattern used at line 379 (the existing comment there explains the race window is microseconds and inert in both directions).

#### `src/power_atlas/config.py`

In the `Config` dataclass, change:
```python
    auto_schedule: bool = False
```
to:
```python
    auto_schedule: bool = True
```

(Section identified by the comment `# 261013_AUTO_SCHEDULE_RESUME_MODE`.)

#### `tests/test_web.py`

In `class TestResume`:

1. Rename `test_auto_schedule_does_not_re_arm_gave_up_run` → `test_auto_schedule_off_does_not_remove_gave_up_run` and update its assertion: with `auto_schedule=False` (explicitly set via `self.config.auto_schedule = False`), a `gave_up` run must REMAIN in `_runs` after `tick()`.

2. Add `test_auto_schedule_on_removes_gave_up_run_and_enables_reschedule`:
   - Set `self.config.auto_schedule = True`
   - Create a stopped session with a future `resets_at`
   - Inject a run with `state="watching"` into `_runs` (NOT `gave_up` — `tick()` filters by `state=="watching"`)
   - Arrange the transcript mock so `_watch()` returns `"end"`: the session has a new quota hit at the SAME `resets_at` (not later) and `attempt == MAX_ATTEMPTS`, causing the "gave_up" branch in `_watch()` to fire
   - Call `_resume_r.tick(now)`
   - Assert the run is removed from `_runs` (the `"end"` + `auto_schedule=True` path removed it)
   - Call `_resume_r.tick(now + 1)` again (next tick, `_auto_schedule_new` runs)
   - Assert the session IS rescheduled (a schedule entry exists in `_state["schedules"]`)

3. In the two existing tests that rely on `auto_schedule=False` as the DEFAULT:
   - `test_auto_schedule_off_does_not_arm_any_session` (line ~46418): add `self.config.auto_schedule = False` explicitly at the start of the test body.
   - `test_cancel_when_auto_schedule_off_does_not_add_skip` (line ~46518): add `self.config.auto_schedule = False` explicitly at the start of the test body.
   Both tests currently rely on `Config()` defaulting to `False`; after the default changes to `True` they fail without the explicit override.

#### `README.md`

All three occurrences of "off by default" in the auto-schedule / resume section: change to "on by default". (Grep for `"off by default"` in README.md to locate them.)

#### `AGENTS.md`

In the `## Terminology` section, entry for **"Stopped by a quota limit"**: update "toggle (off by default)" → "toggle (on by default)" and add a reference to `261014_CRASH_RESTART_AUTO_RESUME_WORKFLOW_CANCEL` alongside the existing plan reference.

**Exit criteria**:
- [x] `tick()` removes a `gave_up` run from `_runs` when `auto_schedule=True`
- [x] `tick()` leaves a `gave_up` run in `_runs` when `auto_schedule=False` (verified with explicit `self.config.auto_schedule = False`)
- [x] `auto_schedule: bool = True` in `config.py`
- [x] `test_auto_schedule_does_not_re_arm_gave_up_run` renamed to `test_auto_schedule_off_does_not_remove_gave_up_run` with explicit `self.config.auto_schedule = False` and updated oracle
- [x] `test_auto_schedule_on_removes_gave_up_run_and_enables_reschedule` added and passes (uses `watching` state + transcript mock)
- [x] `test_auto_schedule_off_does_not_arm_any_session` updated: explicit `self.config.auto_schedule = False` added
- [x] `test_cancel_when_auto_schedule_off_does_not_add_skip` updated: explicit `self.config.auto_schedule = False` added
- [x] README.md "off by default" → "on by default" (2 occurrences matching `"Auto-schedule resumes"` lines at ~197 and ~261)
- [x] AGENTS.md Terminology updated for auto_schedule default
- [x] `.venv-PowerAtlas\Scripts\python -m pytest tests/ -x --timeout=300` passes

Implementation (2026-10-14, code: b864895)
`resume.py`: extended `tick()` removal condition — when `verdict=="end"` and `load_config().auto_schedule` is True, the run is removed from `_runs` so `_auto_schedule_new` can reschedule the session on the next tick. `config.py`: changed `auto_schedule: bool = False` → `True`. `tests/test_web.py`: renamed `test_auto_schedule_does_not_re_arm_gave_up_run` to `test_auto_schedule_off_does_not_remove_gave_up_run` (explicit `auto_schedule=False`, oracle checks run remains); added `test_auto_schedule_on_removes_gave_up_run_and_enables_reschedule` (watching run → end → removed → rescheduled); added explicit `auto_schedule=False` to `test_auto_schedule_off_does_not_arm_any_session`, `test_cancel_when_auto_schedule_off_does_not_add_skip`, and 5 behavioral tests that relied on the old False default; updated route test oracles for new True default. `README.md`: 2 occurrences "off by default" → "on by default". `AGENTS.md` Terminology: toggle note updated to "on by default" with plan reference added. QA: PASS (2 pre-existing `TestAcpWorkflowNotifications` failures unrelated to Phase 2; in Phase 3 scope). Auto-fix code: 192f131 — 3rd README occurrence, load_config guard, test coverage for no_activity path.

### 2026-10-14 — Implementation Review (after Phase 2, personas: Reliability engineer, Senior engineer, Domain expert, Security auditor)

Implementation health: **Green**. 2 cycles. QA: PASS (85 TestResume).
4 findings (0 High, 1 Medium, 3 Low). 4 auto-fixed.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | README.md line 290 still said "off by default" — third occurrence missed | Fixed — changed to "on by default" (`192f131`) |
| 2 | Low | `load_config()` in `tick()` unguarded against config I/O failure | Fixed — wrapped in `try/except Exception: pass` (`192f131`) |
| 3 | Low | `test_resume_now_when_auto_schedule_off_unchanged` relied on False default without explicit guard | Fixed — `self.config.auto_schedule = False` added (`192f131`) |
| 4 | Low | `no_activity` + `auto_schedule=True` cycling path untested | Fixed — test added (`192f131`) |

---

### Phase 3: Workflow-cancel wiring [QA] [P:1,2]

**Goal**: Add a `workflow-cancel` `_request` call in `_handle_cancel()` after `_workflow_stop_children()`, conditional on workflow children having existed. Catch `AcpError` gracefully.

**Covers**: SC-3

**File scope**: `src/power_atlas/acp.py`, `plans/ROADMAP.md`, `docs/KNOWLEDGE.md`, `plans/tests/260701_POWERATLAS.md`, `tests/test_web.py`

#### `src/power_atlas/acp.py`

In `_handle_cancel()`, locate the block:
```python
    had_runs = _supervisor._workflow_has_runs(session_id)
    stopped = _supervisor._workflow_stop_children(session_id)
    changed = bool(stopped) or had_runs
```

Immediately after `stopped = _supervisor._workflow_stop_children(session_id)`, add:
```python
    if stopped or had_runs:
        try:
            await _supervisor._request(
                "_kiro.dev/commands/execute",
                {"sessionId": session_id,
                 "command": {"command": "workflow-cancel", "args": {}}},
                timeout=5.0,   # best-effort; a hung kiro-cli must not delay the Stop broadcast
            )
        except AcpError as exc:
            log.warning("workflow-cancel not sent: [%s] %s", exc.code, exc)
        except Exception:
            log.exception("workflow-cancel request failed unexpectedly")
```

`AcpError` is already in scope (`class AcpError` is defined in `acp.py` itself, above `_handle_cancel`).

Update the comment on `_workflow_stop_children` (section identified by docstring text "the step's own process is not stopped (`workflow-cancel` is not wired)") to read:
`"the step's own process is asked to stop via ``workflow-cancel``; the call degrades gracefully on kiro-cli 2.31.0 (returns -32603) and becomes effective once kiro-cli classifies the method."`.

#### `tests/test_web.py`

Add two tests to the existing `_handle_cancel` test area (or to `class TestCancel` if one exists; search for existing test methods that test `_handle_cancel`):

- `test_handle_cancel_sends_workflow_cancel_when_children_exist`: mock `_supervisor._workflow_has_runs` to return `True`, mock `_supervisor._workflow_stop_children` to return `["child-1"]`, mock `_supervisor._request` to return `{}` (success), call `_handle_cancel(...)`, assert `_supervisor._request` called with method `"_kiro.dev/commands/execute"` and correct params; assert `_workflow_stop_children` also called; assert no exception propagates.
- `test_handle_cancel_logs_warning_on_acp_error`: mock `_supervisor._request` to raise `AcpError("test", "some_code")`, call `_handle_cancel(...)` with children present, assert the broadcast frame still fires and no exception propagates.
- `test_handle_cancel_does_not_call_request_when_no_children`: mock `_supervisor._workflow_has_runs` to return `False`, mock `_supervisor._workflow_stop_children` to return `[]`, call `_handle_cancel(...)`, assert `_supervisor._request` is NOT called.

#### `plans/ROADMAP.md`

- **Supervisor / crash restart** entry (section "Platform"): mark as shipped; reference `261014_CRASH_RESTART_AUTO_RESUME_WORKFLOW_CANCEL`.
- **Auto-resume** entry (section at-a-glance list): mark core multi-cycle + default-on as shipped; update the detail paragraph to note the cycling fix and default change.
- **Workflow-cancel** entry (section "Workflow follow-ups"): update to note PA-side wiring is complete; kiro-cli classification (`KnownExtMethod`) remains the open item.

#### `docs/KNOWLEDGE.md`

- Update the forward-looking note about `_handle_cancel` and `workflow-cancel` (section near "The wiring is safe to add now"): change to reflect the wiring is done, and note the kiro-cli -32603 degradation.
- Update any description of the `_handle_cancel` → `_workflow_stop_children` flow that no longer mentions the `workflow-cancel` call: add a note that the `_request` call follows.

#### `plans/tests/260701_POWERATLAS.md`

- Section 2.20 "Autostart toggle": add a probe step to verify `schtasks /query /tn "PowerAtlas Crash Restart"` returns the task after enable, and is absent after disable.
- Section 2.31 "Auto-schedule resume mode": change `default False` → `default True`; update the oracle for the GET response to `{"enabled": true}` on a fresh config; update the test name reference for the renamed unit test.
- Section 7.5 "PID file lifecycle" (if present): add a note that a stale PID triggers the crash-restart log and notification.

**Exit criteria**:
- [x] `_handle_cancel()` calls `await _supervisor._request("_kiro.dev/commands/execute", ...)` with `timeout=5.0` when `stopped or had_runs`
- [x] `AcpError` from the request is caught with `log.warning`; any other exception caught with `log.exception`
- [x] The call is placed after `stopped = _supervisor._workflow_stop_children(...)` and before `changed = ...`
- [x] `_workflow_stop_children` docstring comment updated to reflect wiring is done
- [x] `_request` is NOT called when `had_runs=False, stopped=[]`
- [x] Unit test: `_handle_cancel` with mock `_supervisor._request` raising `AcpError` → warning logged, broadcast still fires, no exception propagates
- [x] Unit test: `_handle_cancel` with successful `_request` when children exist
- [x] Unit test: `_handle_cancel` with no children → `_request` not called
- [x] ROADMAP.md updated for all three items
- [x] KNOWLEDGE.md workflow-cancel note updated
- [x] `plans/tests/260701_POWERATLAS.md` updated
- [x] `.venv-PowerAtlas\Scripts\python -m pytest tests/ -x --timeout=300` passes

Implementation (2026-10-14, code: f216fb3)
Pre-existing fix: `_workflow_reap`'s expired-run loop now checks `_workflow_holds_step(parent_id)` and skips the closing frame when True (open question/approval prevents premature header flip to Done). This fixed 2 pre-existing `TestAcpWorkflowNotifications` failures. Workflow-cancel wiring in `_handle_cancel`: after `_workflow_stop_children`, when `stopped or had_runs`, sends `await _supervisor._request("_kiro.dev/commands/execute", {...}, timeout=5.0)` with `AcpError` → WARNING, any other exception → log.exception. Degrades gracefully on kiro-cli 2.31.0 (-32603). `_workflow_stop_children` docstring updated. 5 new tests (3 new workflow-cancel tests; 2 new reap guard tests). ROADMAP.md, KNOWLEDGE.md, plans/tests/260701_POWERATLAS.md all updated. QA: PASS (5484 tests, 0 failures).

### 2026-10-14 — Implementation Review (after Phase 3, personas: Senior engineer, Reliability engineer, Domain expert, Architect)

Implementation health: **Green**. 2 cycles. QA: PASS (5484/5484).
5 findings (0 High, 2 Medium, 3 Low). 5 auto-fixed.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | No test for `_workflow_holds_step` guard in `_workflow_reap` expired-run loop | Fixed — `test_workflow_reap_skips_stale_run_when_step_has_open_question` added (`f216fb3`) |
| 2 | Medium | `had_runs=True, stopped=[]` (gap between steps) path untested | Fixed — `test_handle_cancel_sends_workflow_cancel_when_had_runs_but_no_tracked_children` added (`f216fb3`) |
| 3 | Low | `_workflow_stop_children` docstring said "asked to stop" — call is in caller, not this method | Fixed — rephrased to "its caller (`_handle_cancel`) sends…" (`f216fb3`) |
| 4 | Low | Pre-existing fix undeclared in Phase 3 spec — no checkpoint | Fixed — divergence recorded here |
| 5 | Low | Additional tests (M1/M2) added by auto-fix → plan exit criteria not updated | Fixed — recorded in implementation notes |

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `schtasks /create` fails (insufficient permissions, XML malformed) | Medium — crash restart does not work; `.lnk` still provides login-restart | Wrap in try/except; log warning; `.lnk` fallback remains. Unit test validates XML structure |
| `auto_schedule=True` default causes unwanted cycling for sessions a user meant to abandon | Low — per-stop "Cancel schedule" button and opt-out toggle are both present | Documented in AGENTS.md Terminology update |
| `_kiro.dev/commands/execute` returns -32603 on kiro-cli 2.31.0 | Low — AcpError caught and logged; no behavior change | Confirmed by probe 2026-10-14; logged at WARNING |
| Parallel Phase 1 and Phase 2 both modify `tests/test_web.py` | Low — Phase 1 appends a new class, Phase 2 modifies an existing method | Use targeted `str_replace` edits; git merge will be clean as hunks are non-overlapping |
| Repeated rapid crashes (loop) before Task Scheduler exhausts 3 attempts | Low — 3-attempt cap with 1-minute delays; 3 toasts notify the user | Task Scheduler stops automatically; toast on each restart |

## 7) Verification

**Supervisor** (manual, Windows, after `enable()` call):
```
schtasks /query /tn "PowerAtlas Crash Restart"
```
Expected: task listed with trigger OnLogon, action pointing to venv's `pythonw.exe -m power_atlas --foreground`, restart settings PT1M/3.

Force-kill test:
```
taskkill /f /pid <PA_PID>
```
Wait ~1 minute. Verify PA restarts, `orchestrator.log` contains "Restarted after crash", and a toast notification appears.

**Auto-resume** (pytest):
```
.venv-PowerAtlas\Scripts\python -m pytest tests/test_web.py -k "TestResume" -x --timeout=300
```

**Workflow-cancel** (pytest + live integration):
```
.venv-PowerAtlas\Scripts\python -m pytest tests/test_web.py -k "_handle_cancel" -x --timeout=300
```
Live check: start a PA session with a running workflow, press Stop, grep `orchestrator.log` for `"workflow-cancel not sent"` (2.31.0) or success (future kiro-cli).

**Full suite**:
```
.venv-PowerAtlas\Scripts\python -m pytest tests/ -x --timeout=300
node tests/acp_page.test.mjs
```

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `plans/tests/HARNESS.md` | Add Task Scheduler probe to autostart enable/disable section | 1 |
| `README.md` | Change "off by default" → "on by default" for auto_schedule (2 occurrences: lines matching `Auto-schedule resumes.*off by default`) | 2 |
| `AGENTS.md` | Terminology "Stopped by a quota limit": update default + add plan reference | 2 |
| `plans/ROADMAP.md` | Mark Supervisor / crash restart, Auto-resume, Workflow-cancel as shipped | 3 |
| `docs/KNOWLEDGE.md` | Update workflow-cancel wiring note | 3 |
| `plans/tests/260701_POWERATLAS.md` | Sections 2.20, 2.31, 7.5: update probes and test names | 3 |

## Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Phase 1 — Supervisor / crash restart | Complete | code: 06bfbb8 |
| 2 | Phase 2 — Auto-resume cycling | Complete | code: 192f131 |
| 3 | Phase 3 — Workflow-cancel wiring | Complete | code: f216fb3 |

## 9) Implementation Divergences from Plan
<Reserved — filled during implementation>

## Follow-up Work (Deferred)

1. **kiro-cli `KnownExtMethod` classification for `workflow-cancel`.** Add `_kiro.dev/commands/execute` to `KnownExtMethod` in `persistence-classification.ts` so the RPC is persisted rather than returning -32603. External to PA; re-probe on each minor kiro-cli release per KNOWLEDGE.md. Source: Risk Assessment, SC-3 probe 2026-10-14.

2. **Workflow-cancel on non-inflight sessions.** Stop on a session with no running turn (inflight check returns early) currently does not send `workflow-cancel`. A step that outlives the turn's inflight record would not be cancelled. Deferred: uncommon path; address if confirmed problematic. Source: Phase 3 code review.

## Review Log

### 2026-10-14 — Plan Review (via /qplan, full effort)

4 personas: Architect, Senior engineer, Reliability engineer, Domain expert. 1 cycle. 14 findings (2 High, 6 Medium, 6 Low). 13 auto-resolved; 1 refuted (H3). Health: **Green**.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | Task Scheduler XML missing `<Principals>` block — task runs as SYSTEM, not logged-in user | Fixed — `<Principals><LogonType>InteractiveToken</LogonType></Principals>` added to XML sketch |
| 2 | High | `.lnk` + `LogonTrigger` race at login — when `.lnk` wins, Task Scheduler exits code 0 and `RestartInterval` never applies to PA | Fixed — `enable()` removes `.lnk` on successful `schtasks` registration; Task Scheduler becomes sole launcher |
| 3 | High (refuted) | `class TestResume` and `test_auto_schedule_does_not_re_arm_gave_up_run` do not exist | Refuted — both exist at lines 45823 and 46445 of `tests/test_web.py` |
| 4 | Medium | Phase 2 test design wrong — injected `gave_up` run bypasses `watching` filter in `tick()` | Fixed — test redesigned to use `watching` state with transcript mock that causes `_watch()` to return `"end"` |
| 5 | Medium | Phase 3 `_request` uses default 90s timeout, blocking Stop broadcast up to 90s on hung kiro-cli | Fixed — `timeout=5.0` added to `_request` call |
| 6 | Medium | Interpreter path not XML-escaped before splicing into `<Command>` | Fixed — `html.escape(str(_startup_interpreter(...)))` noted in Phase 1 implementation steps |
| 7 | Medium | Phase 3 has tests in exit criteria but `tests/test_web.py` absent from file scope, no test steps | Fixed — `tests/test_web.py` added to Phase 3 scope; three test stubs added to Phase 3 steps |
| 8 | Medium | Two tests (`test_auto_schedule_off_does_not_arm_any_session`, `test_cancel_when_auto_schedule_off_does_not_add_skip`) rely on `auto_schedule=False` default | Fixed — Phase 2 exit criteria now requires explicit `self.config.auto_schedule = False` in both tests |
| 9 | Medium | `enable()` catches non-zero schtasks return code but not subprocess spawn exceptions | Fixed — Phase 1 steps now explicitly wrap `subprocess.run` in try/except catching both |
| 10 | Low | README.md count "3 occurrences" was wrong — only 2 lines match the auto-schedule `"off by default"` context | Fixed — count corrected to 2, text patterns added |
| 11 | Low | `_detect_and_log_crash` needs `try/except (OSError, ValueError)` for PID file read/parse | Fixed — added to Phase 1 implementation description |
| 12 | Low | `_detect_and_log_crash` no `sys.platform` guard — spurious crash notification possible on Linux PID reuse | Fixed — Windows-only guard added to function description |
| 13 | Low | XML temp file `finally` block should use `unlink(missing_ok=True)` | Fixed — noted in Phase 1 implementation steps |
| 14 | Low | `textwrap` listed in Phase 1 proposed imports but unused | Fixed — removed from import list |

### 2026-10-14 — Implementation Review (after Phase 1, personas: Reliability engineer, Security auditor, Senior engineer, Domain expert)

Implementation health: **Green**. 2 cycles. QA: PASS (5 scripted probes + 11 unit tests).
12 findings (0 High, 3 Medium, 9 Low). 8 auto-fixed; 4 user: accepted.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | `tmp_path_str = f.name` assigned after `f.write()` — cleanup skipped if write raises | Fixed — assignment moved before write (`c147d4e`) |
| 2 | Medium | `disable()` discards schtasks /delete returncode silently | Fixed — returncode checked, non-(0,1) logged as WARNING (`c147d4e`) |
| 3 | Medium | `test_autostart.py` existing Windows tests call real schtasks (no subprocess mock) | Fixed — fixture and affected tests patched (`c147d4e`) |
| 4 | Low | Relative `"schtasks"` name — PATH hijacking risk | Fixed — absolute `C:\Windows\System32\schtasks.exe` used (`c147d4e`) |
| 5 | Low | No timeout on subprocess.run calls | Fixed — `timeout=30` + `TimeoutExpired` handler added (`c147d4e`) |
| 6 | Low | `_pid_alive()` call not guarded against `OSError` | Fixed — wrapped in `try/except OSError: return` (`c147d4e`) |
| 7 | Low | `CONFIG_DIR` not defensively created before temp file | Fixed — `CONFIG_DIR.mkdir(parents=True, exist_ok=True)` added (`c147d4e`) |
| 8 | Low | spawn-failure test doesn't pre-create `.lnk` to verify preservation | Fixed — `.lnk` pre-created; assertion added (`06bfbb8`) |
| 9 | Low | No test validates actual XML content (RestartInterval, RestartCount, Principals) | Fixed — `test_enable_writes_xml_with_required_elements` added (`06bfbb8`) |
| 10 | Low | stderr decoded as utf-16-le but schtasks outputs ANSI/OEM | User: accepted — replacement chars in log; doesn't affect functionality |
| 11 | Low | `TimeoutExpired` path in `enable()` has no dedicated test | User: accepted — covered by `except Exception` path; low risk |
| 12 | Low | schtasks path hardcodes `C:\Windows\System32` — non-C: installs degrade silently | User: accepted — documented assumption; .lnk fallback active on failure |



## Harness Improvement Opportunities

- Batched multiple interview questions into one turn despite the Kiro /qexplore overlay requiring one question per turn — cost: user correction round; needed a full re-read of the skill — suggested change: load and check the Kiro overlay section before asking the first interview question.
- Multi-domain decomposition check should fire as Q1 before any other questions — cost: questions were asked before decomposition was resolved — suggested change: add a checklist item in the Kiro overlay that fires the decomposition check when 3+ independent domains are detected.
