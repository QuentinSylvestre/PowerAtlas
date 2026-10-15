# PA — Store-Backed Workspace Plans Support

> **Date**: 2026-10-15
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Add PowerAtlas Active Plans support for workspaces that keep plans in the agent-playbook store

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
- SC-5: PA discovers store mappings by scanning `workspaces/*/WORKSPACE` in the
  config repo (authoritative), with the config repo root inferred from any workspace's
  `__store__.md` or set explicitly via a new `playbook_root` config field.
- SC-6: All existing `TestOverviewPlans` tests continue to pass unchanged.

### Scope boundaries & non-goals

**In scope:**

- `overview.py`: store-aware `scan_plans` — reads WORKSPACE map, redirects `plans_dir`
  and `_plan_activity` git cwd when the store has a `plans/` directory.
- `overview.py`: WORKSPACE map builder — infers config repo root from `__store__.md`,
  reads all `workspaces/*/WORKSPACE` files, caches with 30 s TTL.
- `config.py`: new optional `playbook_root: str` field in `Config`; overrides inference.
- `tests/test_web.py`: new tests in `TestOverviewPlans` for store-backed plan discovery,
  fallback, git activity via store repo, WORKSPACE map building.

**Out of scope (v1):**

- UI indicator distinguishing store-kept plans from local plans.
- Supporting workspaces whose store is not under a recognized config repo layout.
- Multiple config repos (first-encountered wins; a conflict is logged and ignored).
- Supporting symlinks inside `plans/` (existing lstat guard unchanged).

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

- `scan_plans(workspaces)` (`overview.py:291`) iterates `(cwd, name)` pairs from
  `_overview_workspaces()` (`web.py:4483`). For each pair it builds
  `plans_dir = Path(cwd) / "plans"` (`overview.py:338`) and scans top-level `*.md`
  there. `plans/done/` is excluded by design.
- `_overview_workspaces()` is shared by three consumers: `scan_plans`,
  `gitstate.request`, `gitstate.overview_payload` (`web.py:3926`, `4283`, `4535`).
  The `cwd` in each pair feeds `gitstate`'s `git -C <cwd>` calls — changing it to
  the store root would show agent-playbook's dirty state under every store-backed
  workspace. The store detection must happen **inside `scan_plans`**, not in
  `_overview_workspaces`.
- `_plan_activity(cwd, path, mtime)` (`overview.py:259`) runs `git -C <cwd> log` to
  find the latest slug-scoped author-date commit. Plan commits for store-backed
  workspaces live in the agent-playbook repo, not the workspace repo; `_plan_activity`
  must receive the store root as its `cwd` argument.
- `plan["cwd"]` is emitted in the JSON response (`overview.py:385`) and consumed by
  the browser at `index.html:1167` (expand-state key) and `:1183` (tooltip). It must
  remain the workspace session path for correct UI behavior.
- `_plan_memo` is keyed by `str(path)` (`overview.py:353`). A path change (store vs.
  workspace) yields a different key — no stale hits. Eviction on a completed scan
  removes memo entries for paths no longer seen (`overview.py:399-402`).
- `config.get_workspace_settings(config, cwd)` (`config.py:620`) already normalizes
  cwd for lookup. No precedent for reading a workspace-level file (e.g. `__store__.md`)
  to augment behavior; this is a new mechanism.
- `config.Config` carries `workspace_settings: dict[str, dict]` with only `tags` and
  `color` per workspace. The new `playbook_root: str` field is additive.
- `AGENTS.md` constraint: Python changes to `overview.py`/`web.py`/`config.py`
  **require a PA restart**. Live QA needs `pa_local` cookie. Public repo —
  `_check_public_ids.py` runs on every commit.
- Overview plans cache reused for 30 s keyed on `(providers, hidden.key)` (`web.py:4531`).
  A 30 s WORKSPACE map TTL matches this cadence.

### 5. Risks & mitigations

- **`__store__.md` format variance**: diskpeek uses `C:\...`, agent-playbook uses
  `~/...`. Must apply `os.path.expanduser()` to extracted paths.
  `_normalize_path`'s `_expand_short_path` is for 8.3 names only, not `~`.
  **Mitigation**: explicit `Path(os.path.expanduser(raw)).resolve()` in the parser.
- **WORKSPACE file format assumption**: verified as single-line workspace path for
  diskpeek. Assumed consistent across all store directories.
  **Mitigation**: strip + non-empty check; log and skip on unexpected content.
- **`git -C <store-subdir>` resolution**: confirmed that git resolves up to the
  agent-playbook repo root from inside a store subdirectory. If a store dir is not
  inside any git repo, `_plan_activity` returns mtime (existing fallback, safe).
- **Plans cache stale on path switch**: the 30 s Overview cache is keyed on filter
  identity, not plans location. A newly wired workspace won't appear for up to 30 s
  after restart. Acceptable.
- **Empty store `plans/`**: if the store's `plans/` exists but is empty (all archived),
  PA shows no plans for that workspace and does not fall back to the workspace's local
  `plans/`. This is intentional (an empty store `plans/` is a deliberate state).

### 6. Resolved decisions

- Q1: Where should the store-detection logic live? — A: Inside `scan_plans` (not in
  `_overview_workspaces`). — Decision: store detection in `overview.py:scan_plans`;
  no signature change; gitstate and `_overview_workspaces` unchanged.
- Q2: Fallback when store has no `plans/`? — A: Fall back to workspace's `plans/`.
  — Decision: redirect to store only when `<store>/plans/` exists as a directory;
  otherwise use existing `Path(cwd) / "plans"` path.
- Q3 + Q4: Discovery mechanism for store path. — A: A+B; B in scope. — Decision:
  parse `__store__.md` to infer config repo root (walk up 2 levels from store dir);
  scan `workspaces/*/WORKSPACE` for the authoritative map; add `playbook_root` config
  field as an explicit override. Map cached 30 s in `overview.py`.

### 7. Open items

- Whether `os.path.expanduser()` on Windows correctly expands `~/...` paths when
  the home directory is not `C:\Users\<name>` (e.g., domain accounts with redirected
  home). Should be tested with `Path.home()` as a fallback. Execution-contingent.
- Whether `_check_public_ids.py` treats `WORKSPACE` file contents (workspace paths)
  as session ids. Decidable by probe: run the checker against a staged diff that
  adds a WORKSPACE path read. If it blocks, the test fixture must use synthetic paths.

### 8. Recommended approach

**Phase 1 — Store map builder in `overview.py`**

New module-level cache `_store_map: dict` and `_store_map_lock`. Function
`_get_store_dir(cwd: str, config) -> Path | None`:

1. Read `config.playbook_root` first; if set, use it.
2. Else parse `<cwd>/.kiro/steering/__store__.md`: extract backtick-fenced path after
   "The store directory is:", apply `os.path.expanduser()` + `Path.resolve()`, walk up
   two levels to get the config repo root.
3. Scan `<repo_root>/workspaces/*/WORKSPACE` — each file's content is the workspace
   absolute path; build `{_normalize_path(ws_path): store_dir}`.
4. Cache the map with a 30 s TTL.
5. Return `Path(<store_dir>)` for the given normalized `cwd`, or `None` if not found.

Error handling: any I/O failure at any step returns `None` (falls through to existing behavior).

**Phase 2 — Store-aware `scan_plans`**

At the top of `scan_plans`, call `_get_store_dir(cwd, config?)` for each workspace.

Wait — `scan_plans` currently does not receive `config`. Two options:
a. Pass `config` into `scan_plans` (small signature change).
b. Store the playbook root in a module-level variable set at PA startup, alongside
   the existing `_plan_memo`.

Option (b) avoids touching the `scan_plans` signature tested by `test_web.py:37890`.
`config.playbook_root` is read once at startup and stored as `_playbook_root_override`.

When `_get_store_dir` returns a store path whose `plans/` dir exists: use
`store_dir / "plans"` as `plans_dir`. Pass `str(store_dir)` as `cwd` to
`_plan_activity`. Keep `plan["cwd"]` = original workspace `cwd`.

When it returns `None` or the store has no `plans/`: existing behavior unchanged.

**Phase 3 — Config and tests**

- `config.py`: add `playbook_root: str = ""` to `Config`; sanitize in `load_config`.
- `tests/test_web.py`: new fixtures creating a temp store structure; tests for
  SC-1 through SC-5.

### 9. QA environment

- Python changes require PA restart.
- Live QA: `pa_local` cookie (see AGENTS.md Verification Setup).
- Verify SC-1 by navigating to the PA dashboard Overview after restart and confirming
  the diskpeek plan appears under "diskpeek" workspace.
- Unit tests: `.venv-PowerAtlas/Scripts/python -m pytest tests/test_web.py -k "plan" -x`
- Pre-commit: `.venv-PowerAtlas/Scripts/python _check_public_ids.py` and
  `.venv-PowerAtlas/Scripts/python _check_test_names.py`.
