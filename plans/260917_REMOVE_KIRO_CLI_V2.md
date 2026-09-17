# Remove kiro-cli v2 from PowerAtlas

> **Date**: 2026-09-17
> **Status**: Exploring
> **Scope**: Delete the kiro-cli v2 provider (launcher, session history, ACP v2 paths, presence lock scan, status classifier v2 branch) and all associated code, tests, and docs.

---

## Intent

### Problem statement & desired outcomes

PowerAtlas currently registers kiro-cli v2 (`"kiro-cli"` provider, bare UUID session IDs,
`~/.kiro/sessions/cli/` flat store) alongside kiro-cli v3 (`"kiro-cli-v3"`, `sess_`-prefix,
hashed workspace dirs). kiro-cli v2 is no longer used: the ACP path already runs v3-only,
and the dashboard v2 launcher is superseded. The v2 data layer, launcher entries, ACP v2
code paths, and all related test and doc surface area should be removed entirely.

### Success criteria

1. `data_kiro.py` is deleted; `data.PROVIDERS` no longer contains `"kiro-cli"`.
2. The dashboard shows no v2 sessions and no "kiro-cli" (v2) provider tile.
3. The v2 launcher, v2 ACP paths (`_lock_holder`, `KIRO_SESSION_DIR`, `_stored_session_cwd`,
   `_acp_session_paths`, `_acp_sessions_for_workspace` v2 scan), and v2 delete path are
   removed from `acp.py` and `web.py`.
4. `classify_kiro_v2`, the `provider == "kiro-cli"` branch of `_resolve_jsonl_path`, and
   `SESSION_DIR` (v2 store path) are removed from `status_classifier.py`.
5. The kiro `.lock`-file sidecar scan (`_KIRO_LOCK_DIR`, `_kiro_session_cwd`) is removed
   from `presence.py`.
6. The `"kiro-cli"` entries in `config.py`, `launcher.py`, `web.py` provider dicts are gone.
7. `[provider_settings.kiro-cli]` is dropped from saved `config.toml` on the next save.
8. `test_data.py` v2-exercising tests and `test_launcher.py` v2-specific tests are deleted.
9. `test_web.py` `TestAcpLockPreflight` and the v2 regression in `TestAcpSessionsForWorkspaceV3` are deleted.
10. `README.md` `[provider_settings.kiro-cli]` config sample and v2 discovery references are removed.
11. The full test suite passes with no regressions.

### Scope boundaries & non-goals

**In scope:**
- Delete `data_kiro.py` and the SQLite-based v2 workspace discovery it contained.
- Remove `"kiro-cli"` from `data.PROVIDERS` and all provider dicts in `web.py`, `launcher.py`, `config.py`.
- Remove `_lock_holder`, `KIRO_SESSION_DIR`, `_stored_session_cwd`, `_load_session_cwd` (v2 path) from `acp.py`.
- Remove `_acp_session_paths`, `_ACP_SESSION_SUFFIXES`, `_ACP_DELETE_STAGING`, `_ACP_SHARING_VIOLATION`, `_ACP_LISTING_PROVIDER`, v2 branch of `_acp_delete_session`, and `_acp_sessions_for_workspace` from `web.py`; inline the v3 call at the one remaining call site.
- Remove `classify_kiro_v2`, the `provider == "kiro-cli"` branch in `_resolve_jsonl_path_uncached`, `SESSION_DIR` constant, and the `"kiro-cli"` branch in `_classify_from_path` from `status_classifier.py`.
- Remove `_KIRO_LOCK_DIR`, `_kiro_session_cwd`, and the `.lock` sidecar scan from `presence.py`; update `_KIRO_PROVIDERS` to `frozenset({"kiro-cli-v3"})` only.
- Remove the `trust_all_tools` → `"kiro-cli"` provider_settings migration from `config.py`; add `"kiro-cli"` to the provider_settings cleanup so existing entries are dropped on next save.
- Remove `"kiro-cli"` entries from `launcher.py` provider dicts and the `else: # kiro-cli` branch of `_build_provider_args`; update `launch_session`/`launch_batch` default `provider` to `"kiro-cli-v3"`.
- Update `data.py`: remove `data_kiro` import, `SESSION_DIR` re-export, legacy `discover_workspaces()` wrapper; change `SessionCache` method defaults from `"kiro-cli"` to `"kiro-cli-v3"`; remove `"kiro-cli"` from `PROVIDERS`.
- Update `web.py` hardcoded fallback provider set `{"kiro-cli", "claude-code", "kiro-cli-v3"}` → `{"claude-code", "kiro-cli-v3"}` (lines 464, 481).
- Update `api_session_transcript` default `provider` from `"kiro-cli"` to `"kiro-cli-v3"`.
- Delete v2-exercising tests in `test_data.py` and v2-specific tests in `test_launcher.py`.
- Delete `TestAcpLockPreflight` and the v2 regression test in `TestAcpSessionsForWorkspaceV3` from `test_web.py`.
- Update `README.md`: remove `[provider_settings.kiro-cli]` config block; remove "scanned separately from v2" note.
- Preserve v2 knowledge in the playbook or `docs/KNOWLEDGE.md` (note only, not a blocker).

**Not in scope:**
- kiro-cli v3 (`"kiro-cli-v3"`) — fully intact.
- Claude Code, Kiro IDE providers — untouched.
- ACP session content, protocol handling, supervisor logic — untouched (except removing the dead `data_kiro` import).
- Any change to `~/.kiro/sessions/cli/` on disk — PowerAtlas stops reading it; the directory is not deleted.

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

### 4. Existing patterns & constraints

- **Provider registry pattern** (`data.py:133-138`): `PROVIDERS` dict is the single point of
  registration. Removing `"kiro-cli"` from it propagates automatically to `available_providers()`,
  `discover_workspaces_with_counts()`, `warmup_all()`, and the dashboard `api_dashboard_sessions`
  provider filtering — no other changes needed there.
- **Provider dict pattern** (`web.py:85-110`, `launcher.py:81-101`): each provider has matching
  entries in `PROVIDER_COLORS`, `PROVIDER_DISPLAY_NAMES`, `PROVIDER_BADGES`,
  `_PROVIDER_BINARY_DISPLAY` (web.py) and `_PROVIDER_DISPLAY`, `_PROVIDER_BINARY`,
  `_PROVIDER_TERMINAL` (launcher.py). All need the `"kiro-cli"` entry removed.
- **Unknown-key preservation** (`config.py`): `load_config`/`save_config` already preserve
  unknown top-level keys. `provider_settings` is a `dict[str, dict]` — removing `"kiro-cli"`
  from the code means an existing `[provider_settings.kiro-cli]` in the user's config is not
  automatically cleaned. Must explicitly add `"kiro-cli"` to a drop-on-save list or strip it in
  `load_config`.
- **Test isolation** (`tests/test_data.py:23-29`): `mock_sessions` fixture patches
  `power_atlas.data_kiro.SESSION_DIR` — this fixture and all classes depending on it go away.
  `_reset_kiro_caches()` at line 152 resets 6 `data_kiro` module-level caches — also deleted.
- **acp.py isolation boundary**: the module header declares it imports exactly two names from
  the rest of the package (`config.CONFIG_DIR`, `launcher._SESSION_ID_RE`). Removing the
  `from . import data_kiro` import brings it to exactly two, satisfying the stated invariant.
  The plan exit criterion that greps for module names still holds.
- **v2-store docstring references in `acp.py`** (lines 1265, 1430, 2764): these reference
  `data_kiro.get_tool_diffs()` in docstrings. The actual runtime call at load_session uses
  `_get_tool_diffs_v3`. The docstrings must be updated to refer to `_get_tool_diffs_v3` only.
- **`_acp_sessions_for_workspace` single call site** (`web.py:3173`): called with
  `include_v3=True` only. Inlining replaces it with
  `data_kiro_v3.load_sessions(cwd)` → list of `Session` objects → extract `.session_id`.
- **Presence sidecar scan** (`presence.py:358`): `_list_sidecars(_KIRO_LOCK_DIR, ".lock")`
  currently enumerates v2 AND v3 lock files, then routes by `sess_`-prefix. After removal the
  whole kiro `.lock` scan goes. kiro-cli-v3 liveness detection for the process table still
  works via `_PROVIDER_SPECS["kiro-cli-v3"]` binary-name match.

### 5. Risks & mitigations

- **Pinned v2 session IDs in `config.toml`**: `pinned_sessions` is a `list[str]`. If a user
  pinned a v2 session ID, after removal `_find_pinned_session_workspace` iterates all providers
  and finds nothing — the session silently disappears from the pinned rail. Acceptable: the user
  (sole user) is aware v2 history vanishes.
- **`test_web.py` line 5634 asserts `"kiro-cli" in message`**: this checks an ACP error
  message that says "kiro-cli" as the binary name — unchanged by this removal. Test keeps passing.
- **`web.py` `_workspace_status` hardcoded fallback** (`{"kiro-cli", "claude-code", "kiro-cli-v3"}`
  at lines 464, 481): must be updated to remove `"kiro-cli"` or the code tries to read sessions
  for a provider that no longer exists in `data.PROVIDERS` (would return empty, not crash, but is dead code).
- **README config sample** (`[provider_settings.kiro-cli]` at line 130): actively misleading
  if left; update as part of this work.

### 6. Resolved decisions

- Q1: Should historical v2 sessions be preserved read-only or removed completely? — A: complete removal (option a). — Decision: `data_kiro.py` deleted; v2 sessions vanish from dashboard.
- Q2: What happens to `[provider_settings.kiro-cli]` in existing `config.toml`? — A: drop it on next save (option b). — Decision: strip `"kiro-cli"` from `provider_settings` in `load_config`; remove the `trust_all_tools` migration.
- Q3: Should `_acp_sessions_for_workspace` be simplified or deleted? — A: deleted and inlined (option b). — Decision: function deleted; its one call site gets `data_kiro_v3.load_sessions(cwd)` inline.
- Q4: Should the presence `.lock` sidecar scan be removed entirely? — A: remove (option a). — Decision: `_KIRO_LOCK_DIR`, `_kiro_session_cwd`, and the `.lock` branch in `_sidecar_records` removed from `presence.py`.
- Q5: Update README? — A: yes (option a). — Decision: remove `[provider_settings.kiro-cli]` block and v2 references from README.
- Q6: What happens to `test_data.py` v2 tests? — A: deleted (option a). — Decision: remove v2-exercising tests; v3 adapter has its own test file.

### 7. Open items

- Identify the exact set of test methods in `test_data.py` that depend solely on `data_kiro`
  vs. those that test the provider-agnostic `data.py` layer (and would remain valid with the
  default provider changed to `"kiro-cli-v3"`). The latter should be kept and updated, not deleted.
- Confirm whether `docs/KNOWLEDGE.md` or a plans archive entry is the right home for the
  v2 store format notes the user asked to preserve.

### 8. Recommended approach

**Phase 1 — data layer** (`data_kiro.py`, `data.py`):
Delete `data_kiro.py`. In `data.py`: remove the `data_kiro` import, `"kiro-cli"` from `PROVIDERS`,
`SESSION_DIR` re-export, `discover_workspaces()` legacy wrapper; update `SessionCache` method
defaults to `"kiro-cli-v3"`.

**Phase 2 — config** (`config.py`):
Remove `trust_all_tools` migration. Add `"kiro-cli"` to a drop-on-save strip in `load_config`
(remove it from `provider_settings` if present). Remove `trust_all_tools` from `_LEGACY_KEYS`
or leave it (harmless — it's already dropped on save).

**Phase 3 — presence** (`presence.py`):
Remove `_KIRO_LOCK_DIR`, `_kiro_session_cwd`, the `.lock` branch in `_sidecar_records`.
Update `_KIRO_PROVIDERS` to `frozenset({"kiro-cli-v3"})`.

**Phase 4 — status classifier** (`status_classifier.py`):
Remove `SESSION_DIR` constant, `classify_kiro_v2()`, the `provider == "kiro-cli"` branch in
`_resolve_jsonl_path_uncached`, and the `"kiro-cli"` path in `_classify_from_path`.

**Phase 5 — acp** (`acp.py`):
Remove `from . import data_kiro`. Remove `KIRO_SESSION_DIR`, `_lock_holder`, `_stored_session_cwd`,
`_load_session_cwd` (v2 path only). Update docstrings at lines 1265, 1430, 2764 to remove
`data_kiro.get_tool_diffs()` references.

**Phase 6 — web.py**:
Remove `"kiro-cli"` entries from `PROVIDER_COLORS`, `PROVIDER_DISPLAY_NAMES`, `PROVIDER_BADGES`,
`_PROVIDER_BINARY_DISPLAY`. Remove `_ACP_LISTING_PROVIDER`, `_ACP_SESSION_SUFFIXES`,
`_ACP_DELETE_STAGING`, `_ACP_SHARING_VIOLATION`. Remove v2 branch of `_acp_delete_session`
(keep v3 dispatch only). Delete `_acp_session_paths`. Delete `_acp_sessions_for_workspace`;
inline `data_kiro_v3.load_sessions(cwd)` at web.py:3173. Remove `touched_v2` set and
`data.session_cache.forget(cwd, _ACP_LISTING_PROVIDER)` from `api_acp_delete_sessions`.
Update fallback provider sets at lines 464/481. Update `api_session_transcript` default provider.

**Phase 7 — launcher** (`launcher.py`):
Remove `"kiro-cli"` from `_PROVIDER_DISPLAY`, `_PROVIDER_BINARY`, `_PROVIDER_TERMINAL`.
Remove `else: # kiro-cli` branch from `_build_provider_args`. Update `launch_session` and
`launch_batch` default `provider` to `"kiro-cli-v3"`.

**Phase 8 — tests**:
Delete v2-exercising tests from `test_data.py` (keep any provider-agnostic tests after
updating their defaults). Delete v2-specific tests from `test_launcher.py`. Delete
`TestAcpLockPreflight` and the v2 regression in `TestAcpSessionsForWorkspaceV3` from `test_web.py`.
Delete the `test_trust_all_tools_migration` tests from `test_config.py`.

**Phase 9 — docs**:
Update `README.md`: remove `[provider_settings.kiro-cli]` section, update the "scanned
separately from v2" note. Optionally update `docs/KNOWLEDGE.md` to preserve v2 store format notes.

Run the full test suite after Phase 8.

### 9. QA environment

- Run: `.venv-PowerAtlas\Scripts\python -m pytest tests/ -x`
- JS page tests: `node tests/acp_page.test.mjs`
- Manual: launch PowerAtlas, confirm no "kiro-cli" provider tile in dashboard, confirm `/api/available-providers` returns no `"kiro-cli"`.

### Assumptions (unconfirmed)

- The exact boundary between "v2-only" tests and "provider-agnostic" tests in `test_data.py` (Open Item 1) is assumed solvable by reading the test file per-class during Phase 8. No upstream blocker.
- `docs/KNOWLEDGE.md` is assumed to be an acceptable location for v2 store format notes. No governance blocker.
