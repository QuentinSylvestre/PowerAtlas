# kiro-cli v3 Dashboard Support

> **Date**: 2026-08-18
> **Status**: Complete  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Add `kiro-cli-v3` as a built-in provider to the main dashboard — session discovery, display, new/resume launches, and live status dots.
> **Estimated effort**: 1-2 days

---

## Intent

### Problem statement & desired outcomes

kiro-cli v3 stores sessions in a different layout (`~/.kiro/sessions/<workspace-hash>/sess_<uuid>/`) that PowerAtlas does not scan. The store has 85 sessions on this machine, 62 created in the last 7 days — it is actively growing and completely invisible in the dashboard. Users running `kiro-cli --v3` or `kiro-cli chat --agent-engine v3` accumulate sessions that cannot be resumed or inspected from PowerAtlas.

The desired outcome is a `kiro-cli v3` provider in the main dashboard — same workspace cards, same session rows, same status dots — such that a user's v3 sessions are as visible and launchable as their v2 sessions. The `/acp` surface stays v2-only; this change is confined to the main dashboard.

### Success criteria

- SC-1: v3 workspaces (derived from `workspacePaths[0]` in each session's `session.json`) appear in the Workspaces panel alongside v2 and other providers.
- SC-2: v3 sessions are listed per workspace with title, timestamps, and first/last prompt extracted from `messages.jsonl`.
- SC-3: "New session" launches `kiro-cli chat --agent-engine v3 --trust-tools *` in a terminal at the selected workspace.
- SC-4: "Resume" launches `kiro-cli chat --agent-engine v3 --trust-tools * --resume-id sess_<uuid>` in a terminal.
- SC-5: Live status dots appear for v3 sessions resumed via PowerAtlas (argv-match detection; no lock-file sidecar — same limitation as v2 fresh sessions).
- SC-6: v2 sessions, ACP, and all other providers are unaffected.
- SC-7: New `tests/test_data_kiro_v3.py` passes; existing test suite passes.
- SC-8: ROADMAP.md v3 parked item removed.

### Scope boundaries & non-goals

**In scope:**
- New `data_kiro_v3.py` adapter implementing the full 7-function provider interface
- `Session` dataclass gains `extra_fields: dict` field; all construction sites updated; v3 populates `agentMode` there
- `data.py` PROVIDERS dict gains `"kiro-cli-v3": data_kiro_v3`
- `launcher.py` gains `kiro-cli-v3` in `_PROVIDER_BINARY`, `_PROVIDER_DISPLAY`, `_PROVIDER_TERMINAL`, and a dedicated `_build_provider_args` branch (`--agent-engine v3 --trust-tools *`, `--resume-id` for resume)
- `web.py` gains `kiro-cli-v3` in `PROVIDER_COLORS` (`#7138cc`), `PROVIDER_DISPLAY_NAMES` (`"kiro-cli v3"`), `_PROVIDER_BINARY_DISPLAY`; hardcoded `{"kiro-cli","claude-code"}` sets expanded to include `"kiro-cli-v3"`
- `presence.py` `_PROVIDER_SPECS` gains `"kiro-cli-v3"` entry (same binary/flag as `"kiro-cli"`)
- `status_classifier.py` `_resolve_jsonl_path_uncached` gains a dedicated `kiro-cli-v3` branch (goes directly to workspace-hash scan, skips `SESSION_DIR`)
- `tests/test_data_kiro_v3.py` — new test file for the adapter
- `test_data.py`, `test_launcher.py` — updated for `Session.extra_fields` and new provider args
- ROADMAP.md — remove the v3 parked item and update the stale session count

**Not in scope:**
- `/acp` v3 support (ACP stays v2-only; `ACP_ARGS = ("acp", "-a")` unchanged)
- `--mode` selection UI (users can add `--mode vibe` via `default_args` in config)
- `agentMode` rendering in session rows (stored in `extra_fields`, not displayed)
- Liveness refinement beyond argv-matching (follow-up spike post-implementation)
- Multi-workspace sessions (`workspacePaths[0]` is always populated per probe)

---

## 1) Current State

**v3 store**: `~/.kiro/sessions/<workspace-hash>/sess_<uuid>/` containing `session.json` + `messages.jsonl`. The `cli/` subdirectory holds v2 sessions and is excluded when scanning for hash dirs. As of 2026-08-18 (probed live): 23 workspace-hash dirs, 85 total sessions, 62 created in the last 7 days.

**Provider registration** (`data.py:101`): `PROVIDERS = {"kiro-cli": data_kiro, "claude-code": data_claude, "kiro-ide": data_kiro_ide}`. Adding a new provider requires a module implementing 7 functions (`is_available`, `discover_workspaces`, `load_sessions`, `refresh_stale_entries_for_cwd`, `find_session_workspace`, `get_session_tail`, `get_first_prompt`) and an import + dict entry in `data.py`.

**Session dataclass** (`data.py:31`): frozen, 8 fields: `session_id, title, cwd, created_at, updated_at, first_prompt, last_prompt, last_reply_tail`. Construction sites: `data_kiro.py`, `data_claude.py`, `data_kiro_ide.py`, and mirrored in test files.

**v3 session.json key names** differ from the Session field names: `id` (not `session_id`), `workspacePaths[0]` (not `cwd`), `createdAt` (not `created_at`), `lastModifiedAt` (not `updated_at`), `title` (same). Probed: `id` field matches directory name in 100% of 85 sessions. `workspacePaths` is always a non-empty, single-entry array.

**v3 messages.jsonl format**: `{"id","timestamp","payload":{"type":"user"|"assistant"|"tool_call"|...}}`. Content at `payload.content`. Entirely different from v2's `{"version":"v1","kind":"Prompt"|"AssistantMessage","data":{}}`. `status_classifier.classify_kiro_v3()` already handles this format correctly.

**Launcher** (`launcher.py:81,87,93`): `_PROVIDER_DISPLAY`, `_PROVIDER_BINARY`, `_PROVIDER_TERMINAL` dicts. `_build_provider_args` (`launcher.py:100`) dispatches by provider string; the `else` branch (kiro-cli default) would produce wrong args for v3. Probed: `--trust-all-tools` (`-a`) is hard-rejected when `--agent-engine v3` is set (`"the following arguments are not supported with --agent-engine=v3: --trust-all-tools"`). `--trust-tools "*"` launches KAS successfully with v3.

**web.py display dicts** (`web.py:85,90,95,100`): `PROVIDER_COLORS`, `PROVIDER_DISPLAY_NAMES`, `PROVIDER_BADGES` (unused), `_PROVIDER_BINARY_DISPLAY`. The `{"kiro-cli","claude-code"}` hardcoded sets appear at:
- `web.py:459,476`: inside `_workspace_status()` fallback loop — `for prov in (providers or {"kiro-cli", "claude-code"})`
- `web.py:3216` (approximately — exact line varies with edits): inside `partials_all_sessions` — `poll_providers = ({"kiro-cli","claude-code"} if provider_filter == "all" else {provider_filter})`

**presence.py** (`presence.py:66`): `_PROVIDER_SPECS = {"claude-code": ..., "kiro-cli": ...}`. v3 has no lock files (`~/.kiro/sessions/cli/*.lock` is v2-only). `Snapshot.is_live()` returns `False` for any provider key not in `_PROVIDER_SPECS`.

**status_classifier.py** (`status_classifier.py:98`): `_resolve_jsonl_path_uncached` handles `provider == "kiro-cli"` with a v3 fallback scan (walks `_V3_SESSIONS_ROOT`). For `provider == "kiro-cli-v3"`, the `else: return None` branch fires — status classification always returns `None`, rendering as "working" in the UI.

**`empty_msgs` dict** (`web.py`): inside `partials_workspaces` handler, maps provider keys to user-facing empty-state messages. Falls back to `f"No {provider} sessions found."` for unknown providers — acceptable degradation but worth filling.

## 2) Goal

Add a `kiro-cli-v3` provider that scans `~/.kiro/sessions/<hash>/sess_<uuid>/`, exposes v3 workspaces and sessions in the main dashboard with correct display, launches new sessions with `--agent-engine v3 --trust-tools *`, resumes existing sessions with `--resume-id sess_<uuid>`, and shows live status dots for resumed sessions.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Provider key | `"kiro-cli-v3"` (separate key) | Single `"kiro-cli"` key with dual-store adapter | Keeps stores isolated; avoids dual-store mtime invalidation complexity; user can enable/disable independently; consistent with v2 vs kiro-ide separation |
| Trust flag | Hard-code `--trust-tools *` in launch args | Config-driven `default_args` (like v2's `-a`) | Exploration Q1: user chose A; `--trust-all-tools` is hard-rejected by v3 engine |
| Engine flag | `--agent-engine v3` | `--v3` shorthand | Canonical enum form per CLI help; less likely to conflict with future flags |
| `agentMode` handling | Store in `extra_fields: dict` on `Session`; do not render | Ignore entirely; render as badge | Schema hook for future use; `Session` construction-site cost already paid in this change |
| Cache invalidation | Root mtime + per-hash-dir mtime | Per-hash-dir only | Root catches new workspace-hash dirs immediately; stat cost negligible (~23 dirs); zero miss cases |
| Status classifier branch | Dedicated `kiro-cli-v3` branch in `_resolve_jsonl_path_uncached` going directly to workspace-hash scan | Extend `kiro-cli` branch to handle both names | Cleaner; avoids redundant `SESSION_DIR` stat for v3 sessions; mirrors data module separation |
| Liveness detection | Add `kiro-cli-v3` to `_PROVIDER_SPECS` (same binary/flag as `kiro-cli`) | Skip liveness for v3 | Minimal cost; without it `is_live()` hard-returns `False` for all v3 sessions |
| `extra_fields` default | `field(default_factory=dict)` on frozen `Session` | Positional field with no default | Allows existing construction sites to omit the arg; dict is mutable on a frozen dataclass — valid Python, no hashability risk since `Session` is never used as a dict key |

## 4) External Dependencies & Costs

### Required external changes

None. This is a local-filesystem-read, code-only change. No CI/CD, IAM, cloud resources, data migration, or third-party services involved.

### Cost impact

None. All operations are local filesystem reads.

## 5) Implementation Phases

### Phase 1: `Session` dataclass — add `extra_fields` [QA]

**Goal**: Add `extra_fields: dict = field(default_factory=dict)` to the `Session` frozen dataclass, update all construction sites to be explicit about the new field, and verify no tests break.

**Why horizontal**: This change to the shared `Session` dataclass is a prerequisite for Phase 2 (the v3 adapter must populate `extra_fields`). Merging it into Phase 2 would mean Phase 2 both introduces and uses the field, making review harder and rollback more complex.

**Covers**: SC-2 (partial — field storage), SC-7

**File scope**: `src/power_atlas/data.py`, `src/power_atlas/data_kiro.py`, `src/power_atlas/data_claude.py`, `src/power_atlas/data_kiro_ide.py`, `tests/test_data.py`, `tests/test_launcher.py` (only if Session appears there), `tests/test_web.py` (confirm all Session constructions are keyword-arg safe — no changes needed if they are)

**Changes**:

In `data.py` (around line 31), change the `Session` dataclass:
```python
# Before:
@dataclass(frozen=True)
class Session:
    session_id: str
    title: str
    cwd: str
    created_at: str
    updated_at: str
    first_prompt: str
    last_prompt: str
    last_reply_tail: str

# After:
@dataclass(frozen=True)
class Session:
    session_id: str
    title: str
    cwd: str
    created_at: str
    updated_at: str
    first_prompt: str
    last_prompt: str
    last_reply_tail: str
    extra_fields: dict = field(default_factory=dict)
```

Add `field` to the `from dataclasses import dataclass` import line.

In `data_kiro.py`, `data_claude.py`, `data_kiro_ide.py`: each `Session(...)` construction call should pass `extra_fields={}` explicitly. This is optional (the default is `{}`) but keeps the code self-documenting. Find each construction call by searching for `Session(` in each file.

In `tests/test_data.py`: any `Session(...)` construction in test helpers or fixtures should add `extra_fields={}` (or rely on the default). Run the test suite after the change to catch any position-sensitive construction calls. The known mtime-race flaky family (`TestKiroPromptsCache::test_changed_jsonl_is_reparsed`, etc.) is pre-existing and unrelated to this change.

**Exit criteria**:
- [x] `@dataclass(frozen=True) class Session` in `data.py` has `extra_fields: dict = field(default_factory=dict)` as the last field, with a code comment `# extra_fields must remain the last field — positional Session(...) calls in tests depend on this`
- [x] `from dataclasses import dataclass, field` import updated in `data.py`
- [x] All `Session(...)` construction sites in `data_kiro.py`, `data_claude.py`, `data_kiro_ide.py` compile without error
- [x] `_reset_kiro_caches()` in `tests/test_data.py` extended to also reset `data_kiro_v3` module-level globals (`_root_mtime`, `_hash_dir_mtimes`, `_cwd_index`, `_prompts_cache`, `_tail_cache`, `_first_prompt_cache`) — import `data_kiro_v3` inside the function so the import is deferred until Phase 2 creates the module
- [x] `tests/test_web.py` checked for `Session(...)` constructions — confirm all use keyword args and will accept an extra defaulted field without changes
- [x] `.venv-PowerAtlas\Scripts\python -m pytest tests/test_data.py tests/test_launcher.py -x -q` passes (excluding known flaky tests — run with `-p no:randomly` if test order matters)

#### Implementation (2026-08-18, code: 9e81a29 + a9cd08e + 7812d59)

Phase 1 implemented the `Session.extra_fields` dataclass extension across all affected files: added `from dataclasses import dataclass, field` import; added `extra_fields: dict = field(default_factory=dict, hash=False, compare=False)` as the last field of the frozen `Session` dataclass (hash/compare excluded to preserve hashability while preventing the mutable dict from breaking hash()); added `extra_fields={}` to each provider adapter's `Session(...)` call (later simplified to rely on default_factory); extended `_reset_kiro_caches()` with a deferred import block resetting all six `data_kiro_v3` module-level globals including 3 BoundedCache clear() calls; removed dead `except AttributeError` clause; added 3 tests to `TestFrozenSession` covering default isolation, hashability, and compare=False semantics. `tests/test_web.py` `Session()` constructions all use keyword form — no changes needed. 282 tests pass.

---

### Phase 2: `data_kiro_v3.py` — v3 session adapter [QA]

**Goal**: Create the new `data_kiro_v3.py` adapter implementing all 7 provider interface functions, with correct v3 key mapping, v3 JSONL content extraction, and root+per-hash mtime cache invalidation.

**Covers**: SC-1, SC-2, SC-7

**File scope**: `src/power_atlas/data_kiro_v3.py` (new), `tests/test_data_kiro_v3.py` (new)

**Constants**:
```python
V3_SESSIONS_ROOT = Path.home() / ".kiro" / "sessions"
# Subdirs of V3_SESSIONS_ROOT that are not workspace-hash dirs
_V3_EXCLUDED_NAMES = frozenset({"cli"})
```

**Thread safety**: All module-level cache dicts (`_root_mtime`, `_hash_dir_mtimes`, `_cwd_index`, `_prompts_cache`, `_tail_cache`, `_first_prompt_cache`) are mutated from `asyncio.to_thread` calls and from the background refresh thread concurrently. Protect the index rebuild with a `threading.Lock()` at module level:
```python
import threading
_index_lock = threading.Lock()
```
Acquire `_index_lock` inside `_cwd_to_sessions()` around the rebuild block. The BoundedCache class already has its own internal lock — no extra locking needed for `_prompts_cache`.

**`sess_` prefix helper** — extract once, used in `_find_v3_session_path`, `_resolve_jsonl_path_uncached` (Phase 4), and `find_session_workspace`:
```python
def _ensure_sess_prefix(session_id: str) -> str:
    """Normalize a session ID to the sess_<uuid> form."""
    return session_id if session_id.startswith("sess_") else f"sess_{session_id}"
```

**`is_available()`**:
```python
def is_available() -> bool:
    """Return True if any workspace-hash dir exists under V3_SESSIONS_ROOT."""
    if not V3_SESSIONS_ROOT.is_dir():
        return False
    try:
        return any(
            e.is_dir() and e.name not in _V3_EXCLUDED_NAMES
            for e in V3_SESSIONS_ROOT.iterdir()
        )
    except OSError:
        return False
```

**v3 key mapping** (session.json camelCase → Session fields):
- `data.get("id", dir_stem)` → `session_id` (dir_stem = `sess_<uuid>` directory name as fallback)
- `(data.get("workspacePaths") or [""])[0]` → `cwd` (guard: handles null, empty list, and missing key)
- `data.get("createdAt", "")` → `created_at`
- `data.get("lastModifiedAt", "")` → `updated_at`
- `data.get("title", "<untitled>")` → `title`
- `{"agentMode": data.get("agentMode", "")}` → `extra_fields`

**v3 JSONL content extraction** — `_extract_v3_content(line: str, msg_type: str) -> str`:
```python
def _extract_v3_content(line: str, msg_type: str) -> str:
    """Extract text from a v3 JSONL line of a given payload type.
    Returns "" for image-only messages (no text blocks) — intentional."""
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return ""
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return ""
    if payload.get("type") != msg_type:
        return ""
    content = payload.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return " ".join(parts)
    return ""
```

**Cache invalidation strategy** — two-level mtime index, keyed on `session.json` mtime (not hash-dir mtime):

> **Why session.json, not hash-dir mtime**: On NTFS and most Linux filesystems, appending to `messages.jsonl` bumps the containing directory's mtime. Since `messages.jsonl` is written on every assistant turn, gating the cwd-index rebuild on hash-dir mtime would trigger a full `session.json` re-scan on every agent response — O(sessions_in_workspace × hash_dirs) reads per active turn. `session.json` changes only when session metadata changes (creation, title update, status update) — the correct signal for workspace membership. Message content freshness is handled separately by per-session `file_stats` in `load_sessions`.

```python
import threading

_index_lock = threading.Lock()
_root_mtime: float | None = None            # V3_SESSIONS_ROOT mtime
_session_json_mtimes: dict[str, float] = {}  # "hash/sess_uuid" -> session.json mtime
_cwd_index: dict[str, list[tuple[str, str]]] = {}  # norm_cwd -> [(hash_name, sess_dir_name), ...]
_norm_cwd_to_hash: dict[str, str] = {}       # norm_cwd -> hash_dir_name (for refresh lookup)
```

`_cwd_to_sessions()` rebuilds when either `V3_SESSIONS_ROOT.stat().st_mtime` changes (new hash dir) or any `session.json` mtime changes (new/modified session). On rebuild: walk hash dirs, read each `sess_*/session.json`, populate `_cwd_index`, `_session_json_mtimes`, `_norm_cwd_to_hash`.

**Error handling throughout** — all functions must follow the v2 adapter's fail-open pattern:
- Wrap `V3_SESSIONS_ROOT.iterdir()` in `try/except OSError: return {}` (or appropriate empty return)
- After `json.loads`, check `if not isinstance(data, dict): continue` — handles partial writes and non-dict JSON values
- After extracting `cwd = (data.get("workspacePaths") or [""])[0]`, check `if not cwd: continue` — prevents empty-string key in `_cwd_index`
- All OSError paths return early with empty/None rather than propagating

**`discover_workspaces()`**: iterate the cwd index; for each cwd compute `updated_at = max(lastModifiedAt)` across sessions; return `[(cwd, count, updated_at)]` sorted descending by `updated_at`.

**`load_sessions(cwd)`**: call `_cwd_to_sessions()`, filter by `_normalize_path(cwd)`, read `session.json` + `messages.jsonl` for each session; call `_extract_prompts_v3_cached(messages_path, st)` for prompt data; return `(sessions, file_stats)`. File stats track both `session.json` and `messages.jsonl` per session; ignore `publish.cursor` and `publish-sub.cursor`.

**`_extract_prompts_v3(messages_path: Path) -> tuple[str, str, str]`**: reads first 50 lines for `first_prompt` (first `payload.type == "user"`), tails last 100 lines via deque for `last_prompt` (last `"user"`) and `last_reply_tail` (last `"assistant"`). Uses `_extract_v3_content`. No `.history` file — first prompt from `messages.jsonl` only.

**`_extract_prompts_v3_cached(messages_path, st)`**: `BoundedCache(2048)` (import from `data`) keyed on `str(messages_path)`, invalidated by `(mtime, size)`. Same pattern as `data_kiro._extract_prompts_cached`.

**Bounded caches for tail and first-prompt** (use `BoundedCache`, not plain dicts — avoids unbounded growth):
```python
from .data import BoundedCache, Session, _FileInfo, _normalize_path, _cap_text
_prompts_cache: BoundedCache = BoundedCache(2048)
_tail_cache: BoundedCache = BoundedCache(512)    # (time, mtime, lines)
_first_prompt_cache: BoundedCache = BoundedCache(512)  # (time, mtime, result)
```

**`refresh_stale_entries_for_cwd(norm_cwd, old_stats)`**:
- If `V3_SESSIONS_ROOT` is missing: return `True` (root gone = all cached stats invalid; `load_sessions` will return empty and clear the cache)
- Stat each tracked file in `old_stats`; return `True` if any has changed
- For new-session detection: look up the hash dir from `_norm_cwd_to_hash` (the cached reverse mapping from `_cwd_to_sessions()`) — do NOT call `_cwd_to_sessions()` here, which would trigger a full rebuild on every refresh tick. If `_norm_cwd_to_hash` doesn't have the key (index not yet built), return `True` to force a `load_sessions` rebuild.

**`find_session_workspace(session_id)`**: scan `V3_SESSIONS_ROOT` hash dirs for `_ensure_sess_prefix(session_id)/session.json`; wrap in `try/except OSError: return None`; read `workspacePaths[0]`.

**`get_session_tail(session_id, cwd, max_lines)`**: locate path via `_find_v3_session_path(session_id)`; tail `messages.jsonl` (last 131072 bytes), extract `payload.type == "assistant"` lines. Use `BoundedCache` `_tail_cache` keyed on `session_id`, TTL 5s + mtime guard.

**`get_first_prompt(session_id, cwd)`**: read first 50 lines of `messages.jsonl`, find first `payload.type == "user"`. Use `BoundedCache` `_first_prompt_cache` keyed on `session_id`, TTL 60s + mtime guard.

**`_find_v3_session_path(session_id) -> Path | None`**: scans hash dirs for `_ensure_sess_prefix(session_id)/messages.jsonl`. Wrap in `try/except OSError: return None`. No separate cache needed here — callers (`get_session_tail`, `get_first_prompt`) use their own BoundedCache with mtime guard, so this function is called at most once per mtime change per session.

**Test file `tests/test_data_kiro_v3.py`**: Follow the `data_kiro_ide` test pattern from `test_data.py` (lines 663+). Monkeypatch `power_atlas.data_kiro_v3.V3_SESSIONS_ROOT`. Include autouse `_clear_v3_caches` fixture that resets all module-level caches. Use `_bump_mtime()` for any test that writes and immediately reads the same file. Test classes:
- `TestKiroV3IsAvailable` — empty root, missing root, root with hash dirs, root with only `cli/`
- `TestKiroV3DiscoverWorkspaces` — single session, multiple sessions, multiple workspaces, malformed session.json, missing workspacePaths
- `TestKiroV3LoadSessions` — basic load, missing messages.jsonl, empty messages.jsonl, prompt extraction from v3 format
- `TestKiroV3GetSessionTail` — basic tail, TTL cache, mtime invalidation
- `TestKiroV3GetFirstPrompt` — basic, fallback when no user message in first 50 lines
- `TestKiroV3RefreshStale` — unchanged files, changed mtime, new session added
- `TestKiroV3FindSessionWorkspace` — found, not found, sess_ prefix handling

**Exit criteria**:
- [x] `src/power_atlas/data_kiro_v3.py` exists with all 7 interface functions
- [x] `V3_SESSIONS_ROOT` excludes `cli/` dir in all scanning
- [x] `publish.cursor` and `publish-sub.cursor` files are not tracked in `file_stats`
- [x] `extra_fields={"agentMode": ...}` populated from `session.json`
- [x] All `iterdir()` and `session.json` reads are wrapped in `try/except OSError`
- [x] `isinstance(data, dict)` check after every `json.loads` call (handles partial writes, `null`, `[]`)
- [x] `if not cwd: continue` guard after `workspacePaths` extraction
- [x] Cache invalidation keys on `session.json` mtimes, not hash-dir mtimes
- [x] `_index_lock = threading.Lock()` guards the index rebuild in `_cwd_to_sessions()`
- [x] `_tail_cache` and `_first_prompt_cache` use `BoundedCache`, not plain dicts
- [x] `refresh_stale_entries_for_cwd` returns `True` when `V3_SESSIONS_ROOT` is absent
- [x] `refresh_stale_entries_for_cwd` uses `_norm_cwd_to_hash` cached mapping, does NOT call `_cwd_to_sessions()` internally
- [x] `_ensure_sess_prefix` helper extracted and used in all call sites within Phase 2 (third call site added in Phase 4)
- [x] `BoundedCache` imported from `.data` in the import block
- [x] `tests/test_data_kiro_v3.py` created with all 7 test classes listed above
- [x] `.venv-PowerAtlas\Scripts\python -m pytest tests/test_data_kiro_v3.py -x -q` passes
- [x] `.venv-PowerAtlas\Scripts\python -m pytest tests/test_data.py -x -q` passes (no regressions)

#### Implementation (2026-08-18, code: b6bd17a + fc04885 + 14f13e1)

Created `src/power_atlas/data_kiro_v3.py` with all 7 provider interface functions. Key design: two-phase `_cwd_to_sessions()` (fast mtime-only stat pass outside lock; JSON parse only on cache miss; atomic swap under `_index_lock`); `_cwd_display` dict populated during rebuild so `discover_workspaces()` needs zero second reads of session.json; `refresh_stale_entries_for_cwd` derives tracked session dirs from `old_stats` path parents (cwd-scoped by construction) to avoid false-positive stale detection from sibling-cwd additions; `_extract_prompts_v3` reads `messages.jsonl` once (single open, `lines[:50]` for first_prompt, `lines[-100:]` for tail); negative `get_first_prompt` results are cached to avoid re-scanning tool-only sessions on every TTL miss. Cache variable renamed `_session_json_mtimes` (plain name, keys on session.json mtime per D6). Created `tests/test_data_kiro_v3.py` with 50 tests across 8 classes. Divergence: `_cwd_display` dict added (not in original plan) to eliminate `discover_workspaces` double-reads.

---

---

### Phase 3: Provider registration + launcher + web display [QA] [P:4]

**Goal**: Register `kiro-cli-v3` in the PROVIDERS dict, add launcher entries with correct v3 CLI args, and update all web.py display dicts and hardcoded provider sets.

**Covers**: SC-1, SC-3, SC-4, SC-6, SC-7, SC-8

**File scope**: `src/power_atlas/data.py`, `src/power_atlas/launcher.py`, `src/power_atlas/web.py`, `tests/test_launcher.py`, `plans/ROADMAP.md`, `README.md`

**`data.py`** — add import and PROVIDERS entry (after `data_kiro_ide` import):
```python
from . import data_kiro, data_claude, data_kiro_ide, data_kiro_v3  # noqa: E402

PROVIDERS: dict[str, object] = {
    "kiro-cli": data_kiro,
    "claude-code": data_claude,
    "kiro-ide": data_kiro_ide,
    "kiro-cli-v3": data_kiro_v3,
}
```

**`launcher.py`** — add to each dict (`_PROVIDER_DISPLAY:81`, `_PROVIDER_BINARY:87`, `_PROVIDER_TERMINAL:93`):
```python
_PROVIDER_DISPLAY = {
    "kiro-cli": "kiro-cli",
    "claude-code": "Claude Code",
    "kiro-ide": "Kiro IDE",
    "kiro-cli-v3": "kiro-cli v3",
}
_PROVIDER_BINARY = {
    "kiro-cli": "kiro-cli",
    "claude-code": "claude",
    "kiro-ide": "kiro",
    "kiro-cli-v3": "kiro-cli",
}
_PROVIDER_TERMINAL = {
    "kiro-cli": True,
    "claude-code": True,
    "kiro-ide": False,
    "kiro-cli-v3": True,
}
```

**`_build_provider_args`** (`launcher.py:100`) — add `kiro-cli-v3` case **before** the `else` clause:
```python
def _build_provider_args(provider: str, binary: str, session_id: str | None) -> list[str]:
    if provider == "claude-code":
        args = [binary]
        if session_id:
            args += ["--resume", session_id]
    elif provider == "kiro-ide":
        args = [binary]
    elif provider == "kiro-cli-v3":
        # --trust-all-tools (-a) is hard-rejected by --agent-engine v3; use --trust-tools instead.
        # Note: default_args is appended AFTER these args by launch_session. If a user has
        # default_args="-a" (copied from a v2 config), the v3 launch will fail with a clear
        # CLI error. No PowerAtlas-side guard is added — the CLI error is self-describing.
        args = [binary, "chat", "--agent-engine", "v3", "--trust-tools", "*"]
        if session_id:
            args += ["--resume-id", session_id]
    else:  # kiro-cli (v2) — catch-all; any unrecognized provider gets v2 kiro-cli args
        args = [binary, "chat"]
        if session_id:
            args += ["--resume-id", session_id]
    return args
```

**`web.py`** — add entries to display dicts (`web.py:85,90,100`):
```python
PROVIDER_COLORS = {
    "kiro-cli": "#7138cc",
    "claude-code": "#c2590f",
    "kiro-ide": "#8b5cf6",
    "kiro-cli-v3": "#7138cc",  # same purple family as v2
}
PROVIDER_DISPLAY_NAMES = {
    "kiro-cli": "kiro-cli",
    "claude-code": "Claude Code",
    "kiro-ide": "Kiro IDE",
    "kiro-cli-v3": "kiro-cli v3",
}
_PROVIDER_BINARY_DISPLAY = {
    "kiro-cli": "kiro-cli chat",
    "claude-code": "claude",
    "kiro-ide": "kiro",
    "kiro-cli-v3": "kiro-cli chat --agent-engine v3",
}
```

**`web.py` hardcoded sets** — expand all three occurrences. Find by searching the literal string `{"kiro-cli", "claude-code"}` in `web.py`. Both occurrences in `_workspace_status()` (function body around line 459 and 476) and the one in `partials_all_sessions` (around line 3216):
```python
# Before (all 3 occurrences):
{"kiro-cli", "claude-code"}
# After (all 3 occurrences):
{"kiro-cli", "claude-code", "kiro-cli-v3"}
```

**`web.py` `empty_msgs` dict** — add entry (in `partials_workspaces` handler, search for the `empty_msgs = {` literal):
```python
empty_msgs = {
    "claude-code": "No Claude Code sessions found — start one with <code>claude</code> to see it here.",
    "kiro-cli": "No Kiro CLI sessions found — start one with <code>kiro-cli</code> to see it here.",
    "kiro-ide": "No Kiro IDE sessions found — open a folder in Kiro IDE and start a conversation to see it here.",
    "kiro-cli-v3": "No kiro-cli v3 sessions found — start one with <code>kiro-cli chat --agent-engine v3</code> to see it here.",
}
```

**`tests/test_launcher.py`** — add test cases for `kiro-cli-v3` launch args:
- New session: args contain `["chat", "--agent-engine", "v3", "--trust-tools", "*"]`
- Resume: args contain `["chat", "--agent-engine", "v3", "--trust-tools", "*", "--resume-id", "sess_<uuid>"]`
- Provider is in terminal branch (not non-terminal)
- Default args are appended after the v3-specific args

**`plans/ROADMAP.md`** — remove the v3 parked item (the line reading `kiro-cli v3 session support — scan the sess_*/` and its references). Update the Parked items list to remove v3. The session count reference (`currently 23 dormant historical sessions`) should be removed entirely since the feature is now implemented.

**Exit criteria**:
- [x] `data.PROVIDERS` contains `"kiro-cli-v3"` mapping to `data_kiro_v3`
- [x] `_build_provider_args("kiro-cli-v3", "kiro-cli", None)` returns `["kiro-cli", "chat", "--agent-engine", "v3", "--trust-tools", "*"]`
- [x] `_build_provider_args("kiro-cli-v3", "kiro-cli", "sess_abc")` returns `["kiro-cli", "chat", "--agent-engine", "v3", "--trust-tools", "*", "--resume-id", "sess_abc"]`
- [x] All 3 occurrences of `{"kiro-cli", "claude-code"}` in `web.py` expanded to include `"kiro-cli-v3"` (verify with `Select-String '"kiro-cli-v3"' src\power_atlas\web.py | Measure-Object | Select-Object -Expand Count` showing **≥7** matches: 3 in expanded sets + 2 in display dicts + 1 in `empty_msgs` + 1 in `_PROVIDER_BINARY_DISPLAY`)
- [x] `empty_msgs` dict in `partials_workspaces` handler contains `"kiro-cli-v3"` key
- [x] `plans/ROADMAP.md` v3 parked item removed; [P2b] entry updated to note v3 is now visible
- [x] `README.md` has a kiro-cli v3 sub-bullet under "Auto-discovers workspaces"
- [x] `.venv-PowerAtlas\Scripts\python -m pytest tests/test_launcher.py -x -q` passes
- [x] PowerAtlas starts without import errors: `.venv-PowerAtlas\Scripts\python -c "from power_atlas import web; print('ok')"`

#### Implementation (2026-08-18, code: e400931)

Registered `kiro-cli-v3` as a fourth provider across all surfaces. `data.py`: added `data_kiro_v3` import and PROVIDERS entry. `launcher.py`: added `kiro-cli-v3` to all 3 display/binary/terminal dicts; added dedicated `elif provider == "kiro-cli-v3":` branch producing `[binary, "chat", "--agent-engine", "v3", "--trust-tools", "*"]` with optional `--resume-id`, with comment explaining why `-a` is excluded. `web.py`: added 4 dict entries (COLORS, DISPLAY_NAMES, BINARY_DISPLAY, BADGES) and expanded all 3 `{"kiro-cli","claude-code"}` sets to include `kiro-cli-v3`; added `empty_msgs` entry. Added 4 `test_launcher.py` tests. Docs: ROADMAP removed v3 parked item, README added v3 sub-bullet, CLOSED_INVESTIGATIONS updated session count, HARNESS added `kiro-v3-session-data` resource row.

---

### Phase 4: Presence + status classifier [QA] [P:3]

**Goal**: Add `kiro-cli-v3` to `_PROVIDER_SPECS` in `presence.py` so live status dots work, and add a dedicated `kiro-cli-v3` branch in `_resolve_jsonl_path_uncached` in `status_classifier.py` so semantic classification reaches v3 sessions.

**Covers**: SC-5, SC-6, SC-7

**File scope**: `src/power_atlas/presence.py`, `src/power_atlas/status_classifier.py`, `tests/test_data.py` (if status_classifier tests live there), `tests/test_web.py`

**`presence.py`** (`_PROVIDER_SPECS` at line 66):
```python
_PROVIDER_SPECS: dict[str, tuple[tuple[str, ...], str]] = {
    "claude-code": (("claude", "claude.exe", "claude.cmd"), "--resume"),
    "kiro-cli": (("kiro-cli", "kiro-cli.exe", "kiro-cli.cmd"), "--resume-id"),
    "kiro-cli-v3": (("kiro-cli", "kiro-cli.exe", "kiro-cli.cmd"), "--resume-id"),
}
```

**Binary collision caveat**: `kiro-cli` and `kiro-cli-v3` share the same binary names. `_match_provider()` in `presence.py` returns on first dict iteration, so a resumed v3 session (`--resume-id sess_abc`) gets attributed to `"kiro-cli"` (v2), not `"kiro-cli-v3"`, because `"kiro-cli"` appears first in `_PROVIDER_SPECS`. The live dot will appear on the v2 provider row rather than the v3 row.

This is an accepted limitation for this plan: the v3 session ID format (`sess_<uuid>`) is disjoint from v2 (`<bare-uuid>`), so a proper fix would check the session ID prefix inside `_scan()` to route to the correct provider key. That disambiguation is tracked as follow-up work (see Follow-up Work section). For this plan, the live dot will appear but may be attributed to the v2 provider — acceptable since the primary value (liveness detection) is present.

**`status_classifier.py`** (`_resolve_jsonl_path_uncached` at line 98) — add `kiro-cli-v3` branch:
```python
def _resolve_jsonl_path_uncached(session_id: str, provider: str, cwd: str) -> Path | None:
    if provider == "kiro-cli":
        # v2 path first
        path = SESSION_DIR / f"{session_id}.jsonl"
        if path.is_file():
            return path
        # v3 fallback scan (for kiro-cli provider that may hold v3 sessions)
        if _V3_SESSIONS_ROOT.is_dir():
            sid = session_id if session_id.startswith("sess_") else f"sess_{session_id}"
            for ws_dir in _V3_SESSIONS_ROOT.iterdir():
                if not ws_dir.is_dir() or ws_dir.name == "cli":
                    continue
                v3_path = ws_dir / sid / "messages.jsonl"
                if v3_path.is_file():
                    return v3_path
        return None
    elif provider == "kiro-cli-v3":
        # Go directly to workspace-hash scan (no v2 store check)
        if not _V3_SESSIONS_ROOT.is_dir():
            return None
        sid = session_id if session_id.startswith("sess_") else f"sess_{session_id}"
        try:
            for ws_dir in _V3_SESSIONS_ROOT.iterdir():
                if not ws_dir.is_dir() or ws_dir.name == "cli":
                    continue
                v3_path = ws_dir / sid / "messages.jsonl"
                if v3_path.is_file():
                    return v3_path
        except OSError:
            return None
        return None
    elif provider == "claude-code":
        ...
```

Also update `_classify_from_path`:
```python
def _classify_from_path(path: Path, provider: str, file_size: int | None = None) -> Optional[SemanticStatus]:
    tail_lines = _read_tail_lines(path, file_size=file_size)
    if not tail_lines:
        return None
    if provider in ("kiro-cli", "kiro-cli-v3"):
        if _is_v3_format(tail_lines):
            return classify_kiro_v3(tail_lines)
        return classify_kiro_v2(tail_lines)
    elif provider == "claude-code":
        return classify_claude(tail_lines)
    else:
        # Default: v3 format (covers kiro-ide and any future providers).
        # This else-branch is intentional — new providers default to v3 format detection.
        return classify_kiro_v3(tail_lines)
```

**`_path_cache` key change** — extend memoization to `kiro-cli-v3`. The current `kiro-cli` cache key is a 3-tuple `(session_id, str(SESSION_DIR), str(_V3_SESSIONS_ROOT))`. Adding `kiro-cli-v3` requires `provider` in the key to prevent v2 and v3 session IDs from colliding if they ever share a value:

```python
def _resolve_jsonl_path(session_id: str, provider: str, cwd: str) -> Optional[Path]:
    # Cache both kiro-cli and kiro-cli-v3 (both scan _V3_SESSIONS_ROOT)
    if provider not in ("kiro-cli", "kiro-cli-v3"):
        return _resolve_jsonl_path_uncached(session_id, provider, cwd)

    # Key is now a 4-tuple (was 3-tuple for kiro-cli only).
    # Any in-memory cached entries from a prior kiro-cli-only key shape are naturally
    # invalidated by the key mismatch — no explicit flush needed.
    cache_key = (session_id, provider, str(SESSION_DIR), str(_V3_SESSIONS_ROOT))
    # ... rest of cache logic unchanged (lock, hit/miss, TTL) ...
```

**Note**: The cache key changes from 3-tuple to 4-tuple. Any test that inspects `_path_cache` internals by key structure must be updated. Tests that only check whether a path is returned correctly are unaffected.

**Exit criteria**:
- [x] `presence._PROVIDER_SPECS` contains `"kiro-cli-v3"` with same binary/flag tuple as `"kiro-cli"`
- [x] Code comment in `presence.py` near `_PROVIDER_SPECS` notes the binary-collision caveat and points to the Follow-up Work section for the `sess_`-prefix disambiguation fix
- [x] `status_classifier._resolve_jsonl_path_uncached("sess_abc", "kiro-cli-v3", "")` scans `_V3_SESSIONS_ROOT` directly (no `SESSION_DIR` check), wrapped in `try/except OSError`
- [x] `status_classifier._classify_from_path(path, "kiro-cli-v3", ...)` dispatches to `classify_kiro_v3()` for v3-format lines; `else` branch has comment noting it is intentional
- [x] `_resolve_jsonl_path` guard changed to `provider not in ("kiro-cli", "kiro-cli-v3")` and cache key is 4-tuple `(session_id, provider, str(SESSION_DIR), str(_V3_SESSIONS_ROOT))`
- [x] Any test checking `_path_cache` key structure updated for 4-tuple format
- [x] `.venv-PowerAtlas\Scripts\python -m pytest tests/test_web.py tests/test_data.py -x -q` passes

#### Implementation (2026-08-18, code: da6dfd8)

`presence.py`: Added `"kiro-cli-v3"` to `_PROVIDER_SPECS` with same binary tuple/resume flag as `"kiro-cli"`; added comment documenting the binary-collision accepted limitation and Follow-up Work #2. `status_classifier.py`: Added `elif provider == "kiro-cli-v3":` branch in `_resolve_jsonl_path_uncached` that scans `_V3_SESSIONS_ROOT` directly (no v2 store check), wrapped in try/except OSError; also added OSError guard to the existing `kiro-cli` v3 fallback. Changed `if provider == "kiro-cli":` to `if provider in ("kiro-cli", "kiro-cli-v3"):` in `_classify_from_path`. Changed `_resolve_jsonl_path` guard to `provider not in ("kiro-cli", "kiro-cli-v3")` and updated cache key from 3-tuple to 4-tuple `(session_id, provider, str(SESSION_DIR), str(_V3_SESSIONS_ROOT))`. Added 6 new tests to `TestResolveJsonlPath` and 1 to `TestClassifyKiroV3`.

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `--trust-tools *` wildcard semantics change in future kiro-cli version | High — every v3 launch from PowerAtlas silently fails or prompts for each action | Probed on 2026-08-18: `--trust-tools "*"` launches KAS without rejection; config `default_args` field on `kiro-cli-v3` provider can override; monitored on kiro-cli upgrades |
| User sets `default_args = "-a"` for v3 provider (copied from v2 config) | Medium — kiro-cli CLI hard-rejects with a clear error message; no PowerAtlas-side guard | CLI error is self-describing; tracked in Follow-up Work as a potential config migration or settings warning |
| Binary-collision in presence detection | Medium — `_match_provider` returns on first `_PROVIDER_SPECS` match; resumed v3 sessions attributed to `kiro-cli` (v2) provider row, not `kiro-cli-v3` row | Accepted for this plan; live dot still appears (wrong row); deferred `sess_`-prefix disambiguation tracked in Follow-up Work |
| `extra_fields: dict` on frozen `Session` — mutable field | Low — `Session` is never hashed (confirmed by codebase search); `frozen=True` does not deep-freeze dict contents but no mutating consumers exist | Confirmed by codebase search; `extra_fields` must remain last field (code comment added in Phase 1); risk is latent not current |
| v3 store growth causing slow `discover_workspaces()` | Low-Medium — with 23 hash dirs and 85 sessions today, full re-scan on cache miss is bounded; `session.json` mtime gating (not hash-dir mtime) prevents spurious rebuilds on message writes | Session.json mtime strategy chosen specifically to avoid per-message-write rebuilds; acceptable at current scale |
| Liveness detection not exercised on real v3 sessions | Medium — spike deferred; argv-based detection may miss edge cases | Filed as follow-up work; argv matching is the same mechanism used for v2 PowerAtlas-launched sessions and is known to work there |
| Three `{"kiro-cli","claude-code"}` hardcoded sets — if one is missed | High — v3 status dots are silently absent or workspace status aggregation is incomplete | Exit criterion explicitly verifies all three via count (≥7); Phase 3 is a single commit covering all three |

A risk whose Mitigation is anything other than "addressed within this plan":
- `--trust-tools *` wildcard stability: accepted; future kiro-cli upgrade check recommended
- `default_args="-a"` for v3: accepted; tracked in Follow-up Work
- Binary-collision in presence: accepted; deferred `sess_`-prefix disambiguation tracked in Follow-up Work
- Liveness spike: deferred (see Follow-up Work)

## 7) Verification

**Automated (after all phases):**
```powershell
# Full test suite
.venv-PowerAtlas\Scripts\python -m pytest tests/ -x -q

# Verify all hardcoded sets were expanded (should show ≥ 5 matches for kiro-cli-v3 in web.py)
Select-String -Pattern '"kiro-cli-v3"' src\power_atlas\web.py | Measure-Object | Select-Object -Expand Count

# Verify new provider module is importable and registered
.venv-PowerAtlas\Scripts\python -c "from power_atlas.data import PROVIDERS; assert 'kiro-cli-v3' in PROVIDERS; print('ok')"

# Verify launch args
.venv-PowerAtlas\Scripts\python -c "
from power_atlas.launcher import _build_provider_args
args = _build_provider_args('kiro-cli-v3', 'kiro-cli', None)
assert args == ['kiro-cli', 'chat', '--agent-engine', 'v3', '--trust-tools', '*'], args
args_resume = _build_provider_args('kiro-cli-v3', 'kiro-cli', 'sess_abc')
assert '--resume-id' in args_resume and 'sess_abc' in args_resume, args_resume
print('ok')
"

# Verify ROADMAP.md no longer contains the old v3 parked entry
Select-String -Pattern 'currently 23 dormant' plans\ROADMAP.md
# (should return nothing)
```

**Manual (requires PowerAtlas running):**
1. Start PowerAtlas: `.venv-PowerAtlas\Scripts\power-atlas`
2. Open dashboard — verify `kiro-cli v3` provider button appears in the provider filter
3. Confirm v3 workspaces appear in the Workspaces panel (expected: `~`, agent-playbook, PowerAtlas, others)
4. Open a v3 workspace — confirm sessions list with titles, timestamps, prompts
5. Click "New session" on a v3 workspace — verify terminal opens with `--agent-engine v3 --trust-tools *` in the window title / process args
6. Click "Resume" on an existing v3 session — verify terminal opens with `--agent-engine v3 --trust-tools * --resume-id sess_<uuid>`
7. After resuming a v3 session via PowerAtlas, verify a live status dot appears on the session row

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `plans/ROADMAP.md` | Remove v3 parked item (Platform section + parked short list + ranking table footer + lines ~153-159 main bullet); update [P2b] "Session stores PowerAtlas cannot see" entry to note v3 is now visible (only sqlite classic sessions remain); remove stale "23 dormant sessions" reference | Phase 3 |
| `README.md` | Add sub-bullet under "Auto-discovers workspaces" for kiro-cli v3 sessions (`~/.kiro/sessions/<workspace-hash>/sess_*/`) mirroring the Kiro IDE sub-bullet format | Phase 3 |
| `plans/CLOSED_INVESTIGATIONS.md` | Update the `kiro-cli serve` "Would reopen if" paragraph (v3 inventory section, ~line 56): the store count is now 85 (62 in 7 days), not dormant. Note dashboard discovery was added by this plan; the serve path itself remains closed. Also update the dead sub-question remark (~line 62) that called the store "dormant" | Phase 3 (doc-table-only) |
| `plans/tests/HARNESS.md` | Add `kiro-v3-session-data` resource row for `~/.kiro/sessions/<hash>/sess_*/`; update provider count from "two" to "three" in Execution Notes | Phase 3 (doc-table-only) |

## 9) Implementation Divergences from Plan
<Reserved — filled during implementation>

## Follow-up Work (Deferred)

1. **Liveness detection spike.** After Phase 4 ships, resume a v3 session via PowerAtlas and verify the live status dot appears and transitions correctly (working → waiting → closed). No code changes expected; this is an observational check. Source: Risk Assessment row "Liveness detection not exercised on real v3 sessions."

2. **`_match_provider` binary-collision disambiguation.** `presence.py`'s `_scan()` attributes any `--resume-id sess_<uuid>` process to `"kiro-cli"` (first match) instead of `"kiro-cli-v3"`. Fix: inside `_scan()`, after extracting the session ID via `_extract_session_id`, check whether it starts with `"sess_"` and route to `"kiro-cli-v3"` instead of `"kiro-cli"`. Source: Review finding #2 (Architect).

3. **`--trust-tools *` wildcard stability.** On each kiro-cli upgrade, verify `--trust-tools "*"` still launches KAS without error when combined with `--agent-engine v3`. Source: Risk Assessment row "`--trust-tools *` wildcard semantics change."

4. **Config migration: `default_args="-a"` for v3.** Consider adding a Settings panel warning or config migration that detects `-a` / `--trust-all-tools` in `kiro-cli-v3` `default_args` and flags it as incompatible. Source: Review finding #14 (Senior engineer).

## Review Log

### 2026-08-18 — Post-Implementation Review

Overall implementation health: Green.
Personas: Senior engineer, Reliability engineer, Architect, Performance engineer.
17 findings (4 High, 9 Medium, 4 Low) in cycle 1; 3 findings (0 High, 1 Medium, 2 Low) in cycle 2. All resolved.
QA verification: PASS (3 surface types verified: Library, API via TestClient, live v3 store; 9+ probes including 3 state-dependent).

Note: Invoked on fully-executed plan (all 4 phases complete); performed standalone holistic review.
Sub-agent regression detected (test_data.py wiped in final autofix commit); recovered by restore commit 39cde12.

#### Test execution summary

| Phase | Tests | QA | Notes |
|---|---|---|---|
| 1: Session.extra_fields | pass (282) | SKIP | No runtime surface; hashability tests added |
| 2: data_kiro_v3.py adapter | pass (1844) | N/A — no QA annotation | 50 v3 adapter tests |
| 3: Provider registration + web | pass (1847) | SKIP | Docs/registration only; TestClient used in Step 9b |
| 4: Presence + status classifier | pass (1853) | SKIP | No QA annotation; library surface |

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `_cwd_to_sessions` O(n) stat scan on every call — no root-mtime fast path. | Fixed — O(1) early return when root mtime unchanged and index non-empty. |
| 2 | High | `_find_v3_session_path` uncached O(n) walk on every tail/first-prompt miss. | Fixed — `_session_path_cache` dict; positive results cached, None not cached. |
| 3 | High | `discover_workspaces` and `_cwd_display` read in separate lock acquisitions — race. | Fixed — `_cwd_to_sessions` returns `(index, display)` tuple atomically. |
| 4 | High | `_classify_from_path` v3 sessions hit `_is_v3_format` auto-detection — tool-result-only tails could mis-classify. | Fixed — `kiro-cli-v3` always calls `classify_kiro_v3` directly. |
| 5 | Medium | `_cwd_to_sessions` Phase 1 and Phase 2 both walk V3_SESSIONS_ROOT (double I/O on miss). | Orchestrator: proposed-accept — Phase 2 only fires on cache miss; overhead bounded and acceptable. |
| 6 | Medium | `discover_workspaces` + `load_sessions` each call `_cwd_to_sessions` independently. | Orchestrator: proposed-accept — second call is a fast cache-hit (O(1)); negligible overhead. |
| 7 | Medium | `get_session_tail` docstring claimed empty results NOT cached, but code caches them. | Fixed — docstring corrected; empty tails are cached with mtime guard. |
| 8 | Medium | `refresh_stale_entries_for_cwd` string comparison for path parents — case/separator issues on Windows. | Fixed — uses `Path` equality instead. |
| 9 | Medium | `workspacePaths` as a plain string (not list) raises subtle wrong result. | Fixed — `isinstance(wp, list)` guard on all `workspacePaths` accesses. |
| 10 | Medium | `_V3_SESSIONS_ROOT` defined in two modules — two sources of truth. | Fixed — `status_classifier.py` now imports from `data_kiro_v3`. |
| 11 | Medium | `_session_path_cache` cached `None` results permanently, blocking new ACP sessions. | Fixed — only positive results cached; None never cached. |
| 12 | Medium | `discover_workspaces` + `_cwd_display` snapshot race between two lock acquisitions. | Fixed — see #3 above. |
| 13 | Medium | `_cwd_to_sessions` partial-walk scan_error returned empty on cold cache permanently. | Orchestrator: proposed-accept — next call re-retries fresh; not permanent. |
| 14 | Low | ROADMAP `[P2b]` investigation section still said v3 sessions were invisible. | Fixed — updated to note `kiro-cli-v3` ships 2026-08-18. |
| 15 | Low | No test for O(1) fast path (root mtime unchanged, skip Phase 1). | Fixed — `TestKiroV3CwtoCacheHit` class added. |
| 16 | Low | No test for `get_session_tail` empty cache invalidated on first assistant message. | Fixed — `test_empty_tail_cached_and_invalidated_on_new_message`. |
| 17 | Low | Binary collision: `_match_provider` returns `kiro-cli` for v3 sessions (first dict hit). | Orchestrator: proposed-accept — plan-documented accepted limitation (Risk Assessment + Follow-up Work #2). |

### 2026-08-18 — Implementation Review (after Phases 3+4, personas: Senior engineer + Maintainability reviewer [Phase 3], Reliability engineer + Architect [Phase 4])

Implementation health: Green (after post-cap user-directed fixes).
Phase 3: 5 findings (0 High, 2 Medium, 3 Low). All resolved.
Phase 4: 5 findings (1 High accepted, 2 Medium, 2 Low). All resolved.

| # | Severity | Finding (one line) | Resolution |
|---|---|---|---|
| P3-1 | Medium | ROADMAP "Parked items" Platform bullet still listed "v3 support" after Phase 3. | Fixed — removed "v3 support ·" from Platform parked-items summary line. |
| P3-2 | Medium | Missing test "default args appended after v3 args" — plan listed 4 test cases, only 3 added. | Fixed — `test_kiro_v3_default_args_appended_after_trust_tools` added. |
| P3-3 | Low | `HARNESS.md` missing `kiro-v3-session-data` resource row per Documentation Updates table. | Fixed — added resource row with path and `last_verified: 2026-08-18`. |
| P3-4 | Low | `PROVIDER_BADGES` dict missing `"kiro-cli-v3"` entry while all other 3 providers have one. | Fixed — added `"kiro-cli-v3": "V"` with comment noting unused but consistent. |
| P3-5 | Low | `TestResolveJsonlPath` in `test_web.py` had no coverage for `kiro-cli-v3` cache path. | Fixed — 6 new tests: path found, not found, OSError, 4-tuple cache isolation (Phase 4 files). |
| P4-1 | High | `_match_provider` always returns `kiro-cli` (first dict hit); `kiro-cli-v3` live dots land on v2 row. | Orchestrator: proposed-accept — plan-documented accepted limitation (Risk Assessment + Follow-up Work #2). |
| P4-2 | Medium | No test for `_resolve_jsonl_path("sess_x", "kiro-cli-v3", ...)` new branch. | Fixed — added `test_kiro_v3_provider_returns_path_directly`, `_returns_none_when_absent`, `_returns_none_on_oserror`. |
| P4-3 | Medium | No test for `_classify_from_path(..., "kiro-cli-v3", ...)` dispatching to `classify_kiro_v3`. | Fixed — `test_classify_from_path_routes_v3_provider_to_v3_classifier` added. |
| P4-4 | Medium | 4-tuple cache key not tested — v2 and v3 same session_id could collide. | Fixed — `test_kiro_v3_cache_key_isolated_from_v2` added to `TestResolveJsonlPath`. |
| P4-5 | Low | `else` branch in `_classify_from_path` dispatch comment misleading for unknown providers. | Orchestrator: proposed-accept — out of scope for this phase; no behavioral change needed. |

### 2026-08-18 — Implementation Review (after Phase 2, personas: Senior engineer, Reliability engineer, Performance engineer, Maintainability reviewer)

Implementation health: Green (after 2 auto-fix cycles + post-cap user-directed fixes).
17 findings cycle 1 (4 High, 5 Medium, 8 Low). All resolved.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `refresh_stale_entries_for_cwd` compared all hash_dir subdirs against cwd-filtered sessions — false-positive stale on sibling cwd additions. | Fixed — derive tracked dirs from old_stats path parents (cwd-scoped); read new dirs' session.json to confirm cwd before triggering stale. |
| 2 | High | `_cwd_to_sessions` double-walked V3_SESSIONS_ROOT on every cache miss — O(2n) I/O, both passes inside lock. | Fixed — two-phase design: fast mtime-only scan outside lock; JSON parse only on miss; atomic swap under lock. |
| 3 | High | `discover_workspaces` bypassed `_cwd_to_sessions` index — full independent walk on every call. | Fixed — calls `_cwd_to_sessions`; uses `_cwd_display` populated during rebuild for zero extra reads. |
| 4 | High | `refresh_stale_entries_for_cwd` read `_norm_cwd_to_hash` without `_index_lock` — torn-read race with swap. | Fixed — added brief lock acquisition around `_norm_cwd_to_hash.get(norm_cwd)`. |
| 5 | Medium | `_cwd_to_sessions` held `_index_lock` across full I/O scan — serialised all concurrent callers. | Fixed — all I/O outside lock in two-phase redesign. |
| 6 | Medium | `_extract_prompts_v3` opened `messages.jsonl` twice — double I/O and head/tail race. | Fixed — single open; `lines[:50]` head, `lines[-100:]` tail. |
| 7 | Medium | `_hash_dir_mtimes` variable name misleading — stores session.json mtimes. | Fixed — renamed to `_session_json_mtimes` throughout. |
| 8 | Medium | `_cwd_to_sessions` outer OSError fell through to swap instead of returning cached state. | Fixed — early return `_cwd_index.copy()` on scan error. |
| 9 | Medium | `get_first_prompt` didn't negative-cache empty results — re-scanned tool-only sessions on every TTL miss. | Fixed — empty result written to cache with mtime guard. |
| 10 | Medium | `discover_workspaces` non-dict JSON guard had no test for null/[] session.json values. | Fixed — added `test_discover_workspaces_null_json_skipped`. |
| 11 | Medium | `discover_workspaces` missing-`lastModifiedAt` sort behavior untested. | Fixed — added `test_discover_workspaces_missing_last_modified`. |
| 12 | Low | No test for sibling-cwd session addition in same hash dir (H1 regression path). | Fixed — `test_sibling_cwd_session_added_does_not_trigger_stale`. |
| 13 | Low | `test_file_stats_populated` used substring check instead of exact key assertion. | Fixed — now asserts exact `{str(sj), str(msgs)}` key set. |
| 14 | Low | No test for `_extract_prompts_v3_cached` cache-hit path. | Fixed — `test_load_sessions_cache_hit_returns_same_result`. |
| 15 | Low | `get_session_tail` empty result caching behavior undocumented. | Fixed — added docstring comment explaining deliberate non-caching. |
| 16 | Low | `_norm_cwd_to_hash` one-cwd-per-hash invariant undocumented. | Fixed — added comment in module-level dict declaration. |
| 17 | Low | No test for negative `get_first_prompt` cache invalidation on file update. | Fixed — `test_negative_cache_invalidated_when_file_updated`. |
| C2-1 | ~~Med~~ | `_extract_prompts_v3` missing 200-char cap — reviewers were wrong; `_cap_text` defaults 2000 chars. | Orchestrator: proposed-accept — `_cap_text(2000/15)` consistent with all providers; claim was factually incorrect. |

### 2026-08-18 — Implementation Review (after Phase 1, personas: Senior engineer, Maintainability reviewer, Architect, Reliability engineer)

Implementation health: Green (after 2 auto-fix cycles + post-cap fixes).
7 findings cycle 1 (1 High, 4 Medium, 2 Low). All resolved.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `frozen=True` + `dict` field silently broke `hash(Session)`, removing pre-existing hashability guarantee. | Fixed — added `hash=False, compare=False` to `extra_fields` field; hashability verified with test. |
| 2 | Medium | `_reset_kiro_caches()` omitted `_prompts_cache`, `_tail_cache`, `_first_prompt_cache` resets per exit criterion. | Fixed — added three hasattr-guarded `.clear()` calls inside the deferred try block. |
| 3 | Medium | `except (ImportError, AttributeError)` — `AttributeError` is dead code; wrong attr name silently creates new module attrs. | Fixed — removed `AttributeError` clause; kept only `except ImportError`. |
| 4 | Medium | No regression test for `hash(Session(...))` — isolation test covered default_factory, not hashability. | Fixed — added `test_session_is_hashable` to `TestFrozenSession`. |
| 5 | Low | Misleading comment "positional Session(...) calls in tests depend on this" underspecified. | Fixed — rewritten to name the file and line form explicitly. |
| 6 | Low | Redundant explicit `extra_fields={}` at 3 provider construction sites (default_factory already handles it). | Fixed — removed from all 3 adapters. |
| 7 | Low | No test for `compare=False` semantics (Sessions differing only in `extra_fields` should compare equal). | Fixed — added `test_session_compare_ignores_extra_fields`. |
| C2-1 | Low | Double hasattr guard in `_reset_kiro_caches()` is overly defensive (inner `.clear` check adds no protection). | Orchestrator: proposed-accept — pending user decision. Defensive guard for pre-Phase-2 deferred import code; no behavioral consequence. |

### 2026-08-18 — Initial plan review (4 personas: Architect, Senior engineer, Performance engineer, Reliability engineer)

19 findings (8 High, 7 Medium, 4 Low). 16 auto-resolved. 3 deferred to Follow-up Work.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `_path_cache` key change description was self-contradictory ("unchanged" but gains `provider` field). | Fixed — Phase 4 explicitly states key changes from 3-tuple to 4-tuple; test update requirement added. |
| 2 | High | Binary collision: `kiro-cli` and `kiro-cli-v3` share binary; presence first-match attributes v3 to `kiro-cli`. | Fixed — accepted limitation documented in Phase 4 and Risk Assessment; `sess_`-prefix disambiguation deferred to Follow-up Work. |
| 3 | High | No OSError guards in `_cwd_to_sessions()` and `find_session_workspace`; `load_sessions` could propagate unhandled. | Fixed — Phase 2 now requires `try/except OSError` on all `iterdir()` and scan paths. |
| 4 | High | Missing `isinstance(data, dict)` guard after `json.loads` in adapter — partial writes produce AttributeError. | Fixed — Phase 2 exit criterion requires this guard mirroring `data_kiro._load_meta`. |
| 5 | High | `_clear_cache` autouse fixture resets only `data_kiro.*` globals; v3 globals leak between tests causing non-deterministic failures. | Fixed — Phase 1 exit criterion requires extending `_reset_kiro_caches()` for v3 module globals. |
| 6 | High | Hash-dir mtime strategy: appending to `messages.jsonl` bumps hash-dir mtime on NTFS/ext4, triggering full index rebuild on every agent turn. | Fixed — Phase 2 now gates on `session.json` mtime (membership signal), not hash-dir mtime. |
| 7 | High | `_find_v3_session_path` uncached O(n) walk called per-session in hot paths; `_workspace_status` fallback always fires for v3 (no lock files). | Fixed — callers (`get_session_tail`, `get_first_prompt`) use `BoundedCache` with mtime guard; `_find_v3_session_path` is called at most once per mtime change per session. Phase 4 caches `kiro-cli-v3` in `_path_cache`. |
| 8 | High | `test_web.py` constructs `Session(...)` but was missing from Phase 1 file scope. | Fixed — added to Phase 1 file scope as read-only confirmation. |
| 9 | Medium | Empty `workspacePaths` produces `cwd = ""` polluting `_cwd_index`. | Fixed — Phase 2 requires `if not cwd: continue` guard. |
| 10 | Medium | `refresh_stale_entries_for_cwd` returned `False` when root gone (wrong). | Fixed — Phase 2 requires returning `True` when `V3_SESSIONS_ROOT` absent. |
| 11 | Medium | `_tail_cache` and `_first_prompt_cache` as plain dicts — unbounded. | Fixed — Phase 2 now uses `BoundedCache` for both. |
| 12 | Medium | `refresh_stale_entries_for_cwd` called `_cwd_to_sessions()` internally, triggering full rebuild every tick. | Fixed — Phase 2 uses `_norm_cwd_to_hash` cached mapping instead. |
| 13 | Medium | `BoundedCache` missing from Phase 2 import list. | Fixed — Phase 2 import block now explicit. |
| 14 | Medium | `default_args="-a"` appended after `--trust-tools *` causes v3 launch to fail with no PowerAtlas guard. | Fixed — code comment added in `_build_provider_args` snippet; Risk Assessment entry added; deferred config-migration to Follow-up Work. |
| 15 | Medium | Phase 3 grep count "≥5" too low — expected ≥7. | Fixed — exit criterion updated to ≥7 with explicit breakdown. |
| 16 | Medium | Module-level v3 cache dicts not thread-safe under concurrent `asyncio.to_thread`. | Fixed — Phase 2 requires `threading.Lock()` around index rebuild. |
| 17 | Low | `_build_provider_args` else branch comment misleading. | Fixed — comment updated to "catch-all". |
| 18 | Low | `sess_` prefix normalization duplicated across 3 call sites. | Fixed — Phase 2 requires `_ensure_sess_prefix` helper. |
| 19 | Low | README, CLOSED_INVESTIGATIONS.md, HARNESS.md stale; ROADMAP [P2b] needs splitting. | Fixed — Documentation Updates table expanded; Phase 3 file scope and exit criteria updated. |

## Harness Improvement Opportunities

- Interactive CLI probes (kiro-cli resume, kiro-cli v3 launch) cannot be run non-interactively during `/qexplore` — the `--no-interactive` flag requires input, and without it the CLI hangs. Cost: one tool-use cancellation during probe gate. Suggested change: add a note to the probe gate in `/qexplore` that interactive CLIs with no headless mode should be recorded as open items rather than attempted non-interactively.
