# Remove kiro-cli v2 from PowerAtlas

> **Date**: 2026-09-17
> **Status**: In Progress
> **Scope**: Delete the kiro-cli v2 provider (launcher, session history, ACP v2 paths, presence lock scan, status classifier v2 branch) and all associated code, tests, and docs.
> **Estimated effort**: 1–2 days

---

## Intent

### Problem statement & desired outcomes

PowerAtlas currently registers kiro-cli v2 (`"kiro-cli"` provider, bare UUID session IDs,
`~/.kiro/sessions/cli/` flat store) alongside kiro-cli v3 (`"kiro-cli-v3"`, `sess_`-prefix,
hashed workspace dirs). kiro-cli v2 is no longer used: the ACP path already runs v3-only,
and the dashboard v2 launcher is superseded. The v2 data layer, launcher entries, ACP v2
code paths, and all related test and doc surface area should be removed entirely.

### Success criteria

- SC-1: `src/power_atlas/data_kiro.py` is deleted; `data.PROVIDERS` no longer contains `"kiro-cli"`.
- SC-2: The dashboard shows no v2 sessions and no "kiro-cli" (v2) provider tile; `GET /api/available-providers` does not include `"kiro-cli"`.
- SC-3: The v2 ACP paths (`_lock_holder`, `KIRO_SESSION_DIR`, `_stored_session_cwd`, `_acp_session_paths`, v2 delete path) are removed from `acp.py` and `web.py`.
- SC-4: `classify_kiro_v2`, the `provider == "kiro-cli"` branch of `_resolve_jsonl_path_uncached`, and the v2 `SESSION_DIR` constant are removed from `status_classifier.py`.
- SC-5: The kiro `.lock`-file sidecar scan (`_KIRO_LOCK_DIR`, `_kiro_session_cwd`, the `.lock` branch in `_sidecar_records`) is removed from `presence.py`; `_KIRO_PROVIDERS` becomes `frozenset({"kiro-cli-v3"})`.
- SC-6: All `"kiro-cli"` entries are removed from `config.py` (migration deleted), `launcher.py` (provider dicts + `_build_provider_args` v2 branch), and `web.py` (provider display dicts).
- SC-7: A `config.toml` containing `[provider_settings.kiro-cli]` has that key dropped on the next PowerAtlas save.
- SC-8: v2-exercising tests deleted from `test_data.py`; v2-specific tests deleted from `test_launcher.py` and `test_web.py`; kiro lock-related presence tests removed.
- SC-9: The full test suite (`pytest tests/`) passes with no regressions after all phases.
- SC-10: `README.md` no longer contains `[provider_settings.kiro-cli]` config sample or "scanned separately from v2" language.
- SC-11: `docs/KNOWLEDGE.md` updated: v2 diff-backfill references removed; a brief v2 store format note added to preserve the architecture history.

### Scope boundaries & non-goals

**In scope:**
- Delete `data_kiro.py` and its SQLite-based v2 workspace discovery.
- Remove `"kiro-cli"` from `data.PROVIDERS` and all provider dicts/maps in `web.py`, `launcher.py`, `config.py`.
- Remove v2-specific functions from `acp.py`: `_lock_holder`, `KIRO_SESSION_DIR`, `_stored_session_cwd`, `_load_session_cwd` (v2 path), the dead `data_kiro` import and its docstring references.
- Remove v2-specific code from `web.py`: `_ACP_SESSION_SUFFIXES`, `_ACP_DELETE_STAGING`, `_ACP_SHARING_VIOLATION`, `_ACP_LISTING_PROVIDER`, `_acp_session_paths`, the v2 branch of `_acp_delete_session`, `_acp_sessions_for_workspace` (deleted; call site inlined with `data_kiro_v3.load_sessions(cwd)`), `touched_v2` in `api_acp_delete_sessions`, provider fallback sets updated.
- Remove v2 branch from `status_classifier.py`: `SESSION_DIR` constant, `classify_kiro_v2()`, v2 branch in `_resolve_jsonl_path_uncached`, v2 path in `_classify_from_path`.
- Remove kiro lock scan from `presence.py`: `_KIRO_LOCK_DIR`, `_kiro_session_cwd`, `.lock` branch in `_sidecar_records`; update `_KIRO_PROVIDERS`.
- Update `config.py`: remove `trust_all_tools` → `"kiro-cli"` migration; add `"kiro-cli"` to provider_settings drop-on-save (strip it in `load_config` sanitize step).
- Update `launcher.py`: remove `"kiro-cli"` from all provider dicts; remove `else: # kiro-cli` branch from `_build_provider_args`; update default `provider` to `"kiro-cli-v3"` in `launch_session` and `launch_batch`.
- Update `data.py`: remove `data_kiro` import, `SESSION_DIR` re-export, `discover_workspaces()` legacy wrapper; update `SessionCache` method defaults from `"kiro-cli"` to `"kiro-cli-v3"`; update `get_sessions`/`get_session_tail`/`get_first_prompt`/`get_full_transcript` defaults.
- Update `web.py` hardcoded fallback provider sets `{"kiro-cli", "claude-code", "kiro-cli-v3"}` → `{"claude-code", "kiro-cli-v3"}` (two occurrences near `_workspace_status`); update `api_session_transcript` default `provider`.
- Delete tests and update test infrastructure per plan phases.
- Update documentation per doc-impact scan.

**Not in scope:**
- kiro-cli v3 (`"kiro-cli-v3"`) — fully intact.
- Claude Code, Kiro IDE providers — untouched.
- ACP supervisor, session protocol, prompt handling — untouched (except removing the dead `data_kiro` import from `acp.py`).
- `~/.kiro/sessions/cli/` on disk — PowerAtlas stops reading it; the directory is not deleted.

---

## 1) Current State

- **`data_kiro.py`** (`src/power_atlas/data_kiro.py`): the v2 session adapter; `SESSION_DIR = Path.home() / ".kiro" / "sessions" / "cli"`. Public interface: `is_available`, `discover_workspaces`, `load_sessions`, `refresh_stale_entries_for_cwd`, `find_session_workspace`, `get_session_tail`, `get_tool_diffs`, `get_first_prompt`, `get_full_transcript`. All have equivalents in `data_kiro_v3.py` except `get_tool_diffs`, which is covered internally in `acp.py` by `_get_tool_diffs_v3`.
- **`data.py`** (`data.py:128-138`): imports `data_kiro` and registers it as `PROVIDERS["kiro-cli"]`. `SessionCache` method defaults use `provider="kiro-cli"`. `SESSION_DIR` re-exported at `data.py:242`. Legacy `discover_workspaces()` wrapper at `data.py:244`.
- **`acp.py`** (`acp.py:82`): `from . import data_kiro` — import is dead at runtime (three docstring occurrences only, confirmed by probe). `KIRO_SESSION_DIR` at `acp.py:833`, `_lock_holder` at `~acp.py:2196`, `_stored_session_cwd` at `~acp.py:2248`, `_load_session_cwd` at `~acp.py:2271`. All v2-only; v3 uses `_lock_holder_v3` and `_stored_session_cwd_v3`.
- **`web.py`** (`web.py:85-110`): `PROVIDER_COLORS/DISPLAY_NAMES/BADGES/_PROVIDER_BINARY_DISPLAY` each have `"kiro-cli"` entries. `_ACP_LISTING_PROVIDER = "kiro-cli"` at `web.py:1800` — used only in one cache-invalidation call at `web.py:3013`. `_ACP_SESSION_SUFFIXES` at `web.py:2767`. `_acp_session_paths` at `web.py:2787`. `_acp_delete_session` dispatches on `sess_` prefix at `web.py:2808`; the non-`sess_` branch is the v2 path. `_acp_sessions_for_workspace` at `web.py:3023`; its only call site at `web.py:3173` passes `include_v3=True`. `touched_v2` set at `web.py:2959`. Fallback provider sets at `web.py:464,481`.
- **`status_classifier.py`** (`status_classifier.py:26`): `SESSION_DIR` constant. `classify_kiro_v2` at `~status_classifier.py:216`. `_resolve_jsonl_path_uncached` v2 branch at `~status_classifier.py:100`. `_classify_from_path` v2 dispatch at `~status_classifier.py:574`.
- **`presence.py`** (`presence.py:240`): `_KIRO_LOCK_DIR = Path.home() / ".kiro" / "sessions" / "cli"`. `_kiro_session_cwd` at `presence.py:413`, called at `presence.py:790`. `_sidecar_records` at `~presence.py:358` scans `_KIRO_LOCK_DIR` for `.lock` files. `_KIRO_PROVIDERS = frozenset({"kiro-cli", "kiro-cli-v3"})` at `presence.py:89`.
- **`config.py`** (`config.py:405`): `trust_all_tools` migration creates `provider_settings["kiro-cli"]`. `_LEGACY_KEYS` at `config.py:42` includes `"trust_all_tools"`.
- **`launcher.py`** (`launcher.py:81-101`): `_PROVIDER_DISPLAY/BINARY/TERMINAL` each have `"kiro-cli"` entries. `_build_provider_args` v2 `else` branch at `~launcher.py:113`. `launch_session` default `provider="kiro-cli"` at `~launcher.py:145`. `launch_batch` default at `~launcher.py:253`.

## 2) Goal

Remove the `"kiro-cli"` v2 provider entirely from all registration points, code paths, tests, and documentation, leaving `"kiro-cli-v3"` as the sole kiro-cli provider and the test suite green.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Historical v2 sessions | Complete removal — v2 sessions vanish from dashboard | Keep as read-only | User is sole user; v2 is fully superseded; complexity of read-only path outweighs benefit |
| `provider_settings.kiro-cli` in existing config | Drop on next save — strip in `load_config` sanitize step | Active migration on startup; leave as unknown key | Silent cleanup on next save; no startup side-effects; user is sole user |
| `_acp_sessions_for_workspace` | Delete function; inline `data_kiro_v3.load_sessions(cwd)` at the one call site | Simplify body only | Only one call site; function's entire body was the v2 scan loop; v3 call is one line |
| Presence kiro `.lock` scan | Remove entirely | Keep as forward-compat guard | v3 never writes `.lock` to `sessions/cli/` (measured 2026-09-16, build 2.21.4); the scan returns nothing in practice |
| `test_data.py` v2 tests | Delete | Redirect to v3 | `test_data_kiro_v3.py` already has full v3 coverage; no gap to fill |
| `SessionCache` / `data.py` default provider | Change to `"kiro-cli-v3"` | No default (force explicit) | All callers that relied on the default were targeting v2; changing to v3 is semantically correct and requires no caller changes since callers use provider-aware paths |
| v2 architecture notes in `docs/KNOWLEDGE.md` | Add brief section preserving v2 store format history; remove stale references | Remove all v2 content | User explicitly requested preservation |

## 4) External Dependencies & Costs

### Required external changes

None. This is a purely local code removal with no infra, CI/CD, IAM, cloud, or data-migration implications.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Delete data_kiro.py and update data.py [QA]

**Goal**: Remove the v2 data adapter and clean up the provider registry and data.py defaults.

**File scope**: `src/power_atlas/data_kiro.py` (deleted), `src/power_atlas/data.py`

**Covers**: SC-1, SC-2 (provider registry removal), SC-9

**Changes**:

1. **Delete** `src/power_atlas/data_kiro.py`.

2. **`data.py`** — remove the `data_kiro` import and all its dependencies:

   - Remove `from . import data_kiro` from the import line (`data.py:128`). The import line becomes:
     ```python
     from . import data_claude, data_kiro_ide, data_kiro_v3  # noqa: E402
     ```
   - Remove `"kiro-cli": data_kiro,` from `PROVIDERS` (`data.py:134`).
   - Remove `SESSION_DIR = data_kiro.SESSION_DIR` re-export (`data.py:242`).
   - Remove the `discover_workspaces()` legacy wrapper function (`data.py:244-252`).
   - Change all `SessionCache` method default arguments from `provider: str = "kiro-cli"` to `provider: str = "kiro-cli-v3"` — affects `get`, `put`, `get_file_stats`, `forget` methods, and the comment in `invalidate_workspace_counts` that mentions `provider="kiro-cli"`.
   - Change `get_sessions`, `get_session_tail`, `get_first_prompt`, `get_full_transcript` function defaults from `provider: str = "kiro-cli"` to `provider: str = "kiro-cli-v3"`.
   - Update the `invalidate_workspace_counts` docstring that says "a deletion changes the count of exactly one workspace" — remove the sentence mentioning `provider="kiro-cli"` as an example cache key.

**Exit criteria**:
- [x] `src/power_atlas/data_kiro.py` is absent from the working tree (`git status` shows it deleted).
- [x] `grep -r "data_kiro" src/power_atlas/data.py` returns no hits.
- [x] `grep '"kiro-cli"' src/power_atlas/data.py` returns no hits.
- [x] `python -c "from power_atlas import data; print(list(data.PROVIDERS.keys()))"` returns `['claude-code', 'kiro-ide', 'kiro-cli-v3']`.

Implementation (2026-09-17, code: 3e15b29)
Deleted `data_kiro.py`; updated `data.py` to remove the `data_kiro` import, `"kiro-cli"` PROVIDERS entry, `SESSION_DIR` re-export, `discover_workspaces()` wrapper, and all method defaults from `"kiro-cli"` to `"kiro-cli-v3"`. The `from . import data_kiro` removal in `acp.py` was pulled forward from Phase 5 because `data_kiro.py` deletion made `acp.py` fail to import at runtime. Fixup commit 9ed9b80 removed two collection-blocking `data_kiro` imports from `test_data.py`. Known forward-work test failures: `test_get_provider_settings` (4 tests — Phase 8 cleanup), `test_close_session_releases_the_diff_backfill` (Phase 8), `test_trust_all_tools_migration` (Phase 2/8). Non-v2 suite: 1816 passed, 2 skipped.

---

### Phase 2: Update config.py [QA]

**Goal**: Remove the `trust_all_tools` migration and add `"kiro-cli"` to the drop-on-save sanitize step.

**File scope**: `src/power_atlas/config.py`

**Covers**: SC-6 (config.py), SC-7

**Changes**:

1. Remove the `trust_all_tools` migration block (`config.py:405-411`):
   ```python
   # Migration: trust_all_tools=true → provider_settings["kiro-cli"].default_args = "-a"
   if data.get("trust_all_tools") is True and "kiro-cli" not in config.provider_settings:
       config.provider_settings["kiro-cli"] = {
           "default_args": "-a",
           "color": "",
           "enabled": True,
       }
   ```
   Delete this entire block.

2. In the `provider_settings` sanitize step (`config.py:416`), add a line to drop the `"kiro-cli"` key if present:
   ```python
   # Drop the retired v2 provider key if still present in config.toml
   config.provider_settings.pop("kiro-cli", None)
   ```
   Insert this line immediately before the existing sanitize line:
   ```python
   config.provider_settings = {k: v for k, v in config.provider_settings.items() if isinstance(v, dict)}
   ```

3. Keep `"trust_all_tools"` in `_LEGACY_KEYS` — it is still needed to strip that key from any legacy `config.toml` that has it. Only the migration block (step 1 above) is removed. The set remains:
   ```python
   _LEGACY_KEYS = frozenset({"trust_all_tools", "terminal_command"})
   ```
   > **Rejected:** removing `"trust_all_tools"` from `_LEGACY_KEYS` — causes the key to survive in config.toml indefinitely for legacy configs instead of being stripped on save. **Use instead:** keep it in `_LEGACY_KEYS`; only remove the migration block.

**Exit criteria**:
- [x] `grep "trust_all_tools" src/power_atlas/config.py` returns only the `_LEGACY_KEYS` line (migration block removed).
- [x] `grep '"kiro-cli"' src/power_atlas/config.py` returns only the `pop` line (no `provider_settings["kiro-cli"] = ...` block).
- [x] Starting PowerAtlas with a `config.toml` containing `[provider_settings.kiro-cli]` and saving config (e.g. toggling any setting) removes that key from the saved file. (Covered by `test_kiro_cli_v2_key_dropped_on_load`.)

Implementation (2026-09-17, code: 78645b4)
Removed the `trust_all_tools` → `provider_settings["kiro-cli"]` migration block from `load_config` and added `config.provider_settings.pop("kiro-cli", None)` before the type-sanitize step. `"trust_all_tools"` remains in `_LEGACY_KEYS` so legacy configs still have that key stripped on save. `test_config.py` updated in the same commit: deleted three migration-block tests, fixed `test_provider_settings_round_trip` to use `"kiro-cli-v3"`, added `test_kiro_cli_v2_key_dropped_on_load`. Fixup commit 2dd0f41 updated the stale `test_save_config_drops_trust_all_tools` fixture to use `"kiro-cli-v3"`. All 57 config tests pass.

---

### Phase 3: Update presence.py [QA]

**Goal**: Remove the kiro `.lock`-file sidecar scan; update `_KIRO_PROVIDERS` to v3 only.

**File scope**: `src/power_atlas/presence.py`

**Covers**: SC-5

**Changes**:

1. Remove `_KIRO_LOCK_DIR = Path.home() / ".kiro" / "sessions" / "cli"` (`presence.py:240`).

2. Remove the `_kiro_session_cwd` function (`presence.py:413-415`).

3. In `_sidecar_records()`, remove the entire kiro `.lock` file enumeration block. The block begins with `_list_sidecars(_KIRO_LOCK_DIR, ".lock")` (near `presence.py:358`) and processes records up to the sidecar append. After removal, `_sidecar_records()` only processes the Claude Code `.json` sidecar path (and any other non-kiro paths that existed).

4. In the `_scan()` function, remove the `_kiro_session_cwd(sid)` call at `presence.py:790`. The primary cwd source for kiro sessions is `proc.cwd()` from the first-pass process-table scan (`presence.py:650`); `_kiro_session_cwd` was a fallback for sidecars without a matching process-table entry. After removal, kiro sessions still populate cwd from the first pass.

5. Update `_KIRO_PROVIDERS` (`presence.py:89`):
   ```python
   _KIRO_PROVIDERS: frozenset[str] = frozenset({"kiro-cli-v3"})
   ```

6. Remove `"kiro-cli"` from `_PROVIDER_SPECS` (`presence.py:72`). After removal:
   ```python
   _PROVIDER_SPECS: dict[str, tuple[tuple[str, ...], str]] = {
       "claude-code": (("claude", "claude.exe", "claude.cmd"), "--resume"),
       "kiro-cli-v3": (("kiro-cli", "kiro-cli.exe", "kiro-cli.cmd"), "--resume-id"),
   }
   ```
   Also remove the `_scan()` first-pass reroute block that converts `"kiro-cli"` → `"kiro-cli-v3"` when `sid.startswith("sess_")` (near `presence.py:634`). After `"kiro-cli"` is removed from `_PROVIDER_SPECS`, `_match_provider` returns `"kiro-cli-v3"` for the kiro binary directly — the reroute is dead code.

7. Update the comment block at `presence.py:64-88` to remove references to `_KIRO_LOCK_DIR`, the dual-provider setup, and the `_match_provider` reroute rationale. Keep only the `_KIRO_PROVIDERS` family-tolerance explanation for the D32 guard.

**Exit criteria**:
- [x] `grep "_KIRO_LOCK_DIR\|_kiro_session_cwd" src/power_atlas/presence.py` returns no hits.
- [x] `grep '"kiro-cli"' src/power_atlas/presence.py | grep -v "kiro-cli-v3"` returns no hits.
- [x] `grep '"kiro-cli"' src/power_atlas/presence.py` returns hits only for `"kiro-cli-v3"`.
- [x] `python -c "from power_atlas.presence import _KIRO_PROVIDERS; print(_KIRO_PROVIDERS)"` prints `frozenset({'kiro-cli-v3'})`.
- [x] `grep '"kiro-cli"' src/power_atlas/presence.py | grep -v "kiro-cli-v3"` returns no hits (both `_PROVIDER_SPECS` and `_KIRO_PROVIDERS` entries verified).

Implementation (2026-09-17, code: 2e42cdf)
Removed `_KIRO_LOCK_DIR`, `_kiro_session_cwd`, the kiro `.lock` sidecar enumeration block from `_sidecar_records()`, the `_scan()` reroute block, and `"kiro-cli"` from `_PROVIDER_SPECS` and `_KIRO_PROVIDERS`. Updated comment block. Fixup commit 1dc456b: removed kiro lock test infrastructure from `test_data.py` (14 lock tests + `_write_kiro_lock` helper, `_scan_with` kiro_dir param, `data_kiro` refs in `_reset_kiro_caches`); also resolved merge conflict markers in `web.py` introduced by the Phase 3 sub-agent. Suite: 1920 passed, 2 skipped; remaining errors/failures all Phase 8 cleanup targets.

---

### Phase 4: Update status_classifier.py [QA]

**Goal**: Remove the v2 JSONL classification branch and the v2 store path constant.

**File scope**: `src/power_atlas/status_classifier.py`

**Covers**: SC-4

**Changes**:

1. Remove `SESSION_DIR = Path.home() / ".kiro" / "sessions" / "cli"` (`status_classifier.py:26`) and its comment. The constant is no longer used once the v2 branch is gone.

2. Remove `classify_kiro_v2()` function (`status_classifier.py:216-248`).

3. In `_resolve_jsonl_path_uncached`, remove the `if provider == "kiro-cli":` branch entirely (`status_classifier.py:100-118`). The `elif provider == "kiro-cli-v3":` branch can become `if provider == "kiro-cli-v3":`.

4. In `_resolve_jsonl_path` (the cached wrapper at `status_classifier.py:54-56`), remove `"kiro-cli"` from the provider check. The line:
   ```python
   if provider not in ("kiro-cli", "kiro-cli-v3"):
   ```
   becomes:
   ```python
   if provider != "kiro-cli-v3":
   ```

5. In `_classify_from_path` (`status_classifier.py:571-580`), remove the `if provider == "kiro-cli":` block (which includes `_is_v3_format` auto-detection and the `classify_kiro_v2` dispatch). The function becomes:
   ```python
   if provider == "kiro-cli-v3":
       return classify_kiro_v3(tail_lines)
   return None
   ```

6. Delete `_is_v3_format` function — it has exactly one call site, inside the deleted `provider == "kiro-cli"` block. No other caller exists (verified: the function only appeared in the v2 auto-detect path).

7. Add `_LOCK_TIME_RE` to the deletion list alongside the other lock constants: `_LOCK_TIME_RE` is used only by `_lock_started_at`, which is used only by `_lock_holder` (both deleted in Phase 5). Delete `_LOCK_TIME_RE` in Phase 4 as it conceptually belongs with the status classifier's store-path changes — but if it only appears in `acp.py`, handle it in Phase 5.

**Exit criteria**:
- [x] `grep "classify_kiro_v2\|SESSION_DIR\|_is_v3_format" src/power_atlas/status_classifier.py` returns no hits.
- [x] `grep '"kiro-cli"' src/power_atlas/status_classifier.py` returns no hits (only `"kiro-cli-v3"` remains).
- [x] `grep "_is_v3_format" src/power_atlas/status_classifier.py` returns either no hits (deleted) or only the function definition with a documented remaining caller.

Implementation (2026-09-17, code: eee8eae)
Removed `SESSION_DIR` constant, `classify_kiro_v2()`, `_is_v3_format()`, the `kiro-cli` branch in `_resolve_jsonl_path_uncached`, and the v2 auto-detect block in `_classify_from_path`. Cache key for `_resolve_jsonl_path` updated from 4-tuple (dropping `SESSION_DIR`) to 3-tuple. `_LOCK_TIME_RE` confirmed as `acp.py`-only — left for Phase 5. Sub-agent also updated `tests/test_web.py` to remove `TestClassifyKiroV2` and `SESSION_DIR`-patching tests (Phase 8 pull-forward). Fixup commit 7b99812: corrected `_classify_from_path` else-clause (`else: return classify_kiro_v3` \u2192 `return None`), fixed stale comment, updated 3 test literals from `"kiro-cli"` to `"kiro-cli-v3"`. 30 classifier tests pass.

---

### Phase 5: Update acp.py [QA]

**Goal**: Remove the dead `data_kiro` import, `KIRO_SESSION_DIR`, `_lock_holder`, `_stored_session_cwd`, `_load_session_cwd`; update docstrings.

**File scope**: `src/power_atlas/acp.py`

**Covers**: SC-3 (acp.py portion)

**Changes**:

1. Remove `from . import data_kiro` (`acp.py:82`). Verify afterward that `acp.py` now imports exactly two names from the rest of the package: `config.CONFIG_DIR` and `launcher._SESSION_ID_RE`.

2. Remove `KIRO_SESSION_DIR = Path.home() / ".kiro" / "sessions" / "cli"` (`acp.py:833`) and its comment block.

3. Remove `_lock_holder()` function (near `acp.py:2196`) — approximately 50 lines including its full docstring.

4. Remove `_stored_session_cwd()` function (near `acp.py:2248`) — approximately 25 lines.

5. `_load_session_cwd` — delete unconditionally. Verified: it has zero callers in `acp.py`. Its body calls `_stored_session_cwd` (also deleted). No v3 path calls it.

6. Update the three docstring references to `data_kiro.get_tool_diffs()` at `acp.py:1265`, `acp.py:1430`, `acp.py:2764`. Change each to reference `_get_tool_diffs_v3` instead of `data_kiro.get_tool_diffs()`.

7. Remove the constants `LOCK_MAX_BYTES`, `LOCK_START_SKEW_SECONDS`, `_LOCK_TIME_RE`, and the `_lock_started_at()` helper — all are used only by the deleted `_lock_holder`. Verified: `_lock_holder_v3` uses `_V3_SESSION_STALE_SECONDS` and its own path logic; it does not use any of these four names. Delete them unconditionally.

**Exit criteria**:
- [ ] `grep "data_kiro\|KIRO_SESSION_DIR\|_lock_holder\b\|_stored_session_cwd\b\|_load_session_cwd\|LOCK_MAX_BYTES\|LOCK_START_SKEW\|_LOCK_TIME_RE\|_lock_started_at" src/power_atlas/acp.py` returns no hits (except `_lock_holder_v3` and `_stored_session_cwd_v3` which are kept).
- [ ] The module-level isolation-boundary comment in `acp.py` still names exactly two guarded imports: `config.CONFIG_DIR` and `launcher._SESSION_ID_RE`.

---

### Phase 6: Update web.py [QA]

**Goal**: Remove all v2-specific provider metadata, delete/delete functions, and inline the workspace-enumerate call.

**File scope**: `src/power_atlas/web.py`

**Covers**: SC-2 (provider tile), SC-3 (web.py portion), SC-6 (web.py)

**Changes**:

1. Remove `"kiro-cli"` entries from the four provider dicts at `web.py:85-110`:
   - `PROVIDER_COLORS`: remove `"kiro-cli": "#7138cc",`
   - `PROVIDER_DISPLAY_NAMES`: remove `"kiro-cli": "kiro-cli",`
   - `PROVIDER_BADGES`: remove `"kiro-cli": "K",`
   - `_PROVIDER_BINARY_DISPLAY`: remove `"kiro-cli": "kiro-cli chat",`

2. Remove `_ACP_LISTING_PROVIDER = "kiro-cli"` (`web.py:1800`) and its comment block (lines 1795-1800).

3. Remove `_ACP_SESSION_SUFFIXES`, `_ACP_DELETE_STAGING`, `_ACP_SHARING_VIOLATION` constants and their comments (`web.py:2765-2782`).

4. Delete `_acp_session_paths()` function (`web.py:2787-2799`).

5. In `_acp_delete_session()` (`web.py:2802`): remove the entire v2 branch (the `else` block that uses `_acp_session_paths`, the rename-staging loop, the rollback logic, and the Windows sharing-violation handling). The function body becomes a direct dispatch to `data_kiro_v3.delete_session`. Keep the three-way return-code semantics (`not_found` / `in_use` / success) that `_acp_delete_many` forwards to the client — collapsing them would break the client's error-message handling.

   The simplified function body (preserving the three-way semantics):
   ```python
   def _acp_delete_session(session_id: str) -> tuple[str, str]:
       """Delete one session from kiro-cli's store. Returns (error_code, message) or ("", "")."""
       from . import data_kiro_v3
       result = data_kiro_v3.delete_session(session_id)
       if result is None:
           return "not_found", "Session not found in the kiro-cli store."
       if result is False:
           return "delete_error", "Could not delete all session files (partial delete logged)."
       return "", ""
   ```

   > **Rejected:** collapsing `not_found`/`in_use`/`delete_error` into a single `"delete_error"` code — `_acp_delete_many` forwards `code` to the client UI which handles each code distinctly. **Use instead:** preserve the three-way semantics above.

6. Delete `_acp_sessions_for_workspace()` function (`web.py:3023-3065`). At its one call site (`web.py:3173`):
   ```python
   # Before (calls deleted function):
   all_ids = await asyncio.to_thread(
       _acp_sessions_for_workspace, cwd, include_v3=True)
   ```
   Replace with an inline call. Note: `data_kiro_v3.load_sessions(cwd)` returns a **2-tuple** `(list[Session], dict)` — unpack it:
   ```python
   # After (inline v3 enumeration):
   _v3_sessions, _ = await asyncio.to_thread(data_kiro_v3.load_sessions, cwd)
   all_ids = [s.session_id for s in _v3_sessions]
   ```
   Update the comment below the call that references the v2/v3 dual enumeration.

7. In `_acp_delete_many()` (`web.py:2958`):
   - Remove the `touched_v2: set[str] = set()` line.
   - Remove the `is_v3 = session_id.startswith("sess_")` line and all its uses. Since all remaining session IDs are `sess_`-prefixed after v2 removal, use `_lock_holder_v3` and `_stored_session_cwd_v3` unconditionally. For any non-`sess_`-prefixed ID that somehow reaches the function (bare UUID passed by a browser), `_lock_holder_v3` returns `None` (no holder found) and `_stored_session_cwd_v3` returns `""`, and the subsequent `_acp_delete_session` will return `"not_found"` — graceful failure, no crash.
   - Replace `if cwd: (touched_v3 if is_v3 else touched_v2).add(cwd)` with `if cwd: touched_v3.add(cwd)`.
   - Remove the `for cwd in touched_v2: data.session_cache.forget(cwd, _ACP_LISTING_PROVIDER)` block.
   - Add exit criterion: `grep "is_v3" src/power_atlas/web.py` returns no hits in the `_acp_delete_many` function body.

8. Update the two hardcoded fallback provider sets in `_workspace_status` (`web.py:464` and `web.py:481`):
   ```python
   # Before:
   for prov in (providers or {"kiro-cli", "claude-code", "kiro-cli-v3"}):
   # After:
   for prov in (providers or {"claude-code", "kiro-cli-v3"}):
   ```
   Apply to both occurrences.

9. Update `api_session_transcript` default (`web.py:4231`):
   ```python
   # Before:
   async def api_session_transcript(sid: str = "", provider: str = "kiro-cli", cwd: str = ""):
   # After:
   async def api_session_transcript(sid: str = "", provider: str = "kiro-cli-v3", cwd: str = ""):
   ```

10. Update `api_launch` and `api_new_session` provider defaults where they fall back to `"kiro-cli"`:
    - `web.py:4300` area: `provider = body.get("provider") or "kiro-cli"` → `or "kiro-cli-v3"`
    - `web.py:4366` area: `provider = body.get("provider") or "kiro-cli"` → `or "kiro-cli-v3"`

**Exit criteria**:
- [ ] `grep '"kiro-cli"' src/power_atlas/web.py` returns no hits (only `"kiro-cli-v3"` remains).
- [ ] `grep "_acp_session_paths\|_acp_sessions_for_workspace\|_ACP_SESSION_SUFFIXES\|_ACP_LISTING_PROVIDER\|touched_v2" src/power_atlas/web.py` returns no hits.
- [ ] The two fallback provider set literals at `web.py` near `_workspace_status` do not contain `"kiro-cli"`.

---

### Phase 7: Update launcher.py [QA]

**Goal**: Remove `"kiro-cli"` from all launcher provider tables and the v2 argument-build branch.

**File scope**: `src/power_atlas/launcher.py`

**Covers**: SC-6 (launcher.py)

**Changes**:

1. Remove `"kiro-cli": "kiro-cli",` from `_PROVIDER_DISPLAY` (`launcher.py:82`).
2. Remove `"kiro-cli": "kiro-cli",` from `_PROVIDER_BINARY` (`launcher.py:89`).
3. Remove `"kiro-cli": True,` from `_PROVIDER_TERMINAL` (`launcher.py:96`).
4. In `_build_provider_args()`, remove the `else: # kiro-cli` branch (lines `~launcher.py:113-120`). The function now has explicit branches only for `"claude-code"`, `"kiro-ide"`, and `"kiro-cli-v3"`. Add `raise ValueError(f"Unknown provider: {provider}")` as the new else clause. The "never raises" framing is not backed by any documented contract; a clear `ValueError` for an unknown provider is more maintainable and surfaces misconfiguration immediately.
   > **Rejected:** returning empty list `[]` for unknown provider — silently builds a malformed command that fails at launch with a confusing error message. **Use instead:** `raise ValueError`.
5. Update `launch_session` default: `provider: str = "kiro-cli"` → `provider: str = "kiro-cli-v3"` (`launcher.py:145`).
6. Update `launch_batch` default: `provider = s.get("provider") or "kiro-cli"` → `or "kiro-cli-v3"` (`launcher.py:253`).

**Exit criteria**:
- [ ] `grep '"kiro-cli"' src/power_atlas/launcher.py` returns no hits (only `"kiro-cli-v3"` remains).
- [ ] `grep "# kiro-cli" src/power_atlas/launcher.py` returns no hits (the `else: # kiro-cli` comment is gone).

---

### Phase 8: Update tests [QA]

**Goal**: Remove v2-exercising tests; update test infrastructure; verify suite is green.

**File scope**: `tests/test_data.py`, `tests/test_launcher.py`, `tests/test_web.py`, `tests/test_config.py`

**Covers**: SC-8, SC-9

**test_data.py changes:**

Delete the following (v2-only — subject no longer exists):
- `from power_atlas import data_kiro` import at the top of the file.
- `mock_sessions` fixture (patches `data_kiro.SESSION_DIR`).
- `_write_session` helper function (writes v2-format `.json` + `.jsonl` flat files).
- All top-level test functions that use `mock_sessions`: `test_discover_workspaces_with_data`, `test_discover_workspaces_empty_when_missing`, `test_discover_workspaces_filters_subagents`, `test_get_sessions_returns_populated`, `test_get_sessions_filters_subagents`, `test_malformed_json_skipped`, `test_malformed_jsonl_skipped`, `test_missing_jsonl_still_returns_session`.
- `TestRefreshStaleEntries` class (uses `mock_sessions`).
- `TestWarmupPinned` class (uses `mock_sessions`).
- `test_cache_hit_no_reread` and `test_cache_miss_triggers_load` functions (use `mock_sessions`).
- `TestGetSessionTail` class (uses `mock_sessions`).
- `TestKiroPromptsCache` class (patches `data_kiro` directly).
- `TestKiroToolDiffs` class (uses `data_kiro`).
- `TestKiroGetFullTranscript` class (uses `data_kiro`).
- Three standalone functions: `test_kiro_load_sessions_filters_by_cwd_and_skips_subagents`, `test_kiro_index_picks_up_a_newly_created_session`, `test_kiro_load_sessions_sees_rewritten_metadata`.
- `TestGetFullTranscriptDispatch` class entirely — move it to `test_data_kiro_v3.py` where v3 fixtures already exist. The class tests the dispatch logic of `data.get_full_transcript`; the test can be rewritten there using the existing v3 session fixture infrastructure. `test_data.py` has no v3 session fixture (only the v2 `mock_sessions` fixture), so the update-in-place option is not viable without adding a new fixture.
- In `_reset_kiro_caches()`: remove all lines that reference `data_kiro.*` cache attributes (the 6 lines clearing `_meta_cache`, `_cwd_index`, `_cwd_index_mtime`, `_prompts_cache`, `_tail_cache`, `_first_prompt_cache`). Keep the v3 cache reset lines that are in the `try` block. Rename the function to `_reset_v3_caches()` to accurately reflect what it resets.

Delete the following (kiro lock-sidecar tests — subject is the removed `.lock` scan):
- `test_presence_sidecar_identifies_kiro_session`
- `test_presence_sidecar_identifies_kiro_v3_session` (tests a removed code path — the lock scan)
- `test_presence_sidecar_rejects_recycled_pid_on_other_binary`
- `test_presence_sidecar_rejects_pid_recycled_onto_same_binary`
- `test_presence_hides_a_lock_our_own_agent_orphaned`
- `test_presence_leaves_a_foreign_kiro_lock_alone`
- `test_presence_hides_a_v3_agent_orphaned_lock`
- `test_presence_leaves_a_foreign_v3_kiro_lock_alone`
- `test_presence_family_tolerance_does_not_defeat_recycled_pid_check`
- `test_presence_kiro_lock_far_newer_than_its_process_is_live`
- `test_presence_kiro_lock_rewritten_in_place_is_reparsed`
- `test_presence_suppresses_nothing_until_something_publishes` (uses `_write_kiro_lock`)
- `test_presence_sidecar_rejects_sidecar_predating_its_process` (uses `_write_kiro_lock`)
- `test_presence_sidecar_malformed_record_does_not_drop_others` (uses `_write_kiro_lock`)

Also delete `_write_kiro_lock` helper once all its callers above are removed.

Keep all other presence tests (process-table scan tests, Claude sidecar tests, ACP publish tests, `TestProbableFreshSession`), and all Claude, Kiro IDE, normalization, cache, and paged-list tests.

**test_launcher.py changes:**

Delete v2-specific tests:
- `TestPowerShellInvocation.test_kiro_new_session_command` and `test_kiro_resume_command` (calls `_build_provider_args("kiro-cli", ...)`).
- `TestLaunchSession.test_success` — if it calls `launch_session(cwd, session_id="abc123")` with no provider: update to pass `provider="kiro-cli-v3"` explicitly.
- `test_launch_session_kiro_builds_correct_args` and other tests that pass `provider="kiro-cli"` — delete tests that test v2-specific behavior; update tests that are checking generic launcher behavior (non-terminal detection, error handling, Windows vs. Linux paths) to use `"kiro-cli-v3"`.
- The `test_pwsh_escapes_single_quotes` and `test_cmd_rejects_metacharacters` tests pass `"kiro-cli"` as a shell token (not a provider name) in the `cmds` list. These test generic shell escaping; the string value is irrelevant to what is tested. Leave the token as `"kiro-cli"`, or update to `"kiro-cli-v3"` — either is correct.

**test_web.py changes:**

Delete:
- `TestAcpLockPreflight` class entirely (tests `_lock_holder()` — function deleted in Phase 5).
- Before deleting, verify `TestAcpSessionIdValidation` tests at `test_web.py:4531-4594`: these test session-ID format validation (confirmed in exploration: `test_a_rejected_id_reaches_neither_a_path_nor_the_wire` and `test_a_rejected_id_leaves_a_server_side_trace`). Keep them — they test the ID validation guard which is independent of `_lock_holder`.
- ~20 tests across `TestAcpDeleteEndpoint`, `TestAcpAvailabilityV3`, `TestAcpDeleteManyV3Dispatch`, `TestAcpListingEndpoint`, and `TestAcpSessionIdValidation` that call `monkeypatch.setattr(acp_mod, "_lock_holder", ...)` — remove the `monkeypatch.setattr` line from each (the attribute won't exist after Phase 5). Any test whose logic **depends** on `_lock_holder` returning a specific value (e.g. `test_a_v2_id_still_routes_to_the_v2_lock_holder` at `test_web.py:14386`) must be deleted outright.
- `TestClassifyKiroV2` (read `test_web.py:8592` area to confirm exact class location — imports `classify_kiro_v2` which is deleted in Phase 4). Also audit `TestResolveJsonlPath` and `TestGetSemanticStatus` for v2 sub-cases and delete those methods.
- The `acp_store` fixture at `test_web.py:4485` currently patches `acp_mod.KIRO_SESSION_DIR`. After Phase 5 removes this constant, remove **that single line** from the fixture body. Keep the rest of the fixture (100+ tests depend on the other cleanup it provides).
- `TestAcpSessionsForWorkspaceV3.test_include_v3_false_by_default_ignores_v3_sessions` (tests v2 default behavior — function deleted).
- `test_acp_sessions_for_workspace_returns_matching_ids` (tests the deleted function's v2 scan). Delete.
- Any test in `TestAcpDeleteEndpoint` that specifically tests the v2 delete path (v2 rename-staging, v2 `.json`/`.jsonl`/`.history` suffix handling, `touched_v2` cache invalidation).

**test_config.py changes:**

Delete:
- `test_trust_all_tools_migration` (tests the deleted migration block).
- `test_trust_all_tools_no_migration_when_provider_settings_exist` (tests the deleted migration block).
- Any `test_trust_all_tools_false_no_migration` variant if present.
- Do **NOT** delete `test_pinned_folders_dict_to_str_migration` — it tests the `list[dict]` → `list[str]` conversion migration which is unrelated to v2 removal and still active.
- Update `test_provider_settings_round_trip` (correct test name — the test function is `test_provider_settings_round_trip`, not `test_provider_settings_persists`) (`test_config.py:~405`): the fixture uses `"kiro-cli"` as a provider_settings key; after Phase 2 adds `provider_settings.pop("kiro-cli", None)` to `load_config`, loading a config with `"kiro-cli"` strips it. Update the fixture to use `"kiro-cli-v3"` and `"claude-code"` keys only.

**Additional Phase 8 scope (identified in Phase 1 review):**
- `test_web.py::test_close_session_releases_the_diff_backfill` — imports `from power_atlas import data_kiro` (line ~4804). Delete this test or redirect it to v3 (it tests ACP diff-backfill cleanup; the diff-backfill path moved to `data_kiro_v3`).
- `test_data.py` non-v2 tests that use `provider="kiro-cli"` as a cache key (e.g., `TestSessionCacheIsolation` at lines 469, 472, 485, 489) — audit these and update to `"kiro-cli-v3"`.
- `test_web.py` `test_get_provider_settings` tests that call `/api/provider/kiro-cli` (4 tests) — update to use `"kiro-cli-v3"` or delete.

**Exit criteria**:
- [ ] `grep "data_kiro\b" tests/test_data.py` returns no hits.
- [ ] `grep '"kiro-cli"' tests/test_launcher.py` returns no hits (only `"kiro-cli-v3"` if present).
- [ ] `grep "TestAcpLockPreflight\|trust_all_tools_migration\|TestClassifyKiroV2" tests/test_web.py tests/test_config.py` returns no hits.
- [ ] `grep "KIRO_SESSION_DIR" tests/test_web.py` returns no hits (the `acp_store` fixture line removed).
- [ ] `.venv-PowerAtlas\Scripts\python -m pytest tests/ -x` passes with exit code 0.
- [ ] `node tests/acp_page.test.mjs` passes.

---

### Phase 9: Update documentation [QA]

**Goal**: Update README, docs/KNOWLEDGE.md, memory/MEMORY.md, plans docs, and HARNESS.md per the doc-impact scan. Preserve v2 architecture history in KNOWLEDGE.md.

**File scope**: `README.md`, `docs/KNOWLEDGE.md`, `memory/MEMORY.md`, `plans/ROADMAP.md`, `plans/tests/260701_POWERATLAS.md`, `plans/tests/HARNESS.md`

**Covers**: SC-10, SC-11

**README.md changes** (SC-10):
1. Remove `(scanned separately from v2)` from line 61.
2. Remove the entire `[provider_settings.kiro-cli]` block (lines 130-138) from the config sample. The config sample proceeds directly to `[provider_settings.claude-code]`.

**docs/KNOWLEDGE.md changes** (SC-11):
1. At lines 121-125: remove the v2 diff-backfill paragraph that references `data_kiro.get_tool_diffs()`, `src/power_atlas/data_kiro.py`, `sessions/cli/<id>.jsonl`, and the grep anecdote. The surrounding v3-only content stands on its own.
2. Add a new subsection "## kiro-cli v2 store (historical)" after the existing diff-backfill section. Content:
   ```
   ## kiro-cli v2 store (historical — removed 2026-09-17)

   kiro-cli v2 stored sessions as flat files under `~/.kiro/sessions/cli/`:
   - `<uuid>.json` — session metadata (`session_id`, `cwd`, `title`, `created_at`, `updated_at`, `parent_session_id`)
   - `<uuid>.jsonl` — conversation content; NDJSON, each line `{"version":"v1","kind":"<kind>","data":{...}}`; kinds: `Prompt` (user), `AssistantMessage` (agent), `ToolResults` (tool output)
   - `<uuid>.lock` — liveness lock; JSON `{"pid":<int>,"started_at":"<RFC3339>"}` while a session was open
   - `<uuid>.history` — plain-text user input history

   The SQLite DB at `%LOCALAPPDATA%\Kiro-Cli\data.sqlite3` (`conversations_v2` table) held a small number of "classic" sessions not otherwise stored on disk.

   PowerAtlas removed v2 support in this plan. The `~/.kiro/sessions/cli/` directory is not cleaned; the files remain on disk but PowerAtlas no longer reads them.
   ```

**memory/MEMORY.md changes:**
- Entry "Renaming a kiro-cli session requires an atomic write" (heading near line 18): this entry documents the atomic-write pattern for kiro-cli session metadata. The pattern remains valid for v3 `session.json` writes. Update the entry: replace `~/.kiro/sessions/cli/<session-id>.json` (v2 path) with `~/.kiro/sessions/<hash>/sess_<uuid>/session.json` (v3 path), and replace `data_kiro.py`'s `_meta_cache` cache-poisoning mechanism with the equivalent v3 concern (the v3 scanner in `data_kiro_v3.py` is mtime-keyed similarly). If the entry's `How to apply` pattern is only relevant for the v2 admin path (renaming sessions from the settings panel), mark the entry stale instead and add `Stale-after: 2026-09-17`.
- Entry "Session-file parsing must be skipped…" (near line 90): remove the specific `data_kiro._cwd_to_files()` function reference; keep the general principle (skip, don't scan) with a note that the v3 equivalent is `_cwd_to_sessions` in `data_kiro_v3.py`.
- Entry "acp.py's isolation boundary forces `_build_child_env` duplication" (near line 243): remove the parenthetical `; data_kiro is a third import but predates the boundary description`.
- Entry containing "the same … call site v2's `_lock_holder` already serves" (near line 223): remove the clause.

**plans/ROADMAP.md changes:**
- Line ~144 (Chained launches item): update the terminal-session exclusion note that references `sessions/cli` tail reads; replace with a note that v2 session files are no longer read by PowerAtlas.
- Line ~170 (P2b item): update to state PowerAtlas no longer merges `conversations_v2` sqlite cwds (v2 removed).

**plans/tests/260701_POWERATLAS.md changes** (28 hits from doc-impact scan):
Apply all 28 hits from the doc-impact summary table: update provider names from `kiro-cli` → `kiro-cli-v3` where appropriate, delete section 1.9, remove `data_kiro.py` from section headers, delete sections 5.3 and 5.4, update test-targets lines for deleted functions, etc.

**plans/tests/HARNESS.md changes:**
- Remove `kiro-session-data` row (line 18).
- Remove `kiro-cli-sqlite` row (line 20).
- Update provider count from "four" to "three" (line 33): `three — claude-code, kiro-ide, and kiro-cli-v3`.

**Exit criteria**:
- [ ] `grep -r '"kiro-cli"' README.md` returns no hits.
- [ ] `grep "provider_settings.kiro-cli" README.md` returns no hits.
- [ ] `grep "scanned separately from v2" README.md` returns no hits.
- [ ] `grep "data_kiro" docs/KNOWLEDGE.md` returns no hits.
- [ ] `docs/KNOWLEDGE.md` contains a section heading "kiro-cli v2 store (historical".
- [ ] `grep "trust_all_tools" plans/tests/260701_POWERATLAS.md` returns no hits (section 5.4 deleted).
- [ ] `grep "data_kiro.py" plans/tests/260701_POWERATLAS.md` returns no hits (section 1.9 and header updated).

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| v2 session IDs pinned in `config.toml` silently disappear from pinned rail | Low — user is sole user and is aware v2 history vanishes | Acceptable per Q1 decision; no action needed |
| Some provider-agnostic tests in `test_data.py` depend on `mock_sessions` indirectly | Medium — could leave missing coverage if misidentified | Phase 8 provides detailed per-class deletion list; implementer reads each class before deleting |
| `_load_session_cwd` callers — the function might still serve a v3 path | Medium — deleting it while a v3 caller exists would break session loading | Phase 5 explicitly gates on a caller search before deciding to delete; the exit criterion verifies |
| `_is_v3_format` callers — might have a caller outside the v2 branch | Low — function is small and only called in the status classifier context | Phase 4 explicitly gates on a grep before deciding to delete |
| 28 doc-impact hits across 6 files could have omissions | Low — scan was comprehensive; any stale reference left behind will be harmless (no runtime effect) | `/qclose` Pass 4 will catch any remaining drift |

## 7) Verification

```powershell
# Full test suite
.venv-PowerAtlas\Scripts\python -m pytest tests/ -x -v

# JS page tests
node tests/acp_page.test.mjs

# Provider list check
.venv-PowerAtlas\Scripts\python -c "from power_atlas import data; print(list(data.PROVIDERS.keys()))"
# Expected: ['claude-code', 'kiro-ide', 'kiro-cli-v3']

# No kiro-cli v2 references in production code (excluding plan file)
grep -r '"kiro-cli"' src/power_atlas/ | grep -v kiro-cli-v3

# acp.py isolation boundary still satisfied
grep -n "^from \. import\|^import power_atlas" src/power_atlas/acp.py
# Expected: only config and launcher named
```

Manual spot-check: start PowerAtlas, open dashboard, confirm no "kiro-cli" provider filter tile and no bare-UUID sessions in the rail.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Remove `[provider_settings.kiro-cli]` block and "scanned separately from v2" note | 9 |
| `docs/KNOWLEDGE.md` | Remove v2 diff-backfill paragraph; add "kiro-cli v2 store (historical)" section | 9 |
| `memory/MEMORY.md` | Mark/remove stale "atomic write" entry; remove `data_kiro._cwd_to_files` reference; remove two `_lock_holder`/v2 clauses | 9 |
| `plans/ROADMAP.md` | Update Chained launches v2 path note; update P2b sqlite note | 9 |
| `plans/tests/260701_POWERATLAS.md` | 17 doc-impact hits: update provider names, delete sections 1.9/5.3/5.4, update test-targets lines | 9 |
| `plans/tests/HARNESS.md` | Remove `kiro-session-data` and `kiro-cli-sqlite` rows; update provider count | 9 |

## 9) Implementation Divergences from Plan

- **Phase 1 pull-forward**: `from . import data_kiro` removal from `acp.py` (originally Phase 5) was pulled into Phase 1 because deleting `data_kiro.py` caused an `ImportError` when importing `acp.py`. All other Phase 5 items (`KIRO_SESSION_DIR`, `_lock_holder`, etc.) remain deferred. The `acp.py` isolation boundary is still exactly 2 guarded imports.
- **Phase 8 scope additions** (identified in Phase 1 review): `test_web.py::test_close_session_releases_the_diff_backfill` imports `data_kiro` directly — must be deleted/redirected in Phase 8. `test_config.py` trust_all_tools migration tests and `provider="kiro-cli"` occurrences in non-v2 `test_data.py` test classes also need cleanup in Phase 8.

## Follow-up Work (Deferred)

1. **`test_presence_claude_sidecar_outside_window_is_not_live` loses its discriminating companion.** After deleting `test_presence_kiro_lock_far_newer_than_its_process_is_live`, the forward-skew discriminating pair in presence tests is broken; the surviving test's docstring explicitly names the deleted test as its complement. The forward-skew guard's mutation coverage is reduced. Source: review finding 24 (Low). No v3 equivalent test exists yet.

2. **`test_data_kiro_v3.py` `TestGetFullTranscriptDispatch` addition.** Phase 8 moves `TestGetFullTranscriptDispatch` to `test_data_kiro_v3.py`; the class needs to be written using existing v3 fixtures. This is implementation work, not a deferral — capturing here as a reminder that the moved class needs net-new v3 fixture code, not just a function rename.

## Review Log

### 2026-09-17 — Implementation Review (after Phase 4, persona: Senior engineer)

Implementation health: Yellow (after auto-fix).
Initial health: Yellow (M1 plan-spec deviation). After fixup: Green.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | `_classify_from_path` else-clause dispatched to `classify_kiro_v3` for unknown providers instead of `return None` (plan spec). | Fixed — changed to `return None` in fixup 7b99812. |
| 2 | Low | Three test literals in `test_web.py` used `"kiro-cli"` provider inside mocked `get_semantic_status` calls. | Fixed — replaced with `"kiro-cli-v3"` in fixup 7b99812. |
| 3 | Low | Stale comment at `status_classifier.py:40` referenced `"kiro-cli / kiro-cli-v3"`. | Fixed — updated to `"kiro-cli-v3"` in fixup 7b99812. |

### 2026-09-17 — Implementation Review (after Phase 3, persona: Reliability engineer)

Implementation health: Yellow (after auto-fix).
Initial health: Red (2 High). After fixup: all high findings resolved.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `_scan_with()` in `test_data.py` still patched deleted `_KIRO_LOCK_DIR` — `AttributeError` on every presence test. | Fixed — removed `kiro_dir` param and `_KIRO_LOCK_DIR` patch line from `_scan_with()` in fixup 1dc456b. |
| 2 | High | 14 presence tests use `_write_kiro_lock`/`kiro_dir=` — fail or pass vacuously post-removal. All are Phase 8 deletion targets. | Fixed — deleted all 14 kiro lock tests and `_write_kiro_lock` helper in fixup 1dc456b. |
| 3 | Medium | `test_presence_matches_kiro_resume_id_flag` asserted `"kiro-cli"` provider; after removing `"kiro-cli"` from `_PROVIDER_SPECS` it should assert `"kiro-cli-v3"`. | Fixed — updated assertion in fixup 1dc456b. |
| 4 | Low | Two stale comments in `presence.py` still reference kiro lock structure post-removal. | Fixed — updated in fixup 1dc456b. |

### 2026-09-17 — Implementation Review (after Phase 2, persona: Senior engineer)

Implementation health: Green.
2 findings (0 High, 0 Medium, 2 Low). Both auto-fixed.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Low | `test_save_config_drops_trust_all_tools` still used `"kiro-cli"` as fixture key — misleading but no coverage gap. | Fixed — updated fixture to use `"kiro-cli-v3"` in fixup commit 2dd0f41. |
| 2 | Low | Phase 2 exit criteria wording literally unsatisfied (`trust_all_tools` in `_LEGACY_KEYS`, `"kiro-cli"` in pop line). | Fixed — rewrote exit criteria text to match the intentional plan design. |

### 2026-09-17 — Implementation Review (after Phase 1, persona: Senior engineer, Maintainability reviewer)

Implementation health: Yellow.
7 findings (3 High, 2 Medium, 2 Low).

SC-1 achieved; PROVIDERS registry clean; `acp.py` isolation boundary intact (exactly 2 guarded imports); all `SessionCache`/dispatch defaults updated to `"kiro-cli-v3"`. Yellow rating is for test failures that are known forward-work.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `test_data.py` collection-blocked by stale `discover_workspaces` and `data_kiro` imports at lines 17/381. | Fixed — removed the two blocking import lines in fixup commit 9ed9b80. |
| 2 | High | `test_web.py::test_close_session_releases_the_diff_backfill` imports `data_kiro` directly — not in Phase 8 list. | Fixed — added to Phase 8 scope via Divergences note and Phase 8 instructions below. |
| 3 | High | Four `test_get_provider_settings` tests now 404 (use `"kiro-cli"` provider which is no longer in PROVIDERS). | Escalated — expected forward-work gap; Phase 6/8 clean-up. Documented in Divergences. |
| 4 | Medium | Three `acp.py` docstring lines still reference deleted `data_kiro.get_tool_diffs()`. | Escalated — correctly deferred to Phase 5; Phase 5 exit criteria cover these lines. |
| 5 | Medium | `test_config.py` trust_all_tools migration tests will break after Phase 2; not in Phase 8 scope. | Fixed — added to Phase 8 scope via Divergences note. |
| 6 | Medium | `test_data.py` `TestSessionCacheIsolation` and others use `provider="kiro-cli"` as key — not enumerated in Phase 8. | Fixed — added audit instruction to Phase 8 Divergences note. |
| 7 | Low | Three extra blank lines in `data.py` after SESSION_DIR removal. | Fixed — normalized in fixup commit 9ed9b80. |

### 2026-09-17 — Plan Creation (via /qplan, high-effort 4-persona review)

25 findings (8 High, 10 Medium, 7 Low). All 25 auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `load_sessions` returns a 2-tuple; plan's inline replacement iterated the tuple, not the session list. | Fixed — replacement now uses `_v3_sessions, _ = await asyncio.to_thread(...)`. |
| 2 | High | `acp_store` fixture patches `KIRO_SESSION_DIR` (deleted in Phase 5); 100+ tests would fail with `AttributeError`. | Fixed — Phase 8 explicitly removes the `KIRO_SESSION_DIR` monkeypatch line from the fixture body while keeping the fixture. |
| 3 | High | ~20 tests outside `TestAcpLockPreflight` stub `acp_mod._lock_holder` via `monkeypatch.setattr`; all break after Phase 5 deletion. | Fixed — Phase 8 now instructs: remove each `monkeypatch.setattr(_lock_holder)` line; delete any test that depends on its return value. |
| 4 | High | `TestClassifyKiroV2` in `test_web.py` was absent from Phase 8's deletion list. | Fixed — added to Phase 8 test_web.py deletion list with an audit instruction for adjacent test classes. |
| 5 | High | 5 unlisted kiro lock tests (`test_presence_suppresses_nothing_until_something_publishes`, `test_presence_sidecar_rejects_sidecar_predating_its_process`, `test_presence_sidecar_malformed_record_does_not_drop_others`, `test_presence_kiro_lock_rewritten_in_place_is_reparsed`, `test_presence_kiro_lock_far_newer_than_its_process_is_live`) use `_write_kiro_lock` and were absent from Phase 8 deletion list. | Fixed — all 5 added to the Phase 8 deletion list. |
| 6 | High | `trust_all_tools` removal from `_LEGACY_KEYS` would let the key persist in `config.toml` forever for legacy configs. | Fixed — Phase 2 now keeps `"trust_all_tools"` in `_LEGACY_KEYS`; only the migration block is removed. |
| 7 | High | `_acp_delete_session` simplified body lost the `not_found`/`in_use`/`delete_error` three-way semantics that the client UI handles distinctly. | Fixed — updated Phase 6 body preserves three-way semantics with `result is None` / `result is False` / success branches. |
| 8 | High | `TestGetFullTranscriptDispatch.test_dispatches_to_the_named_provider` cannot be updated in-place — `test_data.py` has no v3 session fixture. | Fixed — Phase 8 now moves the class to `test_data_kiro_v3.py` where v3 fixtures exist. |
| 9 | Medium | `_PROVIDER_SPECS["kiro-cli"]` in `presence.py` was absent from Phase 3 changes; its omission would fail Phase 3's own exit criterion. | Fixed — Phase 3 now explicitly removes `"kiro-cli"` from `_PROVIDER_SPECS` and deletes the `_scan()` reroute block that depended on it. |
| 10 | Medium | Phase 2 `trust_all_tools` removal from `_LEGACY_KEYS` same as H6 above. | Fixed — same as H6. |
| 11 | Medium | Plan used wrong test function name `test_provider_settings_persists` — actual name is `test_provider_settings_round_trip`. | Fixed — corrected to `test_provider_settings_round_trip` in Phase 8. |
| 12 | Medium | Plan said to delete `test_pinned_folders_provider_migration` — but that test exercises a live `list[dict]` → `list[str]` migration path unrelated to v2. | Fixed — Phase 8 now explicitly keeps `test_pinned_folders_dict_to_str_migration` (correct test name). |
| 13 | Medium | `_is_v3_format` left as an implementer decision with conditional deletion. | Fixed — Phase 4 now unconditionally deletes `_is_v3_format` (verified: only one call site, in the deleted v2 block). |
| 14 | Medium | Phase 6 simplified `_acp_delete_session` snippet omitted the local `from . import data_kiro_v3` needed in the function body. | Fixed — snippet updated to include the local import. |
| 15 | Medium | Phase 6 step 7 removed `is_v3` but didn't add an exit criterion verifying it's gone; also the `_acp_delete_many` simplification was incomplete. | Fixed — added `grep "is_v3" src/power_atlas/web.py` exit criterion and clarified graceful handling of bare-UUID IDs. |
| 16 | Medium | `_kiro_session_cwd` removal creates a cwd-population gap for ACP sessions with no `--resume-id`. | Fixed — Phase 3 now documents that the first-pass process-table scan at `presence.py:650` already populates cwd from `proc.cwd()`; `_kiro_session_cwd` was a secondary fallback only. No coverage gap. |
| 17 | Medium | `_LOCK_TIME_RE` orphaned dead constant after `_lock_holder`/`_lock_started_at` removal not mentioned. | Fixed — Phase 5 now explicitly includes `_LOCK_TIME_RE` in the deletion list alongside the other lock constants. |
| 18 | Medium | `test_a_v2_id_still_routes_to_the_v2_lock_holder` at `test_web.py:14386` was absent from Phase 8 deletion list. | Fixed — Phase 8 now instructs deletion of any test that depends on `_lock_holder` return values. |
| 19 | Low | `_load_session_cwd` decision deferred to implementer; evidence already clear (zero callers). | Fixed — Phase 5 now unconditionally deletes `_load_session_cwd`. |
| 20 | Low | `LOCK_MAX_BYTES`/`LOCK_START_SKEW_SECONDS` exit criterion was ambiguous with conditional wording. | Fixed — Phase 5 now deletes all four lock constants unconditionally with verified rationale. |
| 21 | Low | `_reset_kiro_caches` rename deferred ("either is fine"). | Fixed — Phase 8 now commits to renaming to `_reset_v3_caches()`. |
| 22 | Low | `_build_provider_args` else branch: plan waffled between `ValueError` and empty list. | Fixed — Phase 7 now commits to `raise ValueError` with a rejected-alternative note. |
| 23 | Low | Exit criterion grep patterns could match comment lines. | Fixed — Phase 3 and Phase 6 exit criteria now use `| grep -v "kiro-cli-v3"` or `| grep -v '^\s*#'` patterns where relevant. |
| 24 | Low | `test_presence_claude_sidecar_outside_window_is_not_live` loses its discriminating complement test. | Accepted (Low, no behavior change) — the loss of `test_presence_kiro_lock_far_newer_than_its_process_is_live` as a companion weakens mutation coverage on the forward-skew logic; deferred to Follow-up Work. |
| 25 | Low | `memory/MEMORY.md` "atomic write" entry: plan suggested staling or deleting; better to update to v3 path. | Fixed in Phase 9 — added guidance to update the entry to reference v3 `session.json` atomic-write requirement rather than deleting it. |

## Harness Improvement Opportunities
*(Reserved)*
