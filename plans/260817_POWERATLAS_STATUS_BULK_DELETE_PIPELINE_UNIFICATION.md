# PowerAtlas: Status Fallback Review, Bulk Workspace Delete, Pipeline Unification

> **Date**: 2026-08-17
> **Status**: Exploring
> **Scope**: Three roadmap items — close the None→working status fallback, add workspace-level bulk session delete with optional folder deletion, unify the /search and partials_workspaces render pipelines

---

## Intent

### Problem statement & desired outcomes

Three open roadmap items:

1. **`None` → `"working"` fallback review** — `_session_status` and `_resolved_session_status` fall back to `"working"` when a process is running but `get_semantic_status` returns `None`. The roadmap flagged this as potentially warranting an `"unknown"` state. Exploration concluded the fallback is correct: a mid-write JSONL means the agent is actively working, so `"working"` is the right signal. No code change needed.

2. **Bulk session delete per workspace** — Single-session delete shipped. The use case that actually hurts is deleting all sessions in a workspace (scratch workspaces, benchmark artifacts, stale projects). The POST `/api/acp/sessions/delete` endpoint and `_acp_delete_many` already exist and handle a list of IDs; what is missing is a workspace-level affordance in the `/acp` rail and a server-side path to enumerate all session IDs for a given workspace. Additionally, offer to delete the workspace directory from the filesystem (irreversible; requires typed confirmation).

3. **Unify `/search` and `partials_workspaces` render pipelines** — Both routes implement the same filter-and-render pipeline in parallel. The duplication already shipped one 500 error. Unify behind a shared helper covering both the filter chain and the render loop. Fix the `session_count` divergence (search uses `total_count` regardless of provider filter; partials_workspaces uses a provider-aware sum — the former is incorrect when a provider filter is active).

### Success criteria

**SC1 (Item 1 — closed):** No code change. The `"working"` fallback is confirmed correct behavior and the roadmap item is removed.

**SC2 (Item 2 — bulk delete):**
- POST `/api/acp/sessions/delete` accepts a `cwd` parameter; the server enumerates all session IDs for that workspace from the kiro-cli store directly (not via the paged listing), subject to the existing 200-ID cap.
- The `/acp` rail's workspace-grouping mode shows a delete affordance on each workspace group header (workspace-grouping mode only — not in recency or status grouping modes).
- Clicking the affordance opens a confirmation modal showing: session count, workspace path, a "Delete N sessions" checkbox (checked by default), and a "Delete folder from filesystem" checkbox (unchecked by default).
- The modal requires typing the folder name (basename) to enable the confirm button.
- Folder deletion uses `shutil.rmtree` with explicit path safety validation (absolute path, not a system/home root).
- If the folder no longer exists, skip rmtree silently and report "folder already gone."
- Per-session held/locked failures are reported inline; folder delete is still offered for the remainder.
- The affordance respects `ACP_CAN_DELETE` (mobile UA exclusion).

**SC3 (Item 3 — pipeline unification):**
- `partials_workspaces` and `search` share a unified filter+render helper in `web.py`.
- The helper uses provider-aware `session_count` throughout (fixes search's current `total_count`-always behavior).
- Zero-session pinned folder injection remains in `partials_workspaces`'s pre-processing step only (search must not show ghost workspaces).
- All 4 existing call sites of `workspace_card.html` route through the shared helper.
- Both routes produce identical output for the same input when no query is given (search delegates to partials_workspaces on empty query — this existing delegation is preserved).
- Existing tests pass; new regression tests confirm the `session_count` fix.

### Scope boundaries & non-goals

**In scope:**
- SC1: closing the roadmap item with no code change.
- SC2: workspace-level bulk session delete + optional filesystem folder delete in `/acp` rail (workspace grouping mode only).
- SC3: unified filter+render helper in `web.py`; `session_count` fix.

**Out of scope:**
- Bulk delete affordance in recency or status grouping modes.
- Deleting v3 sessions (`~/.kiro/sessions/<hash>/sess_*/`) — `_acp_session_paths` only covers the v2 flat store; this is a known gap, not introduced here.
- Workspace folder deletion from the dashboard (only from `/acp`).
- Config cleanup on folder delete (removing from `pinned_folders` / `workspace_settings`) — not included unless explicitly requested.
- Any change to the status vocabulary (`"unknown"` state, CSS, templates).

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

**`_session_status` / `_resolved_session_status`** (`web.py:328`, `web.py:414`):
- Fallback `"working"` fires when `get_semantic_status` returns `None` on a live process. Correct by design — mid-write JSONL = agent actively writing = working.
- Valid status vocabulary: `"working"`, `"waiting"`, `"errored"`, `"closed"`, `""` (deferred). Defined in `_STATUS_PRIORITY` (`web.py:402`) and `SemanticStatus` (`status_classifier.py:29`).
- Templates (`session_row.html`, `workspace_card.html`) use strict equality checks — any new status value requires template + CSS changes. No `.status-unknown` rule exists.
- The `/acp` rail maps `"working"` → blue animated dot in JS (`acp.html:~852`). Same closed set.

**Delete subsystem** (`web.py:2354–2570`):
- `_acp_delete_session`: rename-first, unlink-second. `os.replace` refused by `winerror=32` aborts the whole operation atomically. Rollback on partial staging failure.
- `_acp_delete_many`: held (event-loop snapshot before thread hop, D9), locked (`_lock_holder` hint), `_valid_session_id` (path traversal prevention). Cache invalidation after: `session_cache.forget(cwd)` + `data.invalidate_workspace_counts()`.
- Existing test suite `TestAcpDeleteEndpoint` (`test_web.py:15730`) covers all per-session paths including rollback integrity and path traversal.
- `_ACP_MAX_DELETE_IDS = 200` (`web.py:2333`) — existing per-request cap applies.
- `ACP_CAN_DELETE`: mobile UA exclusion, advisory/UX only. Auth boundary is `same_origin_guard`.

**Render pipeline** (`web.py:2970–3430`):
- `partials_workspaces` and `search` are ~160 lines each. Core shared logic: `discover_workspaces_with_counts`, `_group_workspaces`, tag/time/provider/status filter chain, `presence.get_snapshot`, `_workspace_status` per card, `workspace_card.html` render.
- `_group_workspaces` returns `{cwd, folder_name, providers: [{name, display, color, count, updated_at}], total_count, latest_updated}` (`web.py:537`).
- `workspace_card.html` consumes 14 context variables; all 4 call sites pass identical kwarg sets (`web.py:3015, 3048, 3300, 3329`).
- Divergences: (a) `session_count` — partials uses provider-aware sum, search uses `total_count`; (b) zero-session pinned injection — partials only; (c) empty-state wording — intentionally different; (d) status filter early-exit in search only; (e) alphabetical sort on pinned in partials only.
- Known bug: `session_count` divergence — no test covers it.
- AGENTS.md constraint: "A new context variable added to a shared Jinja partial must be added at EVERY route that renders it" (project memory). The shared helper must preserve all 4 call sites.

**Rail grouping modes** (`acp.html`):
- Three modes: workspace (`_acp_listing`), recency/day (`_acp_flat_listing`), status (`_acp_flat_listing`).
- `railGroupNode` / `railHeadNode` / `railGroupAddNode` build the workspace group header. The `+` create button is appended as a sibling to the toggle (not inside it — parser constraint, button-in-button is closed). Delete affordance must follow the same placement.
- `railMenuNode` / `railDeleteSession` (`acp.html:4986–5115`): existing per-session delete UI. Note in code: "A list, because the route takes one — the UI sends a single id and the shape leaves bulk deletion a change to this page rather than to the protocol."

### 5. Risks & mitigations

**R1 — Folder delete path safety (HIGH):** `shutil.rmtree` on an arbitrary user-supplied path could delete system directories if not validated. No existing validation for arbitrary filesystem paths in this codebase. Mitigation: validate that the path is absolute, exists as a directory, is not a root/home/system directory before calling rmtree. The path comes from the kiro-cli store (the cwd field in the session's metadata) — it is user-controlled data but not directly from the HTTP request in the server-side enumeration path.

**R2 — Partial success on workspace delete (MEDIUM):** Some sessions may be held or locked; those fail while others succeed. The modal must clearly communicate partial failure. Mitigation: the existing `{deleted, failed}` response shape already supports per-id outcomes; surface the summary to the user.

**R3 — Render pipeline behavior change on unification (MEDIUM):** Full unification risks silently changing search behavior (especially the `session_count` fix). Mitigation: write regression tests that assert both routes produce the same output for the same filtered input. The `session_count` fix changes the displayed number when provider filter is active — intentional and documented.

**R4 — 200-ID cap for large workspaces (LOW):** A workspace with >200 sessions would require multiple requests. The roadmap mentions one workspace with 208 sessions. Mitigation: the server can loop internally past the cap (multiple `_acp_delete_many` batches in one request handler), or document the cap and let the user re-trigger. Design decision for `/qplan`.

**R5 — `status_classifier._path_cache` stale on delete (LOW):** After deletion, the path cache may serve a cached path that no longer exists. Resolved: the `is_file()` revalidation in `_resolve_jsonl_path` evicts stale positive entries on the next status poll. No action needed.

### 6. Resolved decisions

- Q1: Is `"working"` the right fallback for `None` from `get_semantic_status`? — A: yes, mid-write = working — Decision: no code change; roadmap item closed.
- Q2: Server-side or client-side session enumeration for workspace bulk delete? — A: server-side — Decision: POST endpoint accepts `cwd` parameter; server enumerates IDs from store directly.
- Q3: What does "delete folder" mean? — A: physically delete the directory from the filesystem — Decision: `shutil.rmtree` with type-name confirmation and path safety validation.
- Q4: Confirm dialog sufficient for folder delete? — A: type the folder name — Decision: modal requires typing the folder basename to enable confirm.
- Q5: Extract render loop only or full unification? — A: full unification — Decision: unified filter chain + render helper; fixes `session_count` divergence.
- Q6: Which `session_count` behavior wins in the unified helper? — A: provider-aware — Decision: unified helper uses `sum(p["count"] for p in group["providers"] if p["name"] == provider)` when `provider != "all"`, else `total_count`.

### 7. Open items

- **O1 (execution-contingent):** How to handle workspaces with >200 sessions — loop internally in one request handler vs document the cap and require re-triggering. Decidable during implementation by checking how `_acp_delete_many` is called and whether a loop is clean.
- **O2 (execution-contingent):** Whether to also remove the workspace from `pinned_folders` and `workspace_settings` in config.toml when the folder is physically deleted. Not in scope by default; surface to user at `/qplan` if the interaction design warrants it.
- **O3 (deterministic):** Does `_all_hover_launchers(config)` return `[]` on an empty config, or raise? Confirmable by reading `web.py:144`. Relevant only if the shared helper calls it in a code path not yet covered by tests.

### 8. Recommended approach

**Item 1:** Close with no code change. Remove from roadmap (already done in this session).

**Item 2 (bulk delete):**
- Add a `cwd: str | None = None` parameter to `api_acp_delete_sessions`. When `cwd` is provided, enumerate all session IDs for that workspace from `KIRO_SESSION_DIR` (using the existing `_iter_meta_files` or equivalent), filter to IDs whose stored cwd matches, batch through `_acp_delete_many` (loop past the 200 cap if needed).
- Add a workspace-level delete button to the rail group header in `acp.html`, visible in workspace-grouping mode only, gated by `ACP_CAN_DELETE`.
- Build a confirmation modal: shows session count + path, "Delete N sessions" checkbox (on by default), "Delete folder" checkbox (off by default), type-name input that enables the confirm button.
- Server-side: add a folder delete path in the endpoint — after session deletion, if `delete_folder=true`, validate path (absolute, not root/home, is a directory), call `shutil.rmtree`, return result.
- Python change — requires PowerAtlas restart to take effect.

**Item 3 (pipeline unification):**
- Extract `_render_workspace_groups(pinned_grouped, other_grouped, provider, prov_names, snap, config, request) -> str` from the shared render logic. Both routes call it after their respective filter chains and pre-processing (zero-session injection stays in `partials_workspaces` pre-processing).
- Fix `session_count` in the unified helper to use provider-aware computation throughout.
- Update tests to cover the `session_count` fix (assert provider-filtered count in search results).
- Python change — requires PowerAtlas restart.

**Order:** Item 3 first (lower risk, pure refactor, establishes clean base), then Item 2 (new capability, touches `acp.py`/`acp.html`/`web.py`).

### 9. QA environment

- PowerAtlas running via `.venv-PowerAtlas\Scripts\power-atlas` (or `python -m power_atlas`). Python changes require a restart; `acp.html`/`style.css` changes only need `Ctrl+Shift+R`.
- `/acp` accessible at `http://127.0.0.1:<port>/acp`. Workspace grouping mode: sliders button in the rail → "By workspace."
- Test suite: `.venv-PowerAtlas\Scripts\pytest` (Python), `node tests/acp_page.test.mjs` (JS template tests).
- Delete verification: use a scratch workspace with known sessions. Verify `KIRO_SESSION_DIR` before/after. Folder delete: use `tmp_path` in pytest, not a real workspace.
- For render pipeline: assert both `/partials/workspaces?provider=kiro-cli` and `/search?q=<workspace>&provider=kiro-cli` return the same `session_count` value in the card HTML.

**Assumptions (unconfirmed)**

- `_all_hover_launchers(config)` returns `[]` on an empty config (not confirmed — see O3). Assumed safe default.
- The `cwd` field in session metadata matches the workspace path used as a key by the `/acp` listing. Assumed true given `_stored_session_cwd` reads it and `_acp_delete_many` uses it for cache invalidation.
- `shutil.rmtree` is the right primitive for folder deletion on Windows (vs. `os.remove` / `send2trash`). Assumed — no recycle bin, irreversible, consistent with the confirmed user intent.

## Harness Improvement Opportunities

- Step 1.5 trio dispatched smoothly. No friction observed in the dispatch or return path.
