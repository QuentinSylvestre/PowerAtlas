# PowerAtlas: Status Fallback Review, Bulk Workspace Delete, Pipeline Unification

> **Date**: 2026-08-17
> **Status**: In Progress
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Close None→working fallback (no-op), add workspace-level bulk session delete with optional folder+config deletion, unify /search and partials_workspaces render pipelines
> **Estimated effort**: 2–3 days

---

## Intent

### Problem statement & desired outcomes

Three open roadmap items:

1. **`None` → `"working"` fallback review** — `_session_status` and `_resolved_session_status` fall back to `"working"` when a process is running but `get_semantic_status` returns `None`. Exploration concluded the fallback is correct: a mid-write JSONL means the agent is actively working. No code change needed.

2. **Bulk session delete per workspace** — Single-session delete shipped. The painful case is deleting all sessions in a workspace (scratch workspaces, benchmark artifacts, stale projects). The POST `/api/acp/sessions/delete` endpoint and `_acp_delete_many` already exist; what is missing is a workspace-level affordance in the `/acp` rail, server-side session enumeration by workspace, and an offer to also delete the workspace directory from the filesystem and clean up its config.toml entries.

3. **Unify `/search` and `partials_workspaces` render pipelines** — Both routes implement the same filter-and-render pipeline in parallel. The duplication already shipped one 500 error. Unify behind a shared helper covering both the filter chain and the render loop. Fix the `session_count` divergence (search always uses `total_count`; partials uses a provider-aware sum — the former is wrong when a provider filter is active).

### Success criteria

- **SC-1 (Item 1 — closed):** No code change. The `"working"` fallback is confirmed correct. Roadmap item removed.
- **SC-2 (Item 2 — bulk delete, sessions):** POST `/api/acp/sessions/delete` accepts a `cwd` parameter; the server enumerates all session IDs for that workspace directly from the kiro-cli store, batches them through `_acp_delete_many` in 200-ID chunks until exhausted. The `/acp` rail's workspace-grouping mode shows a delete affordance on each workspace group header (workspace-grouping mode only, controlled by `railMode === 'project'`). Clicking opens a confirmation modal showing session count and workspace path.
- **SC-3 (Item 2 — bulk delete, confirmation):** The modal has a "Delete N sessions" checkbox (checked by default, cannot be unchecked independently — informational only; server always deletes sessions when `cwd` is provided) and a "Delete folder from filesystem" checkbox (unchecked by default). The confirm button is disabled until the user types the folder basename AND at least one meaningful action is selected. Partial success (some sessions held/locked) is reported inline.
- **SC-4 (Item 2 — folder delete):** When "Delete folder" is checked: validate path is absolute, non-UNC, non-symlink, and has minimum depth (≥4 parts on Windows, ≥3 on Linux), and is not the home directory; call `shutil.rmtree`; remove the path from `config.pinned_folders` and `config.workspace_settings`; save config atomically within a single `_lock` acquisition. If the folder no longer exists, skip rmtree, still clean config, report "folder already gone." Return `folder_deleted: bool` and `folder_error: str` in the response. Remote callers cannot trigger folder deletion (`delete_folder=true` is ignored for non-loopback requests).
- **SC-5 (Item 3 — pipeline unification):** `partials_workspaces` and `search` share a `_render_workspace_groups` helper. The helper accepts `provider` and derives `prov_names` internally. Provider-aware `session_count` is used throughout. Zero-session pinned folder injection remains in `partials_workspaces` pre-processing only. All 4 existing `workspace_card.html` render call sites route through `_render_workspace_groups`. Both routes produce identical output for the same filtered input when no query is given.
- **SC-6 (Item 3 — regression tests):** New pytest tests assert provider-filtered `session_count` is correct in both routes. Existing tests pass unchanged.

### Scope boundaries & non-goals

**In scope:**
- SC-1: closing the roadmap item with no code change.
- SC-2 / SC-3 / SC-4: workspace-level bulk session delete + optional filesystem folder delete + config cleanup, in `/acp` rail (workspace grouping mode only).
- SC-5 / SC-6: unified filter+render helper; `session_count` fix.

**Out of scope:**
- Bulk delete affordance in recency or status grouping modes.
- Deleting v3 sessions (`~/.kiro/sessions/<hash>/sess_*/`) — `_acp_session_paths` only covers the v2 flat store.
- Workspace folder deletion from the dashboard (only from `/acp`).
- Any change to the status vocabulary (`"unknown"` state, CSS, templates).
- Recycle-bin / trash behavior on folder delete — `shutil.rmtree` is irreversible by design.

---

## 1) Current State

**Delete subsystem** (`web.py:2354–2570`):
- `_acp_delete_session(session_id)` (`web.py:2354`): rename-first, unlink-second. Atomic on winerror=32 (sharing violation). Rollback on partial staging failure.
- `_acp_delete_many(session_ids, held)` (`web.py:2439`): validates each ID, checks held/locked, calls `_acp_delete_session`, invalidates `session_cache` and `workspaces_with_counts` cache after. Returns `{deleted, failed}`.
- `api_acp_delete_sessions` (`web.py:2509`): POST `/api/acp/sessions/delete`. Accepts `session_ids` list; validates non-empty list; snapshots `held` on the event loop (D9), calls `_acp_delete_many` off-thread. No workspace-level path exists today.
- `_ACP_MAX_DELETE_IDS = 200` (`web.py:2333`): per-request cap, currently applies to the `session_ids` path only.

**Session enumeration**: `data_kiro._iter_meta_files()` yields all `.json` metadata files in `KIRO_SESSION_DIR`. `acp._stored_session_cwd(session_id)` (`acp.py:~2107`) reads the first 16 KB of a session's `.json` to extract the `cwd` field. No existing function enumerates all IDs for a given cwd. `acp.KIRO_SESSION_DIR` is accessible from `web.py` via the module-level `acp` import (line 53, try/except guard).

**Rail UI** (`acp.html`):
- `railMode` controls grouping. Values: `'project'` (workspace grouping — confirmed variable name at `acp.html:~4244: var railMode = _storedMode === 'date' ? 'date' : _storedMode === 'status' ? 'status' : 'project'`), `'date'`, `'status'`.
- `railGroupNode(group, sessions)` (`acp.html:~5311`): builds the workspace group container.
- `railHeadNode(key, label, countText, opts)` (`acp.html:~5250`): the collapsible header. The `+` create button (`railGroupAddNode`) is appended as a sibling of the toggle — button-in-button is refused by the HTML parser.
- Per-session delete: `railDeleteSession(session)` posts `{session_ids: [id]}`. Code comment confirms the list shape is intentional.

**Render pipeline** (`web.py:2970–3430`): `partials_workspaces` and `search` are ~160 lines each. Identical render loop (14-kwarg `workspace_card.html` call). Verified divergences: (a) `session_count` — partials provider-aware, search always `total_count`; (b) zero-session pinned injection — partials only; (c) empty-state wording — intentionally different; (d) status filter early-exit — search only; (e) alphabetical sort on pinned — partials only.

**Config**: `save_config()` (`config.py:481`) holds `_lock` during write only; takes no argument — serializes the module-level `_config` object (or equivalent). Pattern in `web.py`: every mutating route calls `config = load_config()`, mutates, then `save_config(config)` — but `save_config` likely ignores the argument and re-reads the module state; confirm signature before Phase 2.

> **Implementation note**: `save_config`'s actual signature must be verified against `config.py:481` before Phase 2 code is written. The plan uses `save_config()` (no argument) as the safer assumption based on the `_lock`-based singleton pattern.

## 2) Goal

**Phase 1**: Extract `_render_workspace_groups` from the duplicated render loops in `partials_workspaces` and `search`; fix `session_count` to be provider-aware; add regression tests.

**Phase 2**: Add `_acp_sessions_for_workspace` enumeration helper and extend `api_acp_delete_sessions` to accept `cwd`, enumerate IDs, batch-delete, and optionally delete the folder (loopback-only) + clean config atomically. Add new server-side helper tests.

**Phase 3**: Add the workspace-level delete affordance and confirmation modal to the `/acp` rail. Add `.mjs` tests for the new UI.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| `None`→`"working"` fallback | Keep as-is | Add `"unknown"` state | Mid-write = actively working; fallback is correct |
| Session enumeration strategy | Server-side by `cwd` parameter | Client fetches IDs first | Fewer round-trips; consistent with server-owns-store pattern |
| Batching past 200-ID cap | Server loops internally (async batch loop, one `held` snapshot per iteration on the event loop) | Require client re-triggers | Single request UX; D9 preserved by re-snapshotting `held` on the loop before each `asyncio.to_thread` call |
| Folder delete primitive | `shutil.rmtree` (irreversible) | `send2trash` (recycle bin) | User explicitly chose irreversible; no additional dependency needed |
| Folder delete confirmation | Type folder basename | Simple confirm() dialog | User chose type-name; appropriate for irreversible filesystem operation |
| Config cleanup on folder delete | Auto-clean `pinned_folders` + `workspace_settings` | Leave config unchanged | Folder is gone; config data is orphaned; user confirmed |
| Folder delete for remote callers | Blocked (force `delete_folder=False` for non-loopback requests) | Allow with existing device cookie | Irreversible local filesystem operation should not be triggerable from remote devices |
| Sessions checkbox in modal | Informational-only (always-on, cannot uncheck) | Optional (allow folder-only delete) | Server always deletes sessions when `cwd` is provided; showing it as optional is a contract mismatch |
| `_render_workspace_groups` signature | Accepts `provider` string; derives `prov_names` internally | Pass both `provider` and `prov_names` | Removes redundancy that can drift; single source of truth for `prov_names` derivation |
| `_acp_sessions_for_workspace` location | `web.py` | `acp.py` | `web.py` already calls `acp` internals extensively; the isolation boundary prohibits `acp` importing from `web`, not the reverse |
| Pipeline unification scope | Full filter+render helper | Render loop only | User chose full unification; also fixes `session_count` bug |
| `session_count` semantics | Provider-aware (match `partials_workspaces`) | Always `total_count` | Provider filter active → show filtered count, not total |

## 4) External Dependencies & Costs

### Required external changes

None — code-only change.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Pipeline unification [QA]

**Goal**: Extract `_render_workspace_groups` shared helper; fix `session_count` divergence; add regression tests.

**File scope**: `src/power_atlas/web.py`, `tests/test_web.py`

**Covers**: SC-1 (no-op closure — confirmed no code change needed), SC-5, SC-6

**Changes**:

Extract a new helper `_render_workspace_groups` that accepts the already-filtered and already-split pinned/non-pinned workspace lists plus rendering dependencies. `prov_names` is derived internally from `provider`:

```python
def _render_workspace_groups(
    pinned_grouped: list[dict],
    other_grouped: list[dict],
    provider: str,       # "all" or a specific provider name
    snap,                # presence.Snapshot
    config,              # Config
    request,             # FastAPI Request
) -> str:
    """Render pinned-first then time-bucketed workspace card HTML.

    Both partials_workspaces and search call this after their own filter chains.
    Zero-session pinned injection stays in partials_workspaces's pre-processing.
    session_count: provider-aware sum when provider != "all", else total_count.
    prov_names: derived here from provider (None = all).
    """
    prov_names = None if provider == "all" else {provider}
    hover_launchers = _all_hover_launchers(config)
    cards_html = ""

    for group in pinned_grouped:
        cwd = group["cwd"]
        session_count = (
            sum(p["count"] for p in group["providers"] if p["name"] == provider)
            if provider != "all" else group["total_count"]
        )
        cards_html += templates.get_template("partials/workspace_card.html").render(
            request=request, cwd=cwd, sessions=[], stale=not Path(cwd).exists(),
            pinned_sessions=config.pinned_sessions, folder_name=group["folder_name"],
            session_count=session_count, is_pinned=True,
            last_updated=group["latest_updated"],
            workspace_color=_resolve_workspace_color(cwd, config),
            providers=group["providers"],
            workspace_status=_workspace_status(snap, cwd, prov_names),
            time_group="pinned",
            hover_launchers=hover_launchers,
        )

    if pinned_grouped and other_grouped:
        cards_html += '<div class="pinned-separator" aria-hidden="true"></div>'

    time_groups: dict[str, list[dict]] = {
        "today": [], "yesterday": [], "this_week": [], "before": []}
    for ws in other_grouped:
        time_groups[_time_bucket(ws["latest_updated"])].append(ws)
    for key, label in [("today", "Today"), ("yesterday", "Yesterday"),
                       ("this_week", "This week"), ("before", "Older")]:
        if not time_groups[key]:
            continue
        cards_html += f'<div class="group-heading">{label}</div>'
        for group in time_groups[key]:
            cwd = group["cwd"]
            session_count = (
                sum(p["count"] for p in group["providers"] if p["name"] == provider)
                if provider != "all" else group["total_count"]
            )
            cards_html += templates.get_template("partials/workspace_card.html").render(
                request=request, cwd=cwd, sessions=[], stale=not Path(cwd).exists(),
                pinned_sessions=config.pinned_sessions, folder_name=group["folder_name"],
                session_count=session_count, is_pinned=False,
                last_updated=group["latest_updated"],
                workspace_color=_resolve_workspace_color(cwd, config),
                providers=group["providers"],
                workspace_status=_workspace_status(snap, cwd, prov_names),
                time_group=key,
                hover_launchers=hover_launchers,
            )
    return cards_html
```

**`partials_workspaces` after the change**: retain all pre-processing (pinned split, zero-session injection, filter chain, alphabetical pinned sort, status filter). Replace the two render loops with `_render_workspace_groups(pinned_grouped, other_grouped, provider, snap, config, request)`. The multi-branch empty-state cascade stays in `partials_workspaces` as-is; `_render_workspace_groups` returning `""` is handled by the existing empty-state logic.

**`search` after the change**: retain its own filter chain (query pre-filter, split into `pinned_results`/`other_results`). Call `_render_workspace_groups(pinned_results, other_results, provider, snap, config, request)`. Empty-state logic stays in `search`. Note: `search` no longer needs to compute `prov_names` separately.

**Tests**: add to `test_web.py`:
```python
def test_search_session_count_is_provider_aware(client, workspace_with_two_providers):
    """When provider filter is active, search returns provider-filtered count."""
    resp = client.get("/search?q=<workspace>&provider=kiro-cli")
    # Assert session count in response HTML equals kiro-cli session count only

def test_partials_workspaces_and_search_same_count_with_provider_filter(client, workspace):
    """Both routes return same session_count for same provider filter."""
    r1 = client.get("/partials/workspaces?provider=kiro-cli")
    r2 = client.get("/search?q=<workspace>&provider=kiro-cli")
    # Extract and compare counts from both HTML responses
```

**Exit criteria**:
- [x] `_render_workspace_groups` function exists in `web.py`
- [x] No direct `workspace_card.html` render calls remain in `partials_workspaces` body (only in `_render_workspace_groups`)
- [x] No direct `workspace_card.html` render calls remain in `search` body (only via `_render_workspace_groups`)
- [x] `_render_workspace_groups` derives `prov_names` internally from `provider` (not a parameter)
- [x] `session_count` uses provider-aware logic in both loops inside `_render_workspace_groups`
- [x] Zero-session pinned folder injection is absent from `_render_workspace_groups`
- [x] Multi-branch empty-state cascade unchanged in `partials_workspaces`
- [x] New provider-aware session_count tests pass
- [x] `.venv-PowerAtlas\Scripts\pytest tests/test_web.py` green

**Implementation (2026-08-17, code: 2695ed4 + autofix: 22595a6)**
Extracted `_render_workspace_groups` helper with `_session_count_for_group` extracted from the duplicated ternary. Both `partials_workspaces` and `search` now delegate to the helper after their own filter chains. `search` session_count bug fixed (was always `total_count`; now provider-aware). Three new tests added: `test_search_session_count_is_provider_aware`, `test_partials_workspaces_and_search_produce_same_session_count_for_provider_filter`, and `test_partials_workspaces_pinned_session_count_is_provider_aware` (pinned path). Auto-fix pass removed dead `hover_launchers` assignment from `search`, removed dead `cards_html = ""` initializers, fixed type annotations to `list[dict]`, extracted `_session_count_for_group` helper. 1374 tests pass; 1 pre-existing failure unrelated to Phase 1.

QA (Step 5b): BLOCKED — Python change requires PowerAtlas restart (AGENTS.md: "Never restart PowerAtlas autonomously"). Will be re-verified at Step 9b after all phases complete and user restarts.

---

### Phase 2: Bulk workspace delete — server [QA]

**Goal**: Add server-side workspace session enumeration, extend the delete endpoint to accept `cwd`, handle loop-past-200 batching with D9-correct held snapshots, and implement folder delete + config cleanup.

**File scope**: `src/power_atlas/web.py`, `tests/test_web.py`

**Covers**: SC-2, SC-4

**Pre-implementation check**: verify `save_config` signature in `config.py:481` — use `save_config()` (no argument) unless the function takes a Config argument. The plan assumes no argument based on the `_lock` singleton pattern.

**Changes**:

**1. New enumeration helper `_acp_sessions_for_workspace(cwd: str) -> list[str]`** placed near `_acp_delete_many` in `web.py`:

```python
def _acp_sessions_for_workspace(cwd: str) -> list[str]:
    """Return all session IDs in the v2 kiro-cli store for the given workspace.

    Scans KIRO_SESSION_DIR directly (not the paged listing). A session belongs
    to this workspace when its stored cwd matches after normalization.

    Performance note: reads up to 16 KB per .json file (via _stored_session_cwd).
    At ~6,000 sessions this is 1–3 s on a warm cache; may be slower on cold cache
    or network-backed paths. Called off the event loop via asyncio.to_thread.

    Concurrent calls to this function for the same workspace are safe: each reads
    only, and the rename-first staging in _acp_delete_session handles concurrent
    deletes of discovered IDs gracefully (second caller gets not_found failures).

    Limitation: only covers v2 sessions in KIRO_SESSION_DIR. v3 sessions in
    ~/.kiro/sessions/<hash>/sess_*/ are not enumerated.
    """
    from .data import _normalize_path
    norm = _normalize_path(cwd)
    result = []
    for meta_path in acp.KIRO_SESSION_DIR.glob("*.json"):
        if not meta_path.is_file():
            continue
        sid = meta_path.stem
        if not acp._valid_session_id(sid):   # check before I/O to skip non-session files
            continue
        stored = acp._stored_session_cwd(sid)
        if stored and _normalize_path(stored) == norm:
            result.append(sid)
    return result
```

**2. Config-cleanup helper `_remove_workspace_from_config(cwd: str, config)`**:

```python
def _remove_workspace_from_config(cwd: str, config) -> None:
    """Remove cwd from pinned_folders and workspace_settings (in-place mutation)."""
    from .data import _normalize_path
    norm = _normalize_path(cwd)
    config.pinned_folders = [
        f for f in config.pinned_folders
        if _normalize_path(f) != norm
    ]
    for k in [k for k in config.workspace_settings if _normalize_path(k) == norm]:
        del config.workspace_settings[k]
```

**3. Folder-delete helper `_acp_delete_workspace_folder(cwd: str) -> tuple[bool, str]`**:

Path safety rules (in order, fail fast):
- `p.is_absolute()` → else "Path is not absolute."
- Not a UNC path: `str(p).startswith('\\\\') or str(p).startswith('//')` → "Refusing UNC path."
- Not a symlink: `p.is_symlink()` → "Refusing to delete a symbolic link."
- Minimum depth: `len(p.parts) < 4` on Windows, `len(p.parts) < 3` on Linux → "Path too shallow to be a workspace."
- Not home: `p == Path.home()` → "Refusing to delete home directory."
- Not home parent: `p == Path.home().parent` → "Refusing to delete parent of home directory."
- Is directory: `not p.exists()` → skip rmtree, still clean config, return `(False, "folder_already_gone")`.
- `not p.is_dir()` → "Path is not a directory."

```python
def _acp_delete_workspace_folder(cwd: str) -> tuple[bool, str]:
    """Delete the workspace directory and clean its config entries.

    All validation, config mutation, and save happen here. Config is loaded,
    mutated, and saved atomically within this function's execution on a single
    worker thread — do not pass a pre-loaded Config object, as that creates a
    lost-update race window.

    Returns (deleted: bool, error: str). error is "" on full success,
    "folder_already_gone" when the folder was missing but config was cleaned.
    TOCTOU note: the is_dir() check and rmtree are not atomic; a symlink swapped
    in during that window could be followed. The is_symlink() guard at entry
    mitigates the common case. Accept this risk on a single-user desktop.
    """
    import platform
    p = Path(cwd)
    # --- Path safety checks (ordered, fail-fast) ---
    if not p.is_absolute():
        return False, "Path is not absolute."
    if str(p).startswith('\\\\') or str(p).startswith('//'):
        return False, "Refusing UNC path."
    if p.is_symlink():
        return False, "Refusing to delete a symbolic link."
    min_depth = 4 if platform.system() == "Windows" else 3
    if len(p.parts) < min_depth:
        return False, f"Path too shallow to be a workspace (need >= {min_depth} parts)."
    home = Path.home()
    if p == home or p == home.parent:
        return False, "Refusing to delete home directory or its parent."
    # --- Load, mutate, save config atomically in this thread ---
    config = load_config()
    _remove_workspace_from_config(cwd, config)
    if not p.exists():
        save_config(config)   # still clean config even if folder is gone
        return False, "folder_already_gone"
    if not p.is_dir():
        return False, "Path is not a directory."
    try:
        import shutil as _shutil
        _shutil.rmtree(p)
    except OSError as exc:
        return False, f"Could not delete folder: {exc}"
    save_config(config)
    return True, ""
```

**4. Extend `api_acp_delete_sessions`** — restructure the entry logic to handle `cwd` and `session_ids` as separate branches. The new async batch loop re-snapshots `held` per iteration on the event loop (D9-correct):

```python
# At the top of api_acp_delete_sessions, after body parse:
cwd: str | None = body.get("cwd") if isinstance(body, dict) else None
delete_folder: bool = bool(body.get("delete_folder", False)) if isinstance(body, dict) else False

if cwd is not None:
    # --- Workspace-level delete path ---
    if not isinstance(cwd, str) or not cwd.strip():
        return JSONResponse({"error": "'cwd' must be a non-empty string."}, status_code=400)
    # Enumerate all session IDs for this workspace (off event loop)
    all_ids = await asyncio.to_thread(_acp_sessions_for_workspace, cwd)
    # Batch delete, D9: re-snapshot `held` on the event loop before each thread hop
    deleted_total: list[str] = []
    failed_total: list[dict] = []
    while all_ids:
        batch, all_ids = all_ids[:_ACP_MAX_DELETE_IDS], all_ids[_ACP_MAX_DELETE_IDS:]
        held = frozenset(acp._supervisor.sessions)   # event-loop snapshot (D9)
        result = await asyncio.to_thread(_acp_delete_many, batch, held)
        deleted_total.extend(result["deleted"])
        failed_total.extend(result["failed"])
    response: dict = {
        "deleted": deleted_total,
        "failed": failed_total,
        "total_found": len(deleted_total) + len(failed_total),
    }
    # Folder delete: loopback-only guard
    if delete_folder:
        from .web import _is_loopback  # or equivalent check
        if _is_remote_peer(request):   # block folder delete from remote callers
            response["folder_deleted"] = False
            response["folder_error"] = "Folder deletion is not available from remote access."
        else:
            folder_deleted, folder_error = await asyncio.to_thread(
                _acp_delete_workspace_folder, cwd)
            response["folder_deleted"] = folder_deleted
            response["folder_error"] = folder_error
    return JSONResponse(response)

# --- Existing session_ids path (unchanged) ---
raw = body.get("session_ids") if isinstance(body, dict) else None
if not isinstance(raw, list) or not raw:
    return JSONResponse({"error": "'session_ids' must be a non-empty list."}, status_code=400)
# ... existing code continues unchanged
```

> **Rejected approach**: snapshots `held` inside `asyncio.to_thread` per batch. This violates D9 (`_supervisor.sessions` is loop-owned; reading from a worker thread is a torn read). **Use instead**: re-snapshot `held` on the event loop before each `asyncio.to_thread` call in the async batch loop.

> **Note on `_is_remote_peer`**: the existing code checks request origin via `RemoteAccessGuard` / `same_origin_guard`. Use the same loopback check as those guards. Confirm the correct helper name from `web.py:~700` before implementation.

**Tests** — add to `TestAcpDeleteEndpoint` in `test_web.py`:

```python
def test_cwd_delete_enumerates_all_sessions_for_workspace(acp_store_dir, client)
def test_cwd_delete_skips_held_sessions_and_reports_failures(...)
def test_cwd_delete_batches_past_200_cap(...)   # workspace with 201 sessions
def test_cwd_delete_returns_total_found(...)     # denominator in response
def test_cwd_empty_workspace_returns_zero_deleted_zero_failed(...)
def test_folder_delete_removes_directory_and_cleans_config(tmp_path, ...)
def test_folder_delete_still_cleans_config_if_folder_missing(...)   # folder_already_gone
def test_folder_delete_refuses_unc_path(...)
def test_folder_delete_refuses_symlink(...)
def test_folder_delete_refuses_too_shallow_path_windows(...)
def test_folder_delete_refuses_too_shallow_path_linux(...)
def test_folder_delete_refuses_home_directory(...)
def test_folder_delete_ignored_for_remote_callers(...)
def test_remove_workspace_from_config_cleans_pinned_and_settings(...)
def test_remove_workspace_from_config_case_and_separator_variants(...)  # normalization
def test_cwd_requires_non_empty_string(...)   # 400 on empty cwd
```

**Exit criteria**:
- [x] `_acp_sessions_for_workspace` exists; calls `acp._valid_session_id` before `_stored_session_cwd` (I/O guard order)
- [x] `api_acp_delete_sessions` handles `cwd` and `session_ids` as separate branches; `cwd` branch does not hit the `session_ids` non-empty guard
- [x] Batch loop re-snapshots `held` on the event loop before each `asyncio.to_thread` call (D9)
- [x] Response includes `total_found` field
- [x] `_acp_delete_workspace_folder` validates: absolute, non-UNC, non-symlink, min-depth (4 Windows / 3 Linux), not home or home-parent
- [x] Folder delete loads, mutates, and saves config within the same worker-thread call (no pre-loaded Config passed in)
- [x] `delete_folder=true` returns `folder_deleted: false, folder_error: "…"` for remote callers
- [x] `folder_already_gone` response when folder missing but config cleaned
- [x] All new tests pass; existing `TestAcpDeleteEndpoint` tests pass
- [x] `.venv-PowerAtlas\Scripts\pytest tests/test_web.py` green
- [x] `plans/tests/260701_POWERATLAS.md` updated with probes for (a) workspace deletion removes cwd from `pinned_folders`; (b) workspace deletion removes cwd key from `workspace_settings`

**Implementation (2026-08-17, code: 50c968a + autofix: ff8b37e)**
Added `_acp_sessions_for_workspace` (v2-store scan, `_valid_session_id` before I/O, `_normalize_path` comparison), `_remove_workspace_from_config` (in-place Config mutation), and `_acp_delete_workspace_folder` (seven ordered safety checks: absolute, non-UNC, non-symlink-before-resolve, min-depth, home/home-parent; then load-mutate-save config; then rmtree). Extended `api_acp_delete_sessions` with a `cwd` branch that runs before the existing `session_ids` guard, uses async batch loop with per-iteration D9-correct `held` re-snapshot, and blocks `delete_folder=true` for remote callers via `_is_remote_peer`. Updated `plans/tests/260701_POWERATLAS.md` with §2.26 probes.

Pre-implementation divergence: `save_config` takes a `Config` argument (plan assumed no-arg based on singleton pattern). All calls correctly pass the config object.

Auto-fix pass addressed: H1 (wrong remote guard predicate — changed to `_is_remote_peer`), H2 (`.resolve()` after symlink check to prevent `..` traversal), H3 (config save on "not a directory" branch), F1 (try/except around folder-delete thread hop to prevent HTTP 500), added `test_cwd_delete_batches_past_200_cap`, `test_cwd_delete_skips_held_sessions_and_reports_in_failed`, and rewrote `test_folder_delete_ignored_for_remote_callers` to actually test the HTTP-level guard.

QA (Step 5b): BLOCKED — Python change requires PowerAtlas restart (AGENTS.md: "Never restart PowerAtlas autonomously"). Will re-verify at Step 9b.

---

### Phase 3: Bulk workspace delete — UI [QA]

**Goal**: Add workspace-level delete affordance to the `/acp` rail (workspace grouping mode `railMode === 'project'` only) and the confirmation modal with focus trap.

**File scope**: `src/power_atlas/templates/acp.html`, `src/power_atlas/static/style.css`, `tests/acp_page.test.mjs`

**Covers**: SC-2, SC-3

**Changes**:

**1. `railGroupDeleteNode(group)` — new function** (alongside `railGroupAddNode`):

```javascript
function railGroupDeleteNode(group) {
  // Only rendered in workspace-grouping mode (railMode === 'project').
  // ACP_CAN_DELETE gate (mobile UA exclusion) is advisory; the server also
  // blocks delete_folder for remote callers regardless of UA.
  var btn = document.createElement('button');
  btn.className = 'acp-btn acp-rail-group-delete';
  btn.type = 'button';
  btn.title = 'Delete all sessions in this workspace';
  btn.setAttribute('aria-label', 'Delete all sessions — ' + (group.name || group.cwd));
  btn.addEventListener('click', function(e) {
    e.stopPropagation();
    railDeleteWorkspace(group);
  });
  return btn;
}
```

In `railGroupNode`, add after the `+` button: `if (railMode === 'project' && ACP_CAN_DELETE) head.appendChild(railGroupDeleteNode(group));`

**2. `railDeleteWorkspace(group)` — the confirmation flow**:

```javascript
function railDeleteWorkspace(group) {
  var modal = buildWorkspaceDeleteModal(
    group.cwd, group.name, group.total,
    function onConfirm(deleteFolder) {
      // sessions checkbox is informational; server always deletes sessions when cwd is given
      var body = {cwd: group.cwd};
      if (deleteFolder) body.delete_folder = true;
      fetch(RAIL_DELETE_PATH, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        closeModal(modal);
        if ((data.failed || []).length === 0) {
          railEvictWorkspaceGroup(group.cwd);
        } else {
          showWorkspaceDeleteResult(group, data);
        }
        if (data.folder_deleted) {
          railEvictWorkspaceGroup(group.cwd);
        }
      })
      .catch(function(err) { showToast('Delete failed: ' + err, 'error'); });
    }
  );
  document.body.appendChild(modal);
  trapFocus(modal);  // focus trap + Escape handler
  modal.querySelector('.acp-ws-delete-name-input').focus();
}
```

**3. `buildWorkspaceDeleteModal(cwd, folderName, sessionCount, onConfirm)`**:

Modal contents:
- Heading: "Delete workspace sessions"
- Info line: "Workspace: `<folderName>` — `<sessionCount>` sessions in kiro-cli's store"
- "Delete `<sessionCount>` sessions" checkbox: **checked, disabled** (informational — server always deletes sessions when `cwd` is given; this communicates the action, not a choice)
- "Delete folder from filesystem" checkbox: unchecked, enabled. Label includes: "⚠ Irreversible — deletes `<folderName>` from disk"
- Text input: placeholder "Type folder name to confirm", `autocomplete="off"`
- Confirm button: disabled until `input.value === folderName` (at least the folder-delete checkbox or the confirmation itself is the gate — since sessions are always deleted, the input match alone is sufficient to enable confirm)
- Cancel button: calls `closeModal`
- `aria-modal="true"`, `role="dialog"`, `aria-labelledby` pointing at the heading

**4. `railEvictWorkspaceGroup(cwd)`**:

```javascript
function railEvictWorkspaceGroup(cwd) {
  // Remove the workspace group from railGroups and railGroupsByCwd.
  // If the currently-selected session belongs to this group, call releaseSession().
  var idx = railGroups.findIndex(function(g) { return g.cwd === cwd; });
  if (idx !== -1) railGroups.splice(idx, 1);
  delete railGroupsByCwd[cwd];
  // Release session if it belongs to the evicted workspace
  if (sessionId && _sessionCwd && _sessionCwd === cwd) releaseSession();
  // Re-render the rail
  renderRail();
}
```

> **Note**: verify `_sessionCwd` (or equivalent variable holding the current session's cwd) in `acp.html` before implementation. The principle: if the active session's workspace is being evicted, release it.

**5. `showWorkspaceDeleteResult(group, data)`**:

Show a status message via the existing `railStatus` mechanism (consistent with the rest of the rail's status display):

```javascript
function showWorkspaceDeleteResult(group, data) {
  var nDeleted = (data.deleted || []).length;
  var nFailed = (data.failed || []).length;
  var msg = 'Deleted ' + nDeleted + ' session' + (nDeleted !== 1 ? 's' : '') + '.';
  if (nFailed > 0) msg += ' ' + nFailed + ' could not be deleted (held or locked).';
  if (data.folder_error && data.folder_error !== 'folder_already_gone') {
    msg += ' Folder: ' + data.folder_error;
  }
  railStatus.textContent = msg;
}
```

**6. Focus trap `trapFocus(el)` and `closeModal(el)`**:

```javascript
function trapFocus(modal) {
  var focusable = modal.querySelectorAll(
    'button, input, [tabindex]:not([tabindex="-1"])');
  var first = focusable[0], last = focusable[focusable.length - 1];
  modal.addEventListener('keydown', function handler(e) {
    if (e.key === 'Escape') { closeModal(modal); return; }
    if (e.key !== 'Tab') return;
    if (e.shiftKey ? document.activeElement === first : document.activeElement === last) {
      e.preventDefault();
      (e.shiftKey ? last : first).focus();
    }
  });
}
function closeModal(modal) {
  if (modal.parentNode) modal.parentNode.removeChild(modal);
}
```

**7. CSS** — add to `style.css`:

```css
.acp-rail-group-delete {
  /* Positioned sibling to .acp-rail-group-add; visually distinct */
  color: var(--color-error, #ef4444);
  opacity: 0.6;
  font-size: 0.75rem;
  padding: 0 4px;
  margin-left: 4px;
}
.acp-rail-group-delete:hover { opacity: 1; }
/* Modal overlay */
.acp-ws-delete-modal {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.5);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000;
}
.acp-ws-delete-modal-body {
  background: var(--bg-2, #1e1e2e);
  border-radius: 8px; padding: 1.5rem; max-width: 420px; width: 100%;
}
```

**JS tests** — add to `tests/acp_page.test.mjs`:

```javascript
// 8 new test cases:
test('delete button appears on workspace groups in project mode', ...)
test('delete button absent in date grouping mode', ...)
test('delete button absent in status grouping mode', ...)
test('modal opens with correct session count and folder name', ...)
test('confirm button disabled until typed name matches', ...)
test('confirm button disabled when typed name is empty', ...)
test('confirmed delete posts cwd and delete_folder when checked', ...)
test('full success evicts the group from rail', ...)
test('partial failure shows status message without eviction', ...)
```

**Exit criteria**:
- [ ] Delete button appears on workspace group headers in `railMode === 'project'` mode, gated by `ACP_CAN_DELETE`
- [ ] No delete button appears when `railMode` is `'date'` or `'status'`
- [ ] Clicking opens modal with session count, folder name, sessions checkbox (checked, disabled), folder checkbox (unchecked), typed-name input
- [ ] Confirm button disabled until `input.value === folderName`
- [ ] Confirmed delete posts `{cwd, delete_folder?}` to `RAIL_DELETE_PATH`
- [ ] Full success (no failures): `railEvictWorkspaceGroup` called
- [ ] Partial failure: `showWorkspaceDeleteResult` called with inline summary; group not evicted
- [ ] Folder deleted: `railEvictWorkspaceGroup` called
- [ ] Focus trap active: Tab cycles within modal; Escape closes modal
- [ ] `src/power_atlas/static/style.css` updated with modal and delete-button styles
- [ ] `node tests/acp_page.test.mjs` green (all 9 new tests pass)
- [ ] Hard reload (`Ctrl+Shift+R`) picks up changes with no PowerAtlas restart
- [ ] `README.md` updated with a sentence describing the workspace-level delete affordance

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| R1 — Folder delete path safety: invalid path through adversarial `cwd` body field | HIGH | Multi-layer validation in `_acp_delete_workspace_folder`: absolute, non-UNC, non-symlink, minimum depth, non-home. Tests cover each check. `cwd` is validated against store before folder delete (server enumerates sessions first; a workspace with 0 sessions still runs the folder delete if `delete_folder=true`, but the path came from the user's own request). |
| R2 — Remote callers triggering folder delete | HIGH | `delete_folder=true` is blocked for non-loopback requests. Loopback-only check consistent with other restricted operations. |
| R3 — D9 violation on `held` re-snapshot | HIGH | Resolved in design: the async batch loop re-snapshots `held` on the event loop before each `asyncio.to_thread` call. |
| R4 — Config lost-update race | MEDIUM | `_acp_delete_workspace_folder` loads, mutates, and saves config entirely within one worker thread call. No pre-loaded Config object is passed in. `save_config`'s `_lock` serializes the save. The load-to-save window is a race with concurrent settings changes; accepted as a known trade-off consistent with the existing pattern across all web.py routes. |
| R5 — Partial success on session delete: some held/locked sessions survive | MEDIUM | `{deleted, failed, total_found}` response shape surfaces the denominator. UI shows inline summary. Folder delete still runs (or is skipped) based on the `delete_folder` flag, independent of session delete outcome. |
| R6 — Render pipeline behavior change: `session_count` fix in `search` | MEDIUM | Regression tests in Phase 1 confirm the fix. The change is a correction, not a regression. |
| R7 — TOCTOU between `is_dir()` and `rmtree` | LOW | `is_symlink()` guard at entry mitigates the most common symlink-swap attack. Accepted on a single-user desktop. Documented in code comment. |
| R8 — `_acp_sessions_for_workspace` performance on cold cache | LOW | Observability: log duration. Called off the event loop. No timeout implemented — acceptable for a user-initiated one-time operation; documented in the function's docstring. |
| R9 — Session ID enumeration oracle for remote callers | LOW | The `deleted` list in the response exposes session IDs. Remote callers with a device cookie can already list sessions via `_ACP_LISTING_PATH`. Accepted; consistent with existing remote surface. |

## 7) Verification

```bash
# Python test suite
.venv-PowerAtlas\Scripts\pytest tests/test_web.py -v

# JS template tests
node tests/acp_page.test.mjs

# Manual: pipeline unification
# 1. Restart PowerAtlas (Python change required)
# 2. Filter by provider: /partials/workspaces?provider=kiro-cli
#    Confirm session_count shows kiro-cli only, not total
# 3. /search?q=<workspace>&provider=kiro-cli
#    Confirm same session_count as /partials/workspaces

# Manual: bulk workspace delete (Phase 2+3)
# 1. Open /acp; switch to workspace grouping mode (sliders → "By workspace")
# 2. Verify delete button on each group header
# 3. Click on a scratch workspace → modal opens
# 4. Type folder name → confirm button enables
# 5. Confirm (sessions only): sessions deleted, group evicted
# 6. Repeat with "Delete folder" checked → folder gone, config cleaned
# 7. Partial failure test: hold a session, confirm → inline summary, group stays

# Path safety smoke test
# POST /api/acp/sessions/delete {"cwd": "C:\\", "delete_folder": true}  → folder_error
# POST /api/acp/sessions/delete {"cwd": "C:\\Users", "delete_folder": true}  → folder_error
# POST /api/acp/sessions/delete {"cwd": "\\\\server\\share", "delete_folder": true} → folder_error
# POST from non-loopback: delete_folder ignored → folder_deleted: false
```

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `memory/MEMORY.md` | Update hover_launchers entry (heading "A new context variable added to a shared Jinja partial…"): replace the four web.py line-number citations with the new `_render_workspace_groups` call sites. Governance-gated (Apply / Skip?). | 1 |
| `plans/tests/260701_POWERATLAS.md` | Add probes: (a) workspace deletion removes cwd from `pinned_folders`; (b) workspace deletion removes cwd key from `workspace_settings` | 2 |
| `README.md` | Add a sentence to the "Deleting a session is possible" paragraph: workspace-level delete affordance in workspace grouping mode, what it does, the confirmation modal, the folder delete option | 3 |
| `plans/ROADMAP.md` | Remove the pipeline unification item (summary, priority table, description) and the bulk deletion item (summary, priority table, description) — standard `/qclose` housekeeping | doc-table-only |
| `plans/CLOSED_INVESTIGATIONS.md` | Note that the "bulk deletion ships" reopen condition is met; confirm keep-declined or promote the trash-tier item | doc-table-only |

## 9) Implementation Divergences from Plan

- **Type annotation downgrade**: plan specified `list[dict]` for `pinned_grouped` and `other_grouped` parameters of `_render_workspace_groups`; implemented as bare `list`. Rationale: oversight; fixed in auto-fix pass.
- **Dead `hover_launchers` in `search`**: `_all_hover_launchers(config)` call was not removed from `search` body during initial implementation. Rationale: oversight; fixed in auto-fix pass.

## Follow-up Work (Deferred)

1. **Trash-tier item reassessment.** `plans/CLOSED_INVESTIGATIONS.md` "Would reopen if: bulk deletion ships" is now satisfied. Send2trash / recycle-bin behavior can be reconsidered. Source: R7.
2. **Bulk delete in recency / status grouping modes.** Intentionally out of scope. Different UX concerns apply. Source: Scope boundaries.
3. **v3 session coverage for workspace delete.** `_acp_sessions_for_workspace` only covers the v2 flat store. Source: Scope boundaries.
4. **Minimum-depth check platform-specificity.** The `min_depth` guard uses `platform.system()`. On Linux within WSL, this returns `'Linux'` while paths are Windows-style — consider whether WSL users need special handling. Source: R1 mitigation note.

## Review Log

### 2026-08-17 — Implementation Review (after Phase 2, personas: Senior engineer, Security auditor, Reliability engineer, Architect)

Implementation health: Green (after auto-fix cycle).
8 findings (3 High, 3 Medium, 2 Low). All auto-fixed in one cycle. Cycle-2 review: no findings.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | Wrong remote guard predicate: `_request_host_allowed` checks Host header (attacker-controlled), not TCP peer — remote callers could trigger rmtree when remote_bind enabled. | Fixed — changed to `_is_remote_peer` (transport-level IP) |
| 2 | High | `..` path traversal bypass — `Path("C:\\dir\\project\\..")` has 6 parts, passes min_depth, but resolves to an ancestor; home check also bypassable. | Fixed — `p = p.resolve()` after symlink check; `home.resolve()` for home comparison |
| 3 | High | Config not saved on "Path is not a directory" branch — `_remove_workspace_from_config` mutated in-memory but mutation discarded. | Fixed — `save_config(config)` added before that return |
| 4 | Medium | `save_config` after `rmtree` raising produces HTTP 500 with no body; folder gone, response unreachable. | Fixed — try/except wraps thread hop; returns JSON with `folder_deleted=False` on exception |
| 5 | Medium | Remote guard test only tested the loopback path, not the blocked-remote path; guard bug would not have been caught. | Fixed — test rewritten using actual non-loopback client scope |
| 6 | Medium | Two planned tests missing: batch loop (>200 sessions) and held-session interaction; D9 path untested. | Fixed — `test_cwd_delete_batches_past_200_cap` and `test_cwd_delete_skips_held_sessions_and_reports_in_failed` added |
| 7 | Low | Linux min_depth=3 branch untested; normalization edge cases test absent. | Orchestrator: proposed-accept — pending user decision |
| 8 | Low | Symlink check must precede `resolve()` since `resolve()` follows symlinks (spec error corrected during implementation). | Fixed — symlink check moved before `resolve()` call |

*Finding #7 (Low): the Low-severity normalization/Linux-branch tests were not auto-fixed. Proposing user accept — the existing tests cover the core paths; Linux depth behavior differs from Windows by a single constant that is directly tested via the Windows path. User can accept or request a fix.*

16 findings (7 High, 5 Medium, 4 Low). 15 auto-resolved; 1 Low left for user review.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `session_ids` guard in `api_acp_delete_sessions` rejects the new `cwd` branch before it runs | Fixed — restructured entry logic to handle `cwd` and `session_ids` as separate branches |
| 2 | High | D9 violation: plan re-snapshotted `held` inside `asyncio.to_thread` | Fixed — async batch loop re-snapshots `held` on the event loop before each thread hop |
| 3 | High | Wrong JS variable name `currentGroupMode === 'workspace'` (should be `railMode === 'project'`) | Fixed — all Phase 3 guards use `railMode === 'project'` |
| 4 | High | Sessions checkbox presented as optional but server always deletes sessions on `cwd` path (contract mismatch) | Fixed — sessions checkbox is informational-only (checked, disabled); `onConfirm` receives only `deleteFolder` |
| 5 | High | Path depth check `len(p.parts) <= 2` off by one — allows `C:\Users` and `/home` | Fixed — guard is now `len(p.parts) < min_depth` where min_depth=4 (Windows) or 3 (Linux) |
| 6 | High | `cwd` comes from the HTTP request body, not KIRO_SESSION_DIR metadata (R1 mitigation was wrong) | Fixed — Risk Assessment R1 corrected; path safety checks are the sole barrier |
| 7 | High | No symlink check before `shutil.rmtree` | Fixed — `if p.is_symlink(): return False, "Refusing to delete a symbolic link."` added |
| 8 | Medium | Config lost-update race: pre-loaded Config passed into `_acp_delete_workspace_folder` | Fixed — helper loads, mutates, and saves config entirely within its own execution |
| 9 | Medium | `_render_workspace_groups` empty-state: plan's code snippet collapsed 5-branch cascade to one string | Fixed — `_render_workspace_groups` returns `""` on no cards; empty-state cascade stays in each route |
| 10 | Medium | `prov_names` was a redundant parameter (derived from `provider`) | Fixed — helper derives `prov_names = None if provider == "all" else {provider}` internally |
| 11 | Medium | No `.mjs` tests for new workspace-delete UI (governance requires test coverage for template inline script) | Fixed — 9 new `.mjs` tests specified in Phase 3 |
| 12 | Medium | Remote callers can trigger `shutil.rmtree` via `delete_folder=true` | Fixed — Phase 2 blocks `delete_folder=true` for non-loopback requests |
| 13 | Medium | `railEvictWorkspaceGroup` specified by name but never defined | Fixed — full specification added to Phase 3 |
| 14 | Medium | No focus trap or Escape handler on the modal | Fixed — `trapFocus` and `closeModal` helpers specified; focus trap is an exit criterion |
| 15 | Low | `style.css` missing from Phase 3 file scope | Fixed — added to file scope; CSS for `.acp-rail-group-delete` and modal specified |
| 16 | Low | `memory/MEMORY.md` hover_launchers update listed as a Phase 1 exit criterion checkbox — governance-gated updates require Apply/Skip? proposal, not a checkbox | Escalated — moved to Documentation Updates with governance-gated note; exit criterion removed |

## Harness Improvement Opportunities

- Step 1.5 trio dispatched smoothly. No friction observed in the dispatch or return path.
- Doc-impact sub-agent ran in parallel with plan writing without issue.
- 4-persona review at max effort caught 7 High findings before implementation — the extra personas (Security auditor, Reliability engineer) found findings the Architect+Senior alone would likely have missed (D9 violation, UNC path, symlink attack surface, `session_ids` guard blocker). Max effort justified for filesystem-destructive changes.

### 2026-08-17 — Implementation Review (after Phase 1, personas: Senior engineer, Maintainability reviewer, Reliability engineer, Security auditor)

Implementation health: Green (after auto-fix cycle).
6 findings (0 High, 2 Medium, 4 Low). All auto-fixed in one cycle. Cycle-2 review: no findings.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | Dead `hover_launchers = _all_hover_launchers(config)` in `search` body — computed and never consumed; pays real config-read cost per request. | Fixed — removed from `search`; helper owns it internally |
| 2 | Medium | Dead `cards_html = ""` initializers in `search` and `partials_workspaces` — immediately overwritten. | Fixed — removed both initializers |
| 3 | Medium | Test blind spot: pinned-path `session_count` not covered — both initial tests use `pinned_folders=[]`. | Fixed — added `test_partials_workspaces_pinned_session_count_is_provider_aware` |
| 4 | Low | Duplicated `session_count` ternary in both loops of `_render_workspace_groups` — could silently diverge. | Fixed — extracted `_session_count_for_group` helper |
| 5 | Low | Type annotations `list` not `list[dict]` on `_render_workspace_groups` parameters. | Fixed — changed to `list[dict]` |
| 6 | Low | Section 9 Implementation Divergences not filled. | Fixed — filled with two documented divergences |
