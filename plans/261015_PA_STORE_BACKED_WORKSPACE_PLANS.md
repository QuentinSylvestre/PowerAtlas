# PA — Store-Backed Workspace Plans Support

> **Date**: 2026-10-15
> **Status**: Complete  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Add PowerAtlas Active Plans support for workspaces that keep plans in the agent-playbook store
> **Memory relied on**: none
> **Estimated effort**: 1-2 days

---

## Intent

### Problem statement & desired outcomes

Some workspaces keep their plans in the agent-playbook config repo store
(`agent-playbook/workspaces/<key>/plans/`) rather than in the workspace's own
`plans/` directory. PowerAtlas's Active Plans panel scans `<session_cwd>/plans/*.md`
for each workspace; it has no knowledge of the store, so plans kept there are invisible.

Desired outcomes:

- Plans in the agent-playbook store appear in the PowerAtlas Active Plans panel for
  their workspace, indistinguishable from locally-kept plans.
- Existing workspace plan discovery is unchanged for workspaces with no store.
- Activity (stale badge, sort) correctly reflects commit history in the store's git repo.

### Success criteria

- SC-1: A plan file at `agent-playbook/workspaces/<key>/plans/*.md` with
  `Status: In Progress` appears in the PA Active Plans panel under the correct
  workspace name after a PA restart.
- SC-2: A workspace with a store but no `plans/` directory in the store continues
  to show plans from its own `plans/` directory (fallback preserved).
- SC-3: A workspace with no store (`__store__.md` absent) is unaffected.
- SC-4: The `stale` badge and sort order for a store plan use the plan's slug-scoped
  commit date from the agent-playbook repo (not file mtime).
- SC-5: Two discovery modes. When `playbook_root` is set: PA builds a WORKSPACE map
  by scanning `workspaces/*/WORKSPACE` in that root and looks up each workspace. When
  not set: PA parses each workspace's own `__store__.md` directly to find its store
  directory (per-cwd, no root inference). In both modes the config repo root can also
  be set explicitly via a new `playbook_root` config field.
- SC-6: All existing `TestOverviewPlans` tests continue to pass unchanged.

### Scope boundaries & non-goals

**In scope:**

- `overview.py`: store-aware `scan_plans` — reads store pointer, redirects `plans_dir`
  and `_plan_activity` git cwd when the store has a `plans/` directory.
- `overview.py`: store discovery functions — infers config repo root from `__store__.md`
  (non-override path) or `playbook_root` config field (override path); scans
  `workspaces/*/WORKSPACE` files for the authoritative map when override is set; caches
  with 30 s TTL.
- `config.py`: new optional `playbook_root: str` field in `Config`; overrides inference.
- `__main__.py`: call `overview.apply_config(config.playbook_root)` at startup.
- `tests/test_web.py`: new tests in `TestOverviewPlans` for store-backed plan discovery,
  fallback, git activity via store repo, and WORKSPACE map building.

**Out of scope (v1):**

- UI indicator distinguishing store-kept plans from local plans.
- Supporting workspaces whose store is not under a recognized config repo layout.
- Multiple config repos (first-encountered wins; a conflict is logged and ignored).
- Supporting symlinks inside `plans/` (existing lstat guard unchanged).

---

## 1) Current State

`scan_plans(workspaces, deadline_s=2.0, now=None)` in `overview.py` (under
`## Active plans`) iterates `(cwd, name)` pairs. For each it sets
`plans_dir = Path(cwd) / "plans"` and scans top-level `*.md` there. `plans/done/`
is excluded. Plan file activity is computed by `_plan_activity(cwd, path, mtime)`,
which runs `git -C cwd log` with the plan's slug as the scope pattern.

`_overview_workspaces()` in `web.py` (under `## Dashboard — Overview`) is shared by
`scan_plans`, `gitstate.request`, and `gitstate.overview_payload`. Changing the `cwd`
there would route gitstate calls to the agent-playbook repo, wrongly showing its dirty
state under every store-backed workspace. Store detection must happen inside
`scan_plans`, not in `_overview_workspaces`.

`plan["cwd"]` is emitted in the JSON response and consumed by `index.html`'s
expand-state key and tooltip. It must remain the workspace session path.

`_plan_memo` is keyed by `str(path)`. A store path differs from the workspace's own
`plans/` path, so cache hits never collide and eviction (on a completed scan) works
correctly via the `seen` set.

`config.Config` is a dataclass in `config.py`. Any non-`_`-prefixed field is
automatically included in `_STORED_FIELDS` and round-tripped through `config.toml`.
`load_config`'s type check (`isinstance(v, expected)`) accepts a TOML string for a
`str = ""` field. Sanitization (strip) is applied inline in `_load_config_once` after
`Config(**kwargs)`, alongside other per-field normalizations.

`__main__.py` calls `acp_module.apply_config(config)` at startup to inject
restart-only tunables as module-level variables in `acp.py`. The same pattern is used
here for `overview.py`.

Measured `__store__.md` format (diskpeek, 2026-10-15):
```
The store directory is:

`C:\Users\...\agent-playbook\workspaces\diskpeek-aea199`
```
WORKSPACE file content: single-line workspace path, e.g.
`C:\Users\...\diskpeek`.

---

## 2) Goal

Add `_get_store_dir(cwd) -> Path | None` to `overview.py` that returns the
agent-playbook store directory for a workspace, using a cached WORKSPACE map (when
`playbook_root` is configured) or direct `__store__.md` parsing (when not). Modify
`scan_plans` to redirect `plans_dir` and the `_plan_activity` cwd argument to the
store when `_get_store_dir` returns a store directory with a `plans/` subdirectory.
Add a `playbook_root: str` field to `Config` and wire `overview.apply_config` at
startup.

---

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Store detection placement | Inside `scan_plans`, not `_overview_workspaces` | Modify `_overview_workspaces` | Changing cwd there would route gitstate git calls to the store repo (D1 from exploration) |
| `scan_plans` signature | Unchanged (Option b: module-level override) | Pass `config` to `scan_plans` | Preserves existing test call sites (`test_web.py` `TestOverviewPlans._scan`) |
| Non-override path | Direct `__store__.md` parse per cwd; no map or root inference | WORKSPACE map for both paths | Simpler; a workspace with `__store__.md` already knows its own store dir directly |
| Override path | WORKSPACE map from `playbook_root` | None (override only) | When the user sets `playbook_root`, enumerate all store-backed workspaces without per-cwd `__store__.md` parse |
| Normalization for WORKSPACE map | Both build side and lookup side normalize via `_normalize_path(str(Path(s).expanduser()))` | `_normalize_path(s)` only on lookup | `_normalize_path` expands 8.3 names but not `~`; WORKSPACE files and session cwds may differ in `~` expansion; symmetric normalization eliminates silent key misses |
| `git_cwd` for activity | Store directory (not workspace cwd) when store plans are used | Keep workspace cwd | Plan commits live in the agent-playbook repo; using workspace cwd finds nothing and falls back to mtime |
| `plan["cwd"]` | Always original workspace cwd | Store dir | Preserves expand-state key and tooltip in the browser |
| `os.path.expanduser` on Windows domain accounts | `Path(raw).expanduser().resolve()` — Python 3.6+ uses `USERPROFILE`, handles redirected homes | `Path.home()` as fallback | `Path.expanduser()` is equivalent and the accepted standard pattern; no fallback needed (verified behavior) |
| UNC guard for resolved store path | Apply `_skip_cwd` + `_resolves_to_unc` to the resolved store dir before using it | Rely on existing `plans_dir` guard | `_resolves_to_unc` runs on `plans_dir` only, after `_get_store_dir` already called `.resolve()` — a network store stalls inside `_get_store_dir` before the guard fires |
| Deadline check before `_get_store_dir` | Check `time.monotonic() >= deadline` before calling `_get_store_dir` (index > 0 gate, mirroring `_plan_activity`) | No check; rely on TTL | `_get_store_dir` does file I/O (`__store__.md` read, `.resolve()`, and in override mode a `workspaces/*/` scan); unbounded in a loop respects D21's "one stall, not zero" model but must not compound |
| Cache lock scope for WORKSPACE map | TTL check + build + write all under `_store_map_lock`, following `_plan_memo_lock`'s tolerate-duplicate-parse convention | Check outside lock, build outside, write under lock | `_plan_memo_lock` documents "two threads parsing the same file at once is tolerated"; same convention here; however, `_build_store_map` is a full dir scan (~11 workspaces), so explicitly locking the whole check-build-write prevents the duplicate scan while keeping the same semantics |
| Intra-package `_normalize_path` import | `from . import data` then `data._normalize_path()`, inside function body | `from .data import _normalize_path` | Matches the 8 established import call sites in `overview.py` and all uses in `data_kiro_v3.py` and `presence.py` |
| `_check_public_ids.py` and test fixtures | Use `tmp_path` (pytest temp dirs) for workspace paths | Not applicable | Pytest temp paths don't match session ID patterns; no clash with the checker (verified by format comparison) |

---

## 4) External Dependencies & Costs

### Required external changes

None — pure Python changes to PA itself.

### Cost impact

None.

---

## 5) Implementation Phases

### Phase 1: Config field [QA]

**Goal**: Add `playbook_root: str = ""` to `Config` and sanitize on load, so the
override path in Phase 2 has a config-backed source, and the field is persisted to
`config.toml` when set.

**Covers**: SC-5 (config field), SC-6

**File scope**: `src/power_atlas/config.py`, `tests/test_web.py`

**Changes:**

`config.py` — add before `auto_schedule: bool = True` (after `acp_permission_base_agent`:
after the ACP block, inside the stored fields region):

```python
# Absolute path to the agent-playbook config repo. When set, overview.py
# uses this root to build the WORKSPACE map directly instead of parsing
# __store__.md per workspace. Empty string (the default) means auto-detect
# from __store__.md.
# 261015_PA_STORE_BACKED_WORKSPACE_PLANS Phase 1
playbook_root: str = ""
```
<!-- sketch: exact file position is above `auto_schedule` -->

In `_load_config_once`, add after the `config.peek_mode = _normalize_peek_mode(...)` line:

```python
config.playbook_root = config.playbook_root.strip()
```
<!-- sketch -->

**Exit criteria:**
- [x] `Config()` has `playbook_root: str = ""` and `Config(playbook_root="  /x  ").playbook_root == "  /x  "` (raw field; strip happens in `_load_config_once`)
- [x] `load_config()` on a TOML with `playbook_root = "  /x  "` returns a config where `.playbook_root == "/x"` (strip applied)
- [x] `load_config()` on a TOML without `playbook_root` returns `.playbook_root == ""`
- [x] `_STORED_FIELDS` includes `"playbook_root"` (verified by `"playbook_root" in config._STORED_FIELDS`)
- [x] All existing `TestOverviewPlans` tests pass (SC-6)

**Test additions in `tests/test_web.py`** (inside the existing `isolated_config` fixture scope):
- `test_playbook_root_defaults_to_empty`: `load_config()` with no `playbook_root` key → `""`.
- `test_playbook_root_is_stripped_on_load`: TOML `playbook_root = "  /x/y  "` → `"/x/y"`.

---

### Phase 2: Overview store infrastructure + `scan_plans` + startup wiring [QA]

**Goal**: Implement `_get_store_dir` and helper functions in `overview.py`, modify
`scan_plans` to use them, call `overview.apply_config` at startup.

**Covers**: SC-1, SC-2, SC-3, SC-4, SC-5, SC-6

**File scope**: `src/power_atlas/overview.py`, `src/power_atlas/__main__.py`,
`tests/test_web.py`

**New module-level state in `overview.py`** (after the existing `_plan_memo` block,
under `# --- Active plans ---`):

```python
# Store-backed plan support (261015_PA_STORE_BACKED_WORKSPACE_PLANS).
# When set, _get_store_dir uses the WORKSPACE map built from this root instead
# of parsing __store__.md per workspace.
_playbook_root_override: str = ""
# Cache: [monotonic_at, {normalized_cwd: store_dir_path} | None, root_str]
# Third element allows invalidation when the override changes at apply_config.
_store_map_cache: list = [0.0, None, ""]
_store_map_lock = threading.Lock()
_STORE_MAP_TTL = 30.0
```
<!-- sketch: exact names; comment is the sole documentation -->

**New functions in `overview.py`:**

`apply_config(playbook_root: str) -> None` — sets `_playbook_root_override` and
clears `_store_map_cache` so the next `_get_store_dir` call rebuilds from the new
root. Module-level, called once at startup. Must not raise.

`_parse_store_dir_from_cwd(cwd: str) -> Path | None` — reads
`Path(cwd) / ".kiro" / "steering" / "__store__.md"`, applies
`re.search(r"The store directory is:\s*\n\s*\n`([^`\n]+)`", text)` (this regex is
tied to the `/qwire-governance`-generated `__store__.md` format; a format change
will silently return `None` — the test fixture must reproduce the exact format to pin this),
then `Path(raw.strip()).expanduser().resolve()`. After resolving, apply `_skip_cwd`
string check and `_resolves_to_unc(os.path.realpath(str(resolved)))` to guard against
a store path on a UNC/network share — if either check fails, return `None`. Returns
`None` on any `OSError` or regex miss. No caching; called only when
`_playbook_root_override` is `""`.

`_build_store_map(playbook_root: Path) -> dict[str, Path]` — iterates
`(playbook_root / "workspaces").iterdir()`, reads each subdirectory's `WORKSPACE`
file, normalizes via `from . import data; data._normalize_path(str(Path(ws_path).expanduser()))`,
returns `{norm_cwd: store_dir_path}`. Per-entry `OSError` is swallowed with a
`log.debug` (path only, never content). A failure of the whole iteration loop is also
swallowed with a `log.debug`. Returns an empty dict (never `None`).

`_store_map_cache + _store_map_lock + _STORE_MAP_TTL` — the TTL check, `_build_store_map`
call, and `_store_map_cache[:]` write all run **inside** `_store_map_lock`, following
`_plan_memo_lock`'s convention: "guards get/put only; two threads parsing the same file
at once is tolerated." Here the "file" is the whole `workspaces/*/WORKSPACE` scan, so
holding the lock for the build avoids duplicate scans while remaining semantically safe
(two maps from the same directory contents are value-equivalent).

`_get_store_dir(cwd: str) -> Path | None` — two paths:

- **Override path** (`_playbook_root_override` non-empty): inside `_store_map_lock`,
  check TTL; if stale, build via `_build_store_map(Path(_playbook_root_override).expanduser().resolve())`
  and write to `_store_map_cache`. Lookup: `store_map.get(from . import data; data._normalize_path(str(Path(cwd).expanduser())))`.
- **Non-override path**: call `_parse_store_dir_from_cwd(cwd)` directly and return
  the result. No map cache needed (each workspace already knows its own store dir).

In both paths: catch all exceptions and return `None` rather than raise. The import
`from . import data` for `_normalize_path` lives inside the function body, matching
the 8 established call sites in `overview.py` (e.g., in `_parse_tail`,
`live_sessions`).

**Modification to `scan_plans`:**

Replace the current `plans_dir = Path(cwd) / "plans"` line with:

```python
# SC-3 fast path: skip store lookup past deadline (mirrors _plan_activity gate).
store_dir = None
if index == 0 or time.monotonic() < deadline:
    store_dir = _get_store_dir(cwd)
if store_dir is not None and (store_dir / "plans").is_dir():
    plans_dir = store_dir / "plans"
    git_cwd = str(store_dir)   # plan commits live in the store repo
else:
    plans_dir = Path(cwd) / "plans"
    git_cwd = cwd
```
<!-- sketch: `store_dir = None` initialised before the deadline gate; `index == 0` preserves the existing "first workspace always processed" invariant -->

Replace the `_plan_activity(cwd, path, mtime)` call with `_plan_activity(git_cwd, path, mtime)`.

Keep `plan["cwd"] = cwd` (the original workspace path) unchanged.

The UNC check (`_resolves_to_unc`) on `plans_dir` applies equally to the store path:
`plans_dir` is already the re-assigned store plans dir at that point, so the existing
check fires correctly for both cases.

**`__main__.py` startup wiring:**

After `set_startup_config(config)` (or alongside `acp_module.apply_config(config)`),
add:

```python
# Store-backed plan discovery: inject the configured playbook root once.
from .overview import apply_config as _overview_apply_config
_overview_apply_config(config.playbook_root)
```
<!-- sketch -->

**Test additions in `tests/test_web.py`** (inside `TestOverviewPlans`):

Helper `_store_ws(root, store_root, ws_name, plan_files)`: creates a workspace dir
without a local `plans/`, writes `__store__.md` pointing at a store subdir (using the
exact format generated by `/qwire-governance`: "The store directory is:\n\n`<path>`"),
writes WORKSPACE file in the store subdir, writes plan files inside
`<store_subdir>/plans/`. Returns `(ws_path, store_subdir)`. Does NOT set up a git
repo — tests needing git activity (SC-4) use the existing `_plan_repo`/`_git`/
`_need_git` helpers on the store subdir directly.

- `test_store_backed_plan_appears` (SC-1): workspace with `__store__.md` → store has
  an `In Progress` plan → `scan_plans` lists it under the workspace name; `plan["cwd"]`
  is the workspace path.
- `test_store_no_plans_dir_falls_back_to_workspace` (SC-2): store has no `plans/` dir;
  workspace has local `plans/` with a plan → `scan_plans` lists the local plan.
- `test_no_store_md_unaffected` (SC-3): workspace has no `__store__.md`; workspace has
  local `plans/` → identical to pre-feature behavior; no store lookup happens.
- `test_store_git_activity_uses_store_repo` (SC-4): store plan's slug-scoped commit is
  in the store's git repo (set up via `_plan_repo`/`_git`/`_need_git` on the store
  subdir) → `plan["activity"]` reflects that commit date, not the file mtime. Mirrors
  `test_activity_is_the_slug_commit_not_a_later_rewrite` fixture but with a store repo
  instead of a workspace repo.
- `test_playbook_root_override_builds_workspace_map` (SC-5): set
  `overview._playbook_root_override` to a temp agent-playbook root; `scan_plans` on a
  workspace in that root's WORKSPACE map → plan from the store appears; reset
  `_playbook_root_override` and clear `_store_map_cache` in teardown.
- `test_store_stale_badge_from_store_activity` (SC-4 supplement): store plan with no
  recent slug-scoped commit → `stale` badge fires correctly based on store repo
  activity.
- `test_existing_tests_pass_with_store_inactive` (SC-6): run the existing
  `test_tracker_rows_skip_the_non_numeric_row` fixture unchanged with no store
  configured → result matches pre-feature behavior; documents that SC-6 is actively
  verified, not merely assumed.
- `test_apply_config_clears_cache` (cache invalidation): set `_playbook_root_override`
  to root A, call `_get_store_dir` (builds the map), call `apply_config(root_B_path)`,
  assert `_store_map_cache` is cleared (first element `== 0.0`); next `_get_store_dir`
  call rebuilds against root B.
- `test_store_git_fallback_to_mtime_when_no_repo` (SC-4 fallback): store `plans/`
  present, store dir is NOT a git repo → `_plan_activity` returns store file mtime;
  `plan["stale"]` and `plan["activity"]` reflect mtime, scan completes normally.
- `_fresh` autouse fixture must also reset `overview._playbook_root_override = ""`
  and `overview._store_map_cache[:] = [0.0, None, ""]` alongside the existing
  `_plan_memo.clear()` to prevent state leakage between tests.

**Exit criteria:**
- [x] SC-1 test passes: store-backed plan appears with correct workspace name and `cwd`
- [x] SC-2 test passes: fallback to workspace `plans/` when store has no `plans/`
- [x] SC-3 test passes: no-store workspace unaffected (no `__store__.md` → no lookup)
- [x] SC-4 test passes: `plan["activity"]` uses store repo commit date
- [x] SC-4 fallback test passes: no-git-repo store → `plan["activity"]` = file mtime
- [x] SC-5 test passes: `playbook_root` override builds WORKSPACE map and looks up correctly
- [x] `apply_config` cache-clearing test passes: new root triggers map rebuild
- [x] UNC guard verified: `_parse_store_dir_from_cwd` rejects a UNC-resolved store path (test or code-read)
- [x] Deadline gate: `store_dir = None` initialized before deadline check; store lookup skipped past deadline for index > 0
- [x] `apply_config` called in `__main__.py` and accepted by linter (`python -m py_compile`)
- [x] All existing `TestOverviewPlans` tests pass (SC-6): `pytest tests/test_web.py -k "TestOverviewPlans" -x`
- [x] Pre-commit hooks pass: `_check_public_ids.py` and `_check_test_names.py`

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `__store__.md` format changes | Medium — plans not discovered | Regex anchored to stable prose ("The store directory is:"); returns `None` on miss (safe fallback) |
| Store path on a UNC/network share | Low — scan stalls | Existing `_resolves_to_unc` check applies to redirected `plans_dir`; stall bounded per-cwd |
| `_normalize_path` import in `overview.py` | Low — circular import | Imported inside function body, same pattern as other intra-package imports in `overview.py` |
| `apply_config` never called (e.g. test isolation) | Low — override path disabled, non-override path still works | Feature works without override; tests that need the override set `_playbook_root_override` directly and reset it |
| WORKSPACE file contains invalid path | Low — workspace skipped | `ValueError`/`OSError` caught; entry skipped with no log spam |

---

## 7) Verification

```powershell
# Run targeted plan tests (SC-6 + new tests)
.venv-PowerAtlas\Scripts\python -m pytest tests/test_web.py -k "TestOverviewPlans" -x -v

# Pre-commit hooks
.venv-PowerAtlas\Scripts\python _check_public_ids.py
.venv-PowerAtlas\Scripts\python _check_test_names.py

# Syntax check for __main__.py change
.venv-PowerAtlas\Scripts\python -m py_compile src/power_atlas/__main__.py
```

Live QA (after PA restart with diskpeek open):
- Navigate to PA Overview → Active Plans — the diskpeek plan (`261015_PA_STORE_BACKED_WORKSPACE_PLANS.md`, Status: Draft at time of QA; set to In Progress to test) should appear.
- Confirm `plan["cwd"]` in the API response (`GET /api/dashboard/overview/summary`) is the diskpeek workspace path, not the agent-playbook store path.

---

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `plans/tests/260701_POWERATLAS.md` § 2.29 | Note store-backed plan behavior; mention new SC-1 through SC-5 test additions | doc-table-only |

---

## Progress Tracker

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Config field | Complete | code: 1ff8b71 |
| 2 | Overview store infrastructure + tests | Complete | code: bb11f06 / a1759ef |

---

## 9) Implementation Divergences from Plan

**Phase 1 — Config field:**

1. **Field placement**: plan sketch said "after `auto_schedule: bool = True`"; a newer stored field `kiro_daily_reset_minutes_utc: int = 0` was added since the plan was written. `playbook_root` was placed after that field — i.e. at the very end of the stored-field block, immediately before the `# ---- What load_config found, never stored` comment. `_STORED_FIELDS` is name-keyed; round-trip is unaffected by order.

2. **Test fixture bug in plan snippet**: plan snippet used `p.write_bytes(tomli_w.dumps({...}))`, but `tomli_w.dumps` returns `str`; `write_bytes` would raise `TypeError`. Fixed to `p.write_text(tomli_w.dumps({...}))`.

3. **Test placement**: there is no dedicated config-loading test class in `tests/test_web.py`. The two new tests were added to `TestRemoteBindAddressValidation`, which uses the `isolated_config` autouse fixture and contains the existing `test_load_config_*` sanitization tests (the same CONFIG_PATH-assignment pattern). The class name is unrelated to playbook_root (noted by reviewers as a Low/Medium discoverability issue); a future refactor can move them to a `TestConfigFields` class. The tests are functionally correct, mutation-discriminating, and passing.

**Phase 2 — Overview store infrastructure:**

4. **Lock discipline (build-outside-lock)**: Plan spec and Phase 1 review finding #4 both said "TTL check, build, and write all run inside `_store_map_lock`". The implementation uses "check under lock / build outside lock / write under lock", matching `_plan_memo_lock`'s documented convention ("two threads parsing the same file at once is tolerated"). Superior choice (readers never blocked during a `workspaces/*/` scan); documented in `_get_store_dir` docstring. The fix commit `a1759ef` added a UNC guard to `_build_store_map` and a UNC rejection test.

5. **`test_store_stale_badge_from_store_activity` omitted**: Plan's test-additions listed this as a SC-4 supplement. Absent from delivered tests — SC-4 is adequately covered by `test_store_git_activity_uses_store_repo` and `test_store_git_fallback_to_mtime_when_no_repo`; stale logic is store-agnostic.

## Follow-up Work (Deferred)

1. **UI indicator for store-kept plans.** A visual badge distinguishing store plans
   from local plans (e.g., a small icon or tooltip note) is out of v1 scope but
   would help the user understand why a plan appears under a workspace that has no
   local `plans/` directory. Source: scope boundary.

2. **`playbook_root` in the Settings dialog.** The field is config.toml-only in v1;
   there is no UI to set it. A text field in the General settings section would make
   the feature usable without hand-editing `config.toml`. Source: scope boundary.

## Review Log

### 2026-10-15 — Phase 1 Review (full effort, 4 personas)

10 findings (0 High, 1 Medium, 9 Low). 1 Medium auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | Tests in `TestRemoteBindAddressValidation` class with no §9 divergence note; placement unjustified and undiscoverable. (Corroborated: Senior + Maintainability) | Fixed — §9 divergence note written explaining the class choice. |
| 2 | Low | `config_mod.CONFIG_PATH = ` raw assignment instead of `monkeypatch.setattr` in new tests, diverging from sibling convention. (Corroborated: Senior + Maintainability) | Accept-and-note. Tests are correct and protected by the autouse fixture; consistency improvement deferred. |
| 3 | Low | No `isinstance(..., str)` re-check before `config.playbook_root.strip()`, unlike adjacent sanitizers in `_load_config_once`. (Single-source: Architect) | Accept — type gate in load loop guarantees `str` before strip. Style-only divergence. |
| 4 | Low | No `_strip_control_chars` / length cap on `playbook_root` unlike `default_directory`. (Single-source: Reliability) | Accept-and-note — mitigated by Phase 2's `Path(...).resolve()`. |
| 5 | Low | `test_playbook_root_defaults_to_empty` exercises no-file path, not present-file-missing-key. (Single-source: Senior) | Accept — no-file branch is the simpler and more common case; both code paths produce the same result. |
| 6 | Low | Field placement after `kiro_daily_reset_minutes_utc` rather than `auto_schedule` — noted in divergences. (Single-source: Architect) | Accept — documented in §9. |
| 7 | Low | No `save_config` + `load_config` round-trip test. (Corroborated: Reliability + Maintainability) | Accept-and-note — structural correctness verified via `_STORED_FIELDS` membership; round-trip test would be belt-and-suspenders. |
| 8 | Low | `test_playbook_root_defaults_to_empty` sets `CONFIG_PATH` redundantly (fixture already handles it). (Single-source: Maintainability) | Accept — harmless. |
| 9 | Low | Plan §9 placeholder "Reserved" note was not written during implementation — sub-agent didn't record placement rationale. (Single-source: Maintainability) | Fixed — orchestrator wrote §9 immediately on receiving review. |
| 10 | Low | QA step 5b: [QA]-annotated phase; behavior verified by new passing pytest tests. SKIP — no runtime surface beyond pytest. | SKIP — recorded. |

Phase 1 health: **Green** (0 unresolved High, 0 auto-fixable Medium remaining).
Implementation note: `playbook_root: str = ""` added to `Config`; `config.playbook_root = config.playbook_root.strip()` added in `_load_config_once`. Two tests pass: `test_playbook_root_defaults_to_empty`, `test_playbook_root_is_stripped_on_load`. SC-6: 50 `TestOverviewPlans` tests pass.

### 2026-10-15 — Phase 2 Review (full effort, 4 personas; 1 auto-fix cycle)

13 findings (0 High, 3 Medium, 10 Low). All Mediums auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | `_build_store_map` adds WORKSPACE-derived store dirs to the map without UNC guard; an override WORKSPACE pointing at `\\server\share` reaches `realpath` in `scan_plans` with no string pre-check. (Corroborated: Architect + Senior) | Fixed — added `_skip_cwd`+`_resolves_to_unc` check per store dir in `_build_store_map` in auto-fix commit `a1759ef`. |
| 2 | Medium | No test verifies `_parse_store_dir_from_cwd` returns None for a UNC store path; exit criterion satisfied by code-read only. (Corroborated: Architect + Maintainability) | Fixed — added `test_store_parse_rejects_unc_store_dir` in `a1759ef`; 57 TestOverviewPlans pass. |
| 3 | Medium | Lock discipline (build outside lock) reverses plan spec + Phase 1 review finding #4 with no §9 entry. Code is correct/superior, but undocumented divergence. (Corroborated: Architect + Senior) | Fixed — §9 Phase 2 divergence note written. |
| 4 | Low | `_build_store_map` doesn't guard `playbook_root` itself being UNC; `(root/"workspaces").iterdir()` could stall before the OSError catch. (Single-source: Architect) | Accept-and-note. User-controlled config; apply `_resolves_to_unc` on root in future if needed. |
| 5 | Low | `apply_config` writes `_playbook_root_override` outside `_store_map_lock`; relies on GIL atomicity and startup-only timing. (Single-source: Architect) | Accept — startup-only, GIL-atomic single assignment; self-healing via `cached_root` TTL check. |
| 6 | Low | WORKSPACE path not `.resolve()`d in `_build_store_map`; a subst/symlinked cwd could key-miss. (Single-source: Architect) | Accept-and-note — common cases covered; symlink edge rare. |
| 7 | Low | `test_store_stale_badge_from_store_activity` promised in plan but absent. SC-4 is well covered otherwise. (Corroborated: Senior + Maintainability) | Accept-and-note — §9 records omission with rationale. |
| 8 | Low | Stale-root stamping race: map built from root before lock, but stamped with `_playbook_root_override` after lock; can write map with wrong root string. (Single-source: Reliability) | Accept — startup-only; unreachable in production; docstring discloses outside-lock build. |
| 9 | Low | Non-override path has no memoization for `__store__.md` reads; re-reads on every scan. (Single-source: Reliability) | Accept-and-note — bounded by web layer 30s cache; add per-cwd memo only if store workspace count grows large. |
| 10 | Low | Store-backed workspace past deadline (index>0) silently falls back to local `plans/`. (Single-source: Reliability) | Accept — intended D21 degradation per "first workspace always processed" invariant. |
| 11 | Low | `_build_store_map` return annotation is bare `dict` rather than `dict[str, Path]`. (Single-source: Maintainability) | Accept-and-note — mirrors existing `_plan_memo` convention; tighten annotation in a cleanup pass. |
| 12 | Low | QA step 5b: [QA]-annotated phase. Python API exercised by 57 passing pytest tests. PA restart required for live browser surface (no grant for this task). | SKIP — PA restart deferred to Step 9b or user-initiated restart. |
| 13 | Low | `_get_store_dir` has check-outside-lock read (via `_store_map_cache` unpack) not protected; concurrent `apply_config` call can see partially-cleared cache. (Reviewed and accepted at implementation) | Accept — GIL-atomic list element access; list is immutable-size 3; no torn read possible on CPython. |

Phase 2 health: **Green** (0 unresolved High, 0 auto-fixable Medium remaining).

### 2026-10-15 — Step 9 Final Holistic Review (full effort, 4 personas)

12 findings (0 High, 1 Medium, 11 Low). 1 Medium auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | `plans/tests/260701_POWERATLAS.md` §2.29 not updated; plan's doc table required noting store-backed behavior and SC-1..5 test additions. (Single-source: Senior) | Fixed — orchestrator updated §2.29 inline with store-backed redirection note and `playbook_root` field reference. |
| 2 | Low | `(store_dir / "plans").is_dir()` call runs outside the inner `try/except(OSError)`; relies on `is_dir()` swallowing OSError internally. (Single-source: Senior) | Accept-and-note — `is_dir()` returns False on OSError per Python docs; no behavioral risk. |
| 3 | Low | `playbook_root` not in README Configuration sample block (all sibling keys are documented). (Single-source: Senior) | Accept-and-note — plan's doc table did not include README; key is optional/auto-detect; add in a doc-cleanup pass. |
| 4 | Low | Store path from `__store__.md` has no local-boundary containment check; `..` components are resolved by `.resolve()` allowing a `../../other-repo/plans/` redirect. (Single-source: Security Auditor) | Accept-and-note — same-user-authored file; loopback-only endpoint; no privilege boundary crossed. Defense-in-depth boundary check deferred to Follow-up. |
| 5 | Low | Override path asymmetry: workspace absent from WORKSPACE map undiscoverable in override mode but found in non-override mode. (Single-source: Architect) | Accept — deliberate per §3; WORKSPACE is authoritative when override is set. User-facing asymmetry noted. |
| 6 | Low | `_build_store_map` iterates all `workspaces/` subdirs with one `realpath` each under no deadline; dead-share playbook_root subdir stalls once per 30s cache window. (Single-source: Reliability) | Accept-and-note — matches D21 residual; playbook_root is user-configured local path. |
| 7 | Low | Multiple `realpath` calls added per cwd when non-override path used (one in `_get_store_dir`/`_parse_store_dir_from_cwd` + one on `plans_dir`). (Single-source: Reliability) | Accept — each bounded to one stall per cwd per scan by the `_skip_cwd` string pre-check; matched D21 accepted posture. |
| 8 | Low | `_playbook_root_override` read outside `_store_map_lock`; concurrent write from a hypothetical runtime `apply_config` call would be unsafe. (Single-source: Reliability) | Accept — startup-only; self-healing via `cached_root != _playbook_root_override` TTL invalidation. |
| 9 | Low | `_build_store_map` return annotation `dict` vs `dict[str, Path]`. (Corroborated: Phase 2 + Architect) | Accept-and-note — matches existing bare-`dict` convention in module; tighten in a cleanup pass. |
| 10 | Low | README `## Configuration` sample missing `playbook_root` entry. (Same as #3, cross-persona echo) | Accept-and-note — same as #3. |
| 11 | Low | Step 9b: [QA] annotation; Python API surface (scan_plans + config field) verified by 57 passing pytest tests. Browser/dashboard surface requires PA restart (explicit user grant per AGENTS.md; no grant for this task). | SKIP browser surface — deferred to user-initiated restart. Python API: PASS. |
| 12 | Low | gitstate "Uncommitted work" still uses workspace cwd (not store repo). (Single-source: Architect) | Accept — correct boundary (D1); gitstate should show the user's working tree, not the store's. |

Final health: **Green** (0 unresolved High, 0 auto-fixable Medium).

15 findings (2 High, 8 Medium, 5 Low). All auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `_parse_store_dir_from_cwd` calls `.resolve()` before the UNC guard, so a store on a network share stalls inside `_get_store_dir`. (Corroborated: Architect + Reliability) | Fixed — added `_skip_cwd` + `_resolves_to_unc` check on the resolved store path in Phase 2 spec. |
| 2 | High | `_get_store_dir` called without deadline check, adding unbounded I/O to the per-workspace loop. (Single-source: Reliability; verified plausible) | Fixed — added deadline-gated `store_dir = None` initialization before `_get_store_dir` call in `scan_plans` modification. |
| 3 | Medium | Override-path lookup used `_normalize_path(cwd)` but map built with `expanduser()`-normalized keys — silent key miss on `~`-prefixed or tilde-variant paths. (Corroborated: Architect + Senior + Reliability) | Fixed — both build and lookup sides now use `_normalize_path(str(Path(s).expanduser()))`. |
| 4 | Medium | Cache lock scope not specified; plan declared `_store_map_lock` but didn't state TTL-check/build/write all run under it. (Single-source: Reliability; verified plausible) | Fixed — added explicit statement that all three steps run inside `_store_map_lock` in `_build_store_map` description. |
| 5 | Medium | SC-4 referenced non-existent test name `test_git_activity_uses_slug_scoped_commit`; real name is `test_activity_is_the_slug_commit_not_a_later_rewrite`. (Corroborated: Senior + Maintainability) | Fixed — corrected test name reference in Phase 2 test additions. |
| 6 | Medium | Import style diverged from established module convention; plan specified `from .data import _normalize_path` but `overview.py` uses `from . import data; data._normalize_path()`. (Single-source: Maintainability; corroborated by code read) | Fixed — updated all import references in Phase 2 to use established pattern. |
| 7 | Medium | `_fresh` autouse fixture only clears `_plan_memo`; new `_store_map_cache` and `_playbook_root_override` module state would leak between tests. (Single-source: Architect) | Fixed — added fixture reset requirement to Phase 2 test additions. |
| 8 | Medium | `_store_ws` helper omitted git repo setup; SC-4 store-activity test requires a git repo in the store dir. (Single-source: Maintainability) | Fixed — updated `_store_ws` spec to note SC-4 uses `_plan_repo`/`_git`/`_need_git` helpers on the store subdir. |
| 9 | Medium | No test for `apply_config` clearing cache on root change — the 3rd list element existed but was exercised by nothing. (Single-source: Maintainability) | Fixed — added `test_apply_config_clears_cache` to Phase 2 test list. |
| 10 | Medium | No test for git-unavailable fallback in store repo — `_plan_activity` falls back to mtime, but no exit criterion covered it. (Single-source: Reliability) | Fixed — added `test_store_git_fallback_to_mtime_when_no_repo` to Phase 2 test list. |
| 11 | Medium | SC-5 wording said "config repo root inferrable from `__store__.md`" but non-override path does per-cwd direct parse, not root inference — contradicted the design. (Single-source: Architect) | Fixed — rewrote SC-5 to describe the two distinct discovery modes accurately. |
| 12 | Low | `_build_store_map` swallowed OSError silently; store vanishing indistinguishable from "no stores configured." (Single-source: Reliability) | Fixed — added debug log requirement on per-entry and whole-iteration OSError. |
| 13 | Low | Regex anchored to `/qwire-governance`-generated `__store__.md` format but not documented as such. (Corroborated: Senior + Reliability + Maintainability) | Fixed — added explicit note in `_parse_store_dir_from_cwd` spec tying the regex to the generation format. |
| 14 | Low | `git_cwd` local variable undocumented; maintainer must infer its purpose. (Single-source: Maintainability) | Fixed — added inline comment `# plan commits live in the store repo` in sketch. |
| 15 | Low | SC-5 design-decision table missing `expanduser/resolve` symmetry entry after fix. (Induced by fix #3) | Fixed — added normalization decision row to Design Decisions table. |

## Harness Improvement Opportunities

<Reserved — filled during execution>
