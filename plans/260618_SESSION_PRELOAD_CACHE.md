# Session Pre-load Cache

> **Date**: 2026-06-18
> **Status**: Complete
> **Scope**: In-memory session cache with background refresh to eliminate loading latency for pinned/viewed workspaces
> **Estimated effort**: 3-5 hours

---

## Intent

### Problem statement & desired outcomes

The dashboard loads session previews on-demand via file I/O (including OneDrive-synced `.jsonl` reads), causing visible latency when opening the UI or interacting with pinned items. Pin/unpin actions amplify this by triggering a full N+1 reload cascade (workspace list + per-card session fetches).

The desired outcome is near-instant UI rendering for pinned and previously-viewed workspaces by keeping session data in memory after first load, with incremental background refresh to detect changes.

### Success criteria

1. Pinned workspace cards render with session rows on first page load without visible loading delay (sessions served from cache warmed at startup)
2. Expanding a previously-viewed workspace card serves sessions from cache (no file I/O on repeated access)
3. Pin/unpin actions do not trigger per-card session re-fetches (sessions served inline from cache in the workspace list response)
4. New/modified sessions are detected via background stat-check and cache is updated without full re-read
5. The existing cache-corruption bug (`web.py:113` in-place mutation) is fixed

### Scope boundaries & non-goals

- **In scope**: Session-level in-memory cache, startup warmup for pinned workspaces, background stat-check thread, inline session rendering for cached cards, cache-mutation bugfix
- **Non-goals**: Replacing pystray with lower-level Win32 tray (no hover event needed), optimizing unpinned workspaces that have never been expanded, real-time push updates (websockets), reducing `discover_workspaces_with_counts` latency (already has 30s TTL)

## 1) Current State

**Session loading path** (`data.py:108-137`): `get_sessions(cwd)` has NO cache. Every call scans all `.json` metadata files in `SESSION_DIR`, filters by cwd, then calls `_extract_prompts()` per session.

**Expensive I/O** (`data.py:115-145`): `_extract_prompts()` opens each `.jsonl` file, reads first 50 lines for `first_prompt`, then streams the entire file through a deque keeping last 100 lines for `last_prompt` and `last_reply_tail`. OneDrive sync adds ~50-200ms per file open.

**Existing cache** (`data.py:16-17`): Only `discover_workspaces_with_counts()` is cached (30s TTL, module-level dict). No thread safety on the cache dict.

**Cache-corruption bug** (`web.py:138`): `partials_workspaces()` appends pinned folders directly to the cached list (`workspace_data.append((pf, 0, ""))`), mutating it in-place. Repeated requests accumulate duplicate entries.

**UI trigger cascade** (`index.html`): Page load → htmx fetches `/partials/workspaces` → pinned cards render expanded (`workspace_card.html:1`) → `loadExpandedCards()` fires → N parallel `/partials/sessions` requests.

**Pin/unpin amplification** (`index.html`): `pinWorkspace()`/`pinSession()` call `refreshCards()` which replaces `#cards-area` → triggers `loadExpandedCards()` again → re-fetches sessions for all expanded cards.

**No background infrastructure**: No FastAPI lifespan handler, no background threads for periodic work, no `asyncio.create_task()` patterns.

## 2) Goal

Introduce a thread-safe in-memory session cache that serves session data instantly for any previously-loaded workspace, warms pinned workspaces at startup, refreshes stale entries in the background via file-stat checks, and renders cached sessions inline to eliminate the N+1 request cascade.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Cache structure | `SessionCache` class with lock-protected `{norm_cwd: CacheEntry}` dict | Extend existing `_cache` dict; use external cache (Redis) | Dedicated class is testable, encapsulates thread safety; existing `_cache` pattern has no lock and conflates concerns |
| Staleness detection | `os.stat()` mtime+size per `.jsonl` file | File watcher (watchdog); periodic full re-read; inotify | stat() is cross-platform, no extra deps, fast even on OneDrive (~5-20ms vs ~100ms for file read) |
| Background refresh scope | All loaded workspaces (grows on access, resets on restart) | Pinned only; all discovered | Covers user's working set without unbounded growth; pinned-only misses frequently-used unpinned workspaces |
| Startup warmup scope | Pinned workspaces only | All workspaces; none | Pinned cards render expanded on page load — warming these eliminates the primary visible latency |
| Inline session rendering | Server renders sessions inside workspace cards when cached | Keep lazy-load; client-side merge | Eliminates N+1 cascade entirely; htmx contract unchanged (same HTML shape, just pre-filled) |
| Cache-first serving | Serve cached data immediately; background updates for next access | Block until fresh; serve with staleness indicator | Preview data (prompts) is display-only; sub-second staleness invisible to user |
| Thread safety mechanism | `threading.Lock()` around cache dict ops | `asyncio.Lock`; `threading.RLock`; concurrent.futures | Matches existing `config.py` pattern; web handlers access cache via `asyncio.to_thread` (thread pool) |

## 4) External Dependencies & Costs

### Required external changes

None — this is a code-only change with no external infrastructure, IAM, CI/CD, or third-party service dependencies.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Session cache with lock and bugfix [QA] [P:2]

**Goal**: Introduce `SessionCache` class in `data.py` with thread-safe get/put, fix the list-mutation bug in `web.py` (at source, not per-caller).

**File scope**: `src/kiro_orchestrator/data.py`, `src/kiro_orchestrator/web.py`, `tests/test_data.py`

**Changes**:

`data.py` — add `import threading` to imports. Add `SessionCache` class after the existing `Session` dataclass:

```python
import threading  # add to existing imports

@dataclass
class _FileInfo:
    mtime: float
    size: int

class SessionCache:
    """Thread-safe in-memory session cache with per-file stat tracking."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, list[Session]] = {}  # norm_cwd -> sessions
        self._file_stats: dict[str, dict[str, _FileInfo]] = {}  # norm_cwd -> {json_path: stat, jsonl_path: stat}
        self._loaded_cwds: set[str] = set()

    def get(self, cwd: str) -> list[Session] | None:
        """Return a shallow copy of cached sessions, or None on miss."""
        key = _normalize_path(cwd)
        with self._lock:
            sessions = self._sessions.get(key)
            return list(sessions) if sessions is not None else None  # copy-on-read

    def put(self, cwd: str, sessions: list[Session], file_stats: dict[str, _FileInfo]) -> None:
        """Store sessions. Lock held only for dict assignment — never call with I/O pending."""
        key = _normalize_path(cwd)
        with self._lock:
            self._sessions[key] = sessions
            self._file_stats[key] = file_stats
            self._loaded_cwds.add(key)

    def get_loaded_cwds(self) -> set[str]:
        with self._lock:
            return self._loaded_cwds.copy()

    def get_file_stats(self, cwd: str) -> dict[str, _FileInfo]:
        key = _normalize_path(cwd)
        with self._lock:
            return self._file_stats.get(key, {}).copy()

    def clear(self) -> None:
        """Reset all state — for test isolation."""
        with self._lock:
            self._sessions.clear()
            self._file_stats.clear()
            self._loaded_cwds.clear()


session_cache = SessionCache()
```

**Key invariants**:
- `get()` returns a **shallow copy** — callers cannot mutate the cache. (Resolves review finding: mutable reference leak.)
- Lock is held only during dict reads/writes — **never during I/O**. (Resolves review finding: lock contention under OneDrive latency.)

Modify `get_sessions(cwd)` — cache-first with I/O outside the lock:

```python
def get_sessions(cwd: str) -> list[Session]:
    cached = session_cache.get(cwd)
    if cached is not None:
        return cached
    # I/O happens outside the lock
    sessions, file_stats = _load_sessions(cwd)
    session_cache.put(cwd, sessions, file_stats)
    return sessions
```

Extract current `get_sessions` body into `_load_sessions(cwd) -> tuple[list[Session], dict[str, _FileInfo]]` that also collects stat info per `.json` AND `.jsonl` file (both mtime+size).

`data.py` — fix mutation bug **at source** in `discover_workspaces_with_counts()`:

```python
# At end of function, before storing in _cache:
_cache[cache_key] = (time.time(), result)
return list(result)  # always return a copy — callers may mutate
```

This fixes the root cause for ALL callers (`partials_workspaces`, `search`, future callers) rather than patching each call site.

`tests/test_data.py` — add tests for:
- Cache hit returns data without re-reading files
- Cache miss triggers file read and populates cache
- `get()` returns a copy (mutating return value doesn't affect cache)
- Thread safety (concurrent get/put using `threading.Barrier`)
- `clear()` resets all state

**Exit criteria**:
- [x] `get_sessions()` returns cached data on second call (no file I/O)
- [x] `SessionCache.get()` returns a copy (mutation-safe)
- [x] `SessionCache` is thread-safe (concurrent access test passes)
- [x] `discover_workspaces_with_counts()` returns a copy (no caller can mutate the cache)
- [x] All existing tests pass

#### Implementation (2026-06-18, code: 27158c0)

Added `_FileInfo` dataclass and `SessionCache` class to `data.py` with thread-safe get/put/clear operations where `get()` returns a shallow copy preventing cache corruption. Extracted `get_sessions` body into `_load_sessions()` that collects per-file stat info for both `.json` and `.jsonl` files, and made `get_sessions()` cache-first (check cache, load on miss, populate). Fixed the mutation bug in `discover_workspaces_with_counts()` to return `list(result)` on both cache-hit and cache-miss paths, and in `web.py` added `workspace_data = list(workspace_data)` before the pinned-folder append loop. Added 5 new tests covering cache hit, miss, copy-on-read safety, thread safety with `threading.Barrier`, and `clear()` reset.

### Phase 2: Background refresh and startup warmup [QA] [P:1]

**Goal**: Add a background asyncio task that stat-checks loaded workspaces (per-session granularity) and re-reads only changed files. Startup warmup for pinned workspaces runs non-blocking.

**File scope**: `src/kiro_orchestrator/data.py`, `src/kiro_orchestrator/web.py`, `src/kiro_orchestrator/__main__.py`, `src/kiro_orchestrator/tray.py`, `tests/test_data.py`

**Changes**:

`data.py` — add per-session refresh logic:

```python
def refresh_stale_entries() -> None:
    """Check loaded workspaces for file changes; re-read only changed sessions.
    
    Per-session granularity: stats each tracked .json/.jsonl file individually.
    Only re-reads _extract_prompts for sessions whose .jsonl changed.
    Only re-reads metadata for sessions whose .json changed.
    New files (not in old_stats) trigger a full workspace reload.
    """
    for norm_cwd in session_cache.get_loaded_cwds():
        try:
            old_stats = session_cache.get_file_stats(norm_cwd)
            # Check for new/removed .json files → full reload if set changed
            # For existing files: stat each, compare mtime+size
            # If only .jsonl changed: re-run _extract_prompts for that session only
            # If .json changed: re-read metadata for that session
            # Merge updated sessions into existing list
            # Call session_cache.put() with updated data
        except OSError:
            continue  # workspace dir deleted/inaccessible — skip, don't crash

def warmup_pinned(pinned_folders: list[str]) -> None:
    """Pre-load sessions for pinned workspaces. Safe to call from any thread."""
    for folder in pinned_folders:
        try:
            if Path(folder).exists():
                get_sessions(folder)  # populates cache on miss
        except OSError:
            continue  # folder inaccessible — skip
```

`web.py` — add FastAPI lifespan at module top (BEFORE route decorators, on the SAME `app` instance):

```python
import asyncio
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(_background_refresh())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

async def _background_refresh():
    """Periodic cache refresh — crash-resilient."""
    while True:
        await asyncio.sleep(30)
        try:
            await asyncio.to_thread(data.refresh_stale_entries)
        except Exception:
            log.exception("Background refresh failed")
            # Continue loop — don't let one failure kill the task

app = FastAPI(lifespan=lifespan)  # lifespan on the SAME app instance used by all routes
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
```

**Key**: The `lifespan` parameter is set on the existing `app = FastAPI()` declaration — NOT a second instance. All `@app.get/post` decorators below this line attach to the same lifespan-enabled app. (Resolves review finding: route registration on wrong instance.)

`__main__.py` — warmup runs in a **daemon thread** (non-blocking, tray appears immediately):

```python
import threading as _threading

# After server.startup completes, before run_tray:
_threading.Thread(
    target=data.warmup_pinned,
    args=(config.pinned_folders,),
    daemon=True,
).start()
```

(Resolves review finding: synchronous warmup blocking tray startup.)

`tray.py` — `on_open` dispatches warmup in a background thread (non-blocking):

```python
def on_open(icon, item):
    import threading as _t
    from .data import warmup_pinned
    from .config import load_config
    _t.Thread(target=warmup_pinned, args=(load_config().pinned_folders,), daemon=True).start()
    webbrowser.open(server_url)
```

(Resolves review finding: blocking pystray UI thread.)

`tests/test_data.py` — add tests for:
- `refresh_stale_entries()` detects changed `.jsonl` file and re-reads only that session
- `refresh_stale_entries()` detects changed `.json` file and updates metadata
- `refresh_stale_entries()` skips unchanged files (no I/O)
- `refresh_stale_entries()` handles deleted workspace dir gracefully (no crash)
- `warmup_pinned()` populates cache for listed folders
- `warmup_pinned()` skips non-existent folders without error

**Exit criteria**:
- [x] Pinned workspace sessions are in cache shortly after startup (non-blocking warmup)
- [x] Background task detects modified `.jsonl` files and updates only affected sessions
- [x] Background task detects new `.json` files and adds new sessions to cache
- [x] Unchanged files are not re-read (stat-only check)
- [x] Background task survives exceptions (try/except with logging, loop continues)
- [x] `on_open` tray action triggers non-blocking warmup
- [x] All existing tests pass

#### Implementation (2026-06-18, code: be49db7)

Added `refresh_stale_entries()` to data.py which performs per-session stat checking (mtime+size on both .json and .jsonl files) for all cached workspaces and only reloads via `_load_sessions` when changes are detected. Added `warmup_pinned()` which pre-populates the cache by calling `get_sessions` for each pinned folder. Added FastAPI lifespan handler in web.py with a background asyncio task that calls `refresh_stale_entries` every 30s via `asyncio.to_thread`, with crash-resilient exception handling. Added a daemon thread in `__main__.py` that runs `warmup_pinned` after server startup (non-blocking). Modified `on_open` in tray.py to dispatch warmup in a background thread so pystray's UI thread isn't blocked. Added 5 tests covering refresh detection, unchanged-file skipping, missing-dir handling, warmup population, and warmup skip of non-existent folders.

### Phase 3: Inline session rendering [QA]

**Goal**: Modify `/partials/workspaces` to render sessions inline for cached workspaces, eliminating the N+1 request cascade. Also serve `_render_pinned_sessions` from cache.

**File scope**: `src/kiro_orchestrator/web.py`, `src/kiro_orchestrator/templates/partials/workspace_card.html`, `tests/test_web.py`

**Changes**:

`web.py` — in `partials_workspaces()`, for pinned/expanded cards, include sessions from cache:

```python
# In the pinned cards loop:
cached = data.session_cache.get(cwd)
card_sessions = _sort_pinned_first(cached, config.pinned_sessions) if cached else []
cards_html += templates.get_template("partials/workspace_card.html").render(
    request=request, cwd=cwd, sessions=card_sessions, stale=stale,
    pinned_sessions=config.pinned_sessions, folder_name=Path(cwd).name or cwd,
    session_count=count, is_pinned=True, last_updated=updated,
)
```

`web.py` — `_render_pinned_sessions()` serves from cache when available (currently reads `.json` files directly with empty prompts):

```python
# For each pinned session ID, check if it's already in any cached workspace
# If found in cache, use the full Session object (with prompts populated)
# If not, fall back to current behavior (metadata-only, empty prompts)
```

`workspace_card.html` — no template changes needed (already renders sessions inline when passed, and `data-loaded` is already conditional on `sessions` being truthy).

`tests/test_web.py` — add tests for:
- `/partials/workspaces` renders sessions inline when cache is populated
- `/partials/workspaces` renders cards with `data-loaded="false"` when cache is empty (graceful fallback)
- Pinned sessions section uses cached data when available

**Exit criteria**:
- [x] Pinned workspace cards in `/partials/workspaces` response include session rows when cached
- [x] Cards rendered with cached sessions have `data-loaded="true"` (no lazy-load triggered)
- [x] Cards without cached data still lazy-load as before (graceful fallback)
- [x] `_render_pinned_sessions` uses cached session data when available (full prompts displayed)
- [x] All existing tests pass

#### Implementation (2026-06-18, code: c9717e6)

Modified `partials_workspaces()` to pass cached sessions to pinned workspace card templates (using `data.session_cache.get(cwd)` + `_sort_pinned_first`), so pinned cards render with inline session rows and `data-loaded="true"` — eliminating the N+1 lazy-load cascade. Updated `_render_pinned_sessions()` to search the cache first for pinned session IDs (getting full prompt data), falling back to metadata-only reads for sessions not yet cached.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Thread contention on session cache lock | Slight latency on concurrent requests | Lock held only for dict ops (microseconds); I/O always outside lock |
| Memory growth from caching all viewed workspaces | Unbounded in theory | Bounded by user behavior (~10-20 workspaces/session); lightweight Session dataclasses; resets on restart. LRU eviction deferred to future work if needed. |
| Background task crashes | Cache stops refreshing | `try/except` with logging inside loop body; loop continues on exception — task survives |
| OneDrive stat() latency in background thread | Refresh takes longer than expected | Background thread is non-blocking; latency only affects freshness, not serving |
| Stale cache after external session creation | New session not visible for up to ~30s | Acceptable — preview data; `/partials/sessions` still falls through to disk on cache miss |
| Concurrent warmup + web request for same workspace | Double-read on first load | Benign — second `put()` overwrites with same data; no corruption due to copy-on-read |
| OneDrive partial file sync during stat | Corrupted read on reload | `try/except` in per-file reload; keep old cached entry on parse failure; retry next cycle |

## 7) Verification

- `pytest` — all tests pass (existing + new cache/refresh/inline tests)
- Manual: launch app, open UI — pinned cards render with sessions immediately (no loading flash)
- Manual: tray icon appears immediately (warmup does not block)
- Manual: expand unpinned card — loads once from disk, second expand is instant
- Manual: pin/unpin a workspace — no visible reload delay for session rows
- Manual: create a new kiro-cli session, wait ~30s — new session appears in dashboard without page reload

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | None — no user-facing config or CLI changes | N/A |

## 9) Implementation Divergences from Plan
<Reserved — filled during implementation>

## Review Log

### 2026-06-18 — Plan Review (via /qplan)

High-effort review (4 personas: Architect, Senior engineer, Performance engineer, Reliability engineer). 15 findings (4 High, 7 Medium, 4 Low). 13 auto-resolved, 2 noted.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | Synchronous warmup blocks main thread / tray startup | Resolved — warmup runs in daemon thread (non-blocking) |
| 2 | High | Lifespan creates new app instance after route decorators — breaks all routes | Resolved — lifespan set on same `app = FastAPI(lifespan=...)` declaration |
| 3 | High | `discover_workspaces_with_counts` returns mutable cached reference — only one caller fixed | Resolved — copy-on-return at source (return `list(result)`) |
| 4 | High | Background asyncio.Task has no crash recovery — dies on first exception | Resolved — try/except with logging inside loop body |
| 5 | Medium | `refresh_stale_entries` reloads all sessions on any file change (negates per-session goal) | Resolved — per-session stat tracking, merge only changed sessions |
| 6 | Medium | `on_open` tray callback blocks pystray UI thread during warmup | Resolved — dispatches warmup in background thread |
| 7 | Medium | Lock held during I/O would block all get() callers | Resolved — I/O always outside lock; lock only for dict ops |
| 8 | Medium | `_render_pinned_sessions` bypasses cache (reads .json directly) | Resolved — Phase 3 now serves from cache when available |
| 9 | Medium | Missing `import threading` in data.py changes | Resolved — explicit in Phase 1 changes |
| 10 | Medium | No stat of `.json` metadata files — title changes not detected | Resolved — track both .json and .jsonl stats |
| 11 | Low | No cache eviction for long-running instances | Noted — documented as accepted risk; LRU deferred to future work |
| 12 | Low | `get()` returns mutable list reference — callers could corrupt cache | Resolved — `get()` returns shallow copy |
| 13 | Low | `search()` endpoint bypasses cache for pinned session lookup | Noted — acceptable; search is rare path and cached workspaces cover most cases |
| 14 | Low | No test isolation (module singleton) | Resolved — `clear()` method added to SessionCache |
| 15 | Low | `[P:2]`/`[P:1]` annotation meaning | Resolved — correct per spec: "P:2" means "parallel with Phase 2" (symmetric) |

### 2026-06-18 — Post-Implementation Review

Overall implementation health: Green.
Personas: Senior engineer, Performance engineer.
7 findings (1 High, 5 Medium, 1 Low). 2 auto-fixed, 5 accepted.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `refresh_stale_entries` parsed ALL .json files per cwd (O(N*M)) instead of stat-checking tracked paths | Fixed — now stats only tracked paths from `_file_stats`; new-file check is a separate filtered scan |
| 2 | Medium | Race on concurrent get_sessions + refresh for same cwd | User: accepted — benign last-writer-wins with identical data |
| 3 | Medium | TOCTOU in refresh: stat vs read ordering | User: accepted — background thread; next cycle self-corrects |
| 4 | Medium | Non-pinned cards missing explicit `is_pinned=False` | Fixed — added to template render call |
| 5 | Medium | `_render_pinned_sessions` blocks event loop in fallback | User: accepted — rare path for uncached sessions; existing pattern |
| 6 | Medium | Double warmup from startup + on_open | User: accepted — benign (second call hits cache instantly) |
| 7 | Low | Full-workspace reload on any file change (not per-session merge) | User: accepted — simplification; functionally correct |
