# Test Plan: kiro-orchestrator

approach: organic
created: 2026-06-18T17:08:00+02:00
last_executed: null

## Resource Constraints

- **Web UI + Data + Config**: share browser instance and server process
- **Launcher**: spawns real terminal processes — test in isolation, clean up after
- **Autostart**: mutates real Startup folder — snapshot before, restore after
- **Tray**: native GUI, user-assisted (deferred to end)
- **Main/Lifecycle**: process-level — test in isolation (mutex, port binding)

---

## 1. Data Layer


### 1.1 Workspace discovery (basic)
- **what**: Discovers unique workspace paths from session metadata + sqlite, sorted by recency
- **how-to-reach**: Call `data.discover_workspaces()` directly or trigger via `/partials/workspaces`
- **probes**: Empty SESSION_DIR; SESSION_DIR with only sub-agent sessions (all filtered); mix of .json and non-.json files; sqlite unavailable (locked/missing); metadata with missing `cwd` field; file >1MB (should be skipped)
- **oracle**: Returns list of normalized paths sorted by `updated_at` desc; sub-agents excluded; sqlite supplements filesystem
- **risks**: OneDrive lock contention; O(n) scan with no caching; sqlite 5s block on locked DB

### 1.2 Workspace discovery with counts (cached)
- **what**: Returns (cwd, session_count, updated_at) tuples with 30s TTL cache
- **how-to-reach**: Call `data.discover_workspaces_with_counts()` twice within 30s
- **probes**: Verify cache hit (same object returned within TTL); verify cache miss after 30s; concurrent calls from multiple threads; verify sqlite-only workspaces get count=0
- **oracle**: First call scans filesystem+sqlite; subsequent calls within 30s return cached; after 30s re-scans
- **risks**: Thread-unsafe global `_cache` dict; stale data up to 30s; `display` dict non-deterministic for same-workspace different-casing

### 1.3 Session listing per workspace
- **what**: Returns Session objects for a given workspace cwd, sorted by updated_at desc, sub-agents excluded
- **how-to-reach**: Call `data.get_sessions(cwd)` with a real workspace path
- **probes**: Workspace with many sessions (>20); workspace with zero sessions; cwd with different casing/slash direction than stored; session with missing .jsonl file; session >1MB metadata
- **oracle**: Sorted list of Session objects; no sub-agent sessions; missing .jsonl yields empty prompt fields
- **risks**: No caching (re-reads all files); no pagination; session_id fallback to filename stem

### 1.4 Sub-agent filtering
- **what**: Sessions with truthy `parent_session_id` are excluded from all results
- **how-to-reach**: Ensure session metadata files with and without `parent_session_id` exist in SESSION_DIR
- **probes**: Session with `parent_session_id: "abc123"` (excluded); session with `parent_session_id: null` (included); session with `parent_session_id: ""` (included — falsy); session with `session_created_reason: "subagent"` but no `parent_session_id` (included)
- **oracle**: Only `parent_session_id` truthiness determines exclusion
- **risks**: Orphaned sub-agent sessions (no parent_session_id) appear as regular sessions

### 1.5 Prompt extraction from .jsonl
- **what**: Extracts first_prompt, last_prompt, last_reply_tail from session .jsonl files
- **how-to-reach**: Call `data.get_sessions()` — triggers `_extract_prompts()` per session
- **probes**: .jsonl with first user prompt beyond line 50 (missed); very short .jsonl (<5 lines); .jsonl with only ToolResults lines (no prompts); binary/corrupted .jsonl; .jsonl with content as list vs string
- **oracle**: first_prompt[:200] from first 50 lines; last_prompt[:200] and last_reply_tail[-100:] from tail 100 lines
- **risks**: First prompt missed if preamble >50 lines; tail deque may miss in very long exchanges; CPU-intensive JSON parsing per line

### 1.6 Content extraction (multi-format)
- **what**: Parses .jsonl line content field (string or list of text objects)
- **how-to-reach**: Indirectly via prompt extraction on real session data
- **probes**: Content as plain string; content as list with `kind:"text"` items; content with `type:"text"` items; content with nested `data.text`; content with toolUse entries (should be skipped); malformed JSON line
- **oracle**: String content returned directly; list content joined with spaces; parse failures return ""
- **risks**: Deeply nested structures beyond one level not handled; toolUse silently skipped

### 1.7 Path normalization
- **what**: Normalizes paths by stripping trailing separators and casefolding on Windows
- **how-to-reach**: Any workspace comparison internally calls `_normalize_path()`
- **probes**: Path with trailing backslash; path with trailing forward slash; mixed slashes (`C:/foo\bar`); path with unicode characters; same path different casing
- **oracle**: Trailing separators stripped; casefold on Windows; forward vs back slash NOT normalized (known gap)
- **risks**: `C:/foo` and `C:\foo` produce different keys; locale-dependent casefold for Unicode

### 1.8 SQLite read-only access
- **what**: Opens kiro-cli sqlite DB in read-only mode with busy_timeout
- **how-to-reach**: Any workspace discovery call attempts sqlite
- **probes**: DB exists and readable; DB locked by another process (wait up to 5s); DB doesn't exist; LOCALAPPDATA empty; DB with unexpected schema
- **oracle**: Returns Connection or None; never raises; busy_timeout=5000ms
- **risks**: 5s block on locked DB; empty LOCALAPPDATA produces wrong path; no connection pooling

### 1.9 TTL cache (30s)
- **what**: Module-level dict caching discover_workspaces_with_counts results
- **how-to-reach**: Call discover_workspaces_with_counts twice
- **probes**: Verify same result within TTL; verify fresh scan after TTL; no explicit invalidation after pin/unpin actions; concurrent reads
- **oracle**: Cache hit returns same object; cache miss triggers rescan; no invalidation API
- **risks**: Thread-unsafe dict; stale data after session creation; never evicted (only overwritten)

### 1.10 Session dataclass
- **what**: Data container for session information with all-string fields
- **how-to-reach**: Returned by get_sessions()
- **probes**: Verify all fields populated from metadata; verify defaults for missing fields; verify title defaults to '<untitled>'
- **oracle**: Strings for all fields; timestamps as ISO strings; no datetime parsing
- **risks**: Non-normalized cwd stored; no file path field for resume operations

### 1.11 Large-file skip guard
- **what**: Skips .json metadata files >1MB silently
- **how-to-reach**: Place a >1MB .json file in SESSION_DIR
- **probes**: File exactly 1MB (included); file 1MB+1 byte (excluded); verify no error logged
- **oracle**: Files >1,048,576 bytes skipped; no error/warning
- **risks**: Legitimate large sessions silently excluded; no reporting

### 1.12 Defensive error handling
- **what**: All parse/read errors caught and skipped silently
- **how-to-reach**: Introduce corrupted .json files, unreadable files, malformed sqlite
- **probes**: Invalid JSON in .json file; permission-denied file; corrupted sqlite; UnicodeDecodeError in metadata
- **oracle**: Individual errors skip that entry; partial results returned; no exceptions propagate
- **risks**: Silent swallowing makes debugging impossible; no completeness indicator

---

## 2. Web API + UI


### 2.1 Workspace discovery and display
- **what**: On page load, fetches and renders workspace cards via htmx
- **how-to-reach**: Navigate to `/` — skeleton cards appear, then htmx fires `GET /partials/workspaces`
- **probes**: Normal load with real data; load when SESSION_DIR is empty; load when data layer throws exception; verify skeleton → cards transition; check aria-busy removal
- **oracle**: Skeleton cards replaced by workspace cards sorted by recency; error shows toast; empty shows "No sessions found"
- **risks**: Exception path returns toast but doesn't clear skeleton; no timeout on discovery call

### 2.2 Workspace card collapse/expand with lazy session loading
- **what**: Cards start collapsed; clicking header expands and lazy-loads sessions
- **how-to-reach**: Click workspace card header; first expand triggers `GET /partials/sessions?cwd=...`
- **probes**: Expand card → sessions load; collapse and re-expand (no re-fetch, data-loaded='true'); expand when /partials/sessions returns error; expand card with 0 sessions ("+ New session" shown)
- **oracle**: First expand fetches; subsequent toggles don't re-fetch; empty workspace shows new-session prompt
- **risks**: data-loaded set before fetch completes; double-fetch on rapid click; no retry on failure

### 2.3 Session row selection (multi-select)
- **what**: Clicking session rows toggles selection; action bar appears with count
- **how-to-reach**: Click on a session row (not on action buttons)
- **probes**: Select one row → action bar visible with "1 sessions selected"; select multiple; deselect; "Clear" button deselects all; verify selection lost after search/refresh
- **oracle**: Selected rows get `.selected` class; action bar shows count; Clear removes all selections
- **risks**: Selection is DOM-only (lost on htmx swap); no keyboard accessibility

### 2.4 Batch launch selected sessions
- **what**: Launches all selected sessions via POST /api/launch-batch
- **how-to-reach**: Select 1+ sessions → click "Launch selected" in action bar
- **probes**: Launch 1 session; launch 5 (no confirm); launch 6+ (confirm dialog); launch with mix of valid/invalid workspaces; verify toast message varies (success/warning/error)
- **oracle**: >5 shows confirm; partial failures show warning; all-fail shows error
- **risks**: No rate limiting; workspace-card selection passes no session_id (new session)

### 2.5 Single session resume
- **what**: Resumes a specific session via POST /api/launch
- **how-to-reach**: Hover session row → click "Resume" button
- **probes**: Resume valid session; resume with stale workspace (button should be hidden); verify toast feedback; verify launched terminal opens
- **oracle**: POST with session_id + workspace; success toast; Resume hidden on stale workspaces
- **risks**: No loading state on button during fetch; cryptic error messages

### 2.6 New session launch (per workspace)
- **what**: Launches fresh kiro-cli session in a workspace (no resume-id)
- **how-to-reach**: Click '+' button on card header; or POST /api/new-session
- **probes**: Launch new session on valid workspace; verify "New session launched" toast; verify it opens terminal without --resume-id
- **oracle**: POST /api/new-session with workspace only; success launches terminal
- **risks**: '+' button only visible on hover (no keyboard/touch access)

### 2.7 Debounced search
- **what**: Searches workspaces by folder name with 300ms debounce
- **how-to-reach**: Type in search input; results filter after 300ms pause
- **probes**: Type query matching a workspace name; type query with no matches (empty state); clear input (full list restored); verify only folder name matched (not session content); rapid typing (verify debounce)
- **oracle**: Matches workspace CWD folder name case-insensitively; empty query restores full list
- **risks**: Does NOT search session content despite _session_matches helper existing; htmx swap destroys selection state

### 2.8 Trust-all-tools toggle (topbar)
- **what**: Toggles global trust_all_tools setting via click
- **how-to-reach**: Click the "Trust all" toggle in topbar
- **probes**: Toggle on → verify config persisted; toggle off → verify; verify visual state (green dot); verify optimistic UI: if API fails, UI is out of sync
- **oracle**: POST /api/toggle-trust flips config.trust_all_tools; persists to TOML
- **risks**: Optimistic UI — classList toggled before fetch completes; no error handling

### 2.9 Autostart toggle (topbar)
- **what**: Toggles Windows autostart via topbar button
- **how-to-reach**: Click "Autostart" toggle in topbar
- **probes**: Toggle on → verify .lnk created; toggle off → verify .lnk removed; verify visual state matches actual state
- **oracle**: POST /api/autostart toggles autostart.is_enabled(); returns JSON {enabled: bool}
- **risks**: Optimistic UI ignores response; COM failure unhandled

### 2.10 Terminal preference selector (topbar)
- **what**: Dropdown to change terminal preference, saved immediately on change
- **how-to-reach**: Change the terminal dropdown in topbar
- **probes**: Select each option (Auto, WT, pwsh, cmd); verify POST /api/save-setting fires; verify config persisted; verify no "custom" option in topbar (only on settings page)
- **oracle**: Immediate save via /api/save-setting; no confirmation; value persisted
- **risks**: No feedback on save; no undo; no custom option in topbar

### 2.11 Pin/unpin workspace folder
- **what**: Pins a workspace to "Pinned workspaces" section
- **how-to-reach**: Hover workspace card → click pin button (📌)
- **probes**: Pin a workspace → verify it moves to pinned section; unpin → verify it returns to "All workspaces"; pin a stale workspace; verify refreshCards() fires after pin/unpin
- **oracle**: POST /api/pin-folder adds to config.pinned_folders; full refresh rebuilds layout
- **risks**: Full workspace refresh on every pin; pin button inaccessible via keyboard

### 2.12 Pin/unpin individual session
- **what**: Pins a session to the "Pinned sessions" flat list at top
- **how-to-reach**: Hover session row → click pin emoji button
- **probes**: Pin session → appears in "Pinned sessions" section; unpin → removed; pin session from stale workspace; pin multiple sessions
- **oracle**: POST /api/pin-session; refreshCards rebuilds; pinned sessions shown as flat list above workspace cards
- **risks**: Full refresh on every pin; pinned sessions disappear if metadata file deleted


### 2.13 Settings page (full form)
- **what**: Full settings form at /settings with terminal, trust, autostart options
- **how-to-reach**: Navigate to `/settings`
- **probes**: Verify all current config values pre-populated; change terminal to custom + enter template → save; toggle trust_all_tools; toggle autostart (fires independently of form); submit form; verify pinned_folders hidden field
- **oracle**: POST /api/settings saves all form fields; page re-renders with updated values
- **risks**: Custom terminal empty = auto-detect (no validation); autostart checkbox fires independently of form submit; pinned_folders field vestigial

### 2.14 Toast notifications
- **what**: Ephemeral notification messages after actions
- **how-to-reach**: Trigger any action (launch, pin, settings save)
- **probes**: Success toast appears and auto-dismisses after 4s; error toast styling; dismiss button works; rapid actions stack toasts; verify toast-warning renders (may lack styling)
- **oracle**: Toast container created on first toast; auto-remove after 4000ms; × button removes immediately
- **risks**: No stack limit; innerHTML injection (unsanitized); warning level may lack explicit CSS

### 2.15 Stale workspace detection
- **what**: Workspaces with non-existent paths shown with "missing" badge and no Resume buttons
- **how-to-reach**: Pin a workspace, then delete/rename the folder on disk
- **probes**: Verify "missing" badge on stale cards; verify Resume buttons hidden; verify card still interactive (expandable); verify stale pinned folder persists
- **oracle**: Path.exists() check; stale gets 0.6 opacity + badge; Resume hidden
- **risks**: Sync check happens once at render (no live update); network paths slow

### 2.16 Skeleton loading state
- **what**: Shimmer cards shown while workspace data loads
- **how-to-reach**: Load page — observe initial 3 skeleton-card divs
- **probes**: Verify skeletons visible on page load; verify replaced on data load; verify aria-busy="true" during load and removed after; simulate slow backend (artificial delay) — skeletons persist
- **oracle**: 3 skeleton cards with animation; replaced by hx-swap="innerHTML" on /partials/workspaces response
- **risks**: If htmx request fails, skeletons remain forever (no timeout fallback)

### 2.17 Pinned sessions section (flat list)
- **what**: Flat list of pinned sessions shown above workspace cards
- **how-to-reach**: Pin at least one session, then reload page
- **probes**: Verify "Pinned sessions" label appears; verify pinned sessions shown as rows (not inside workspace cards); verify sessions from different workspaces can be pinned; verify removal from pinned list
- **oracle**: _render_pinned_sessions reads metadata files for pinned IDs; rendered above workspace cards
- **risks**: Sync file I/O in async endpoint; no deduplication if same session in workspace card below

### 2.18 Generic save-setting API
- **what**: POST /api/save-setting sets any config attribute by name
- **how-to-reach**: POST with `{key: "terminal_command", value: "wt"}`
- **probes**: Set valid key; set invalid key (hasattr returns False → no-op); set wrong type (string where bool expected); set pinned_folders to non-list; verify return {ok: true}
- **oracle**: Any key matching Config dataclass attribute is writable; no type validation
- **risks**: No allowlist beyond hasattr; can set any field to any type; no sanitization

### 2.19 Workspace deduplication
- **what**: Workspaces with same normalized path merged into single card
- **how-to-reach**: Have sessions from same workspace with different casing stored
- **probes**: Same workspace different casing → single card; verify session count aggregated; verify pinned folder merged with discovered workspace
- **oracle**: _normalize_path used as dedup key; first-seen original path used for display
- **risks**: Forward/back slash not normalized (possible false duplicates)

### 2.20 Session sorting (pinned first)
- **what**: Pinned sessions appear at top of session list within a workspace card
- **how-to-reach**: Pin a session, then expand its workspace card
- **probes**: Verify pinned session at top; verify unpinned sessions in chronological order below; pin multiple → both at top
- **oracle**: _sort_pinned_first: pinned set to top, rest preserves original order
- **risks**: Pinning disrupts time-based ordering without visual indicator

### 2.21 Accessibility: skip-link
- **what**: Skip-to-content link for keyboard users
- **how-to-reach**: Tab key on page load focuses skip-link
- **probes**: Verify skip-link visible on focus; verify it targets #cards-area; verify other keyboard navigation paths
- **oracle**: Hidden off-screen until focused; links to #cards-area
- **risks**: Target may still be loading; no other ARIA landmarks

### 2.22 Session preview content
- **what**: Session rows show truncated first_prompt and last_reply_tail
- **how-to-reach**: Expand a workspace card with sessions
- **probes**: Verify first_prompt shown (truncated at 100 chars); verify last_reply_tail shown (truncated at 80 chars); verify HTML-safe (Jinja2 autoescaping); session with empty prompts
- **oracle**: Template renders `session.first_prompt[:100]` and `session.last_reply_tail[:80]`
- **risks**: May cut mid-word; depends on Jinja2 autoescaping for XSS safety

### 2.23 Responsive grid layout
- **what**: Cards area uses CSS grid that reflows based on viewport width
- **how-to-reach**: Resize browser window; test at different breakpoints
- **probes**: Wide viewport (3 columns); medium (2 columns); narrow (1 column); below 380px (overflow check); verify section-label spans full width
- **oracle**: `repeat(auto-fill, minmax(380px, 1fr))` grid; reflows automatically
- **risks**: <380px viewport causes horizontal overflow; section-label grid-column:1/-1

### 2.24 Dark theme
- **what**: Always-dark UI via CSS custom properties
- **how-to-reach**: Load page — always dark regardless of system preference
- **probes**: Verify dark background and light text; verify no prefers-color-scheme media query; verify readability of all UI states (selected, hover, stale)
- **oracle**: Static dark theme; no light mode; CSS variables for colors
- **risks**: No system preference detection; no high-contrast support

---

## 3. Launcher


### 3.1 Terminal auto-detection
- **what**: Detects available terminal (wt > pwsh > cmd) via shutil.which
- **how-to-reach**: Call `launcher.detect_terminal("")` or launch with no config override
- **probes**: Verify wt detected first if available; verify pwsh fallback; verify cmd fallback; verify None returned when nothing found (mock PATH)
- **oracle**: Priority order: wt > pwsh > cmd; returns full path from shutil.which; None if none found
- **risks**: Hardcoded priority; unexpected binaries on PATH

### 3.2 Config-override terminal selection
- **what**: Non-empty config_override bypasses auto-detection entirely
- **how-to-reach**: Call `detect_terminal("custom_path")`
- **probes**: Override with valid path; override with non-existent path (returns it anyway, no validation); override with empty string (triggers auto-detect)
- **oracle**: Non-empty override returned directly; no existence/executable check
- **risks**: Typo passes detection but fails at Popen

### 3.3 Session launch (single)
- **what**: Launches a kiro-cli session in a detected terminal
- **how-to-reach**: Call `launcher.launch_session(cwd, session_id, trust_all, terminal_override)`
- **probes**: Valid launch; no terminal found; cwd doesn't exist; invalid session_id format; Popen raises OSError; verify never raises (always returns LaunchResult)
- **oracle**: Returns LaunchResult(success, session_id, workspace, error); never raises
- **risks**: Fire-and-forget; no kiro-cli pre-validation; race between exists check and Popen

### 3.4 Session resume (--resume-id)
- **what**: Appends --resume-id flag when session_id provided
- **how-to-reach**: Launch with non-None session_id
- **probes**: Valid session_id adds `--resume-id <id>`; None session_id omits flag; verify the actual command built
- **oracle**: `kiro-cli chat --resume-id <session_id>` in args
- **risks**: No validation that session_id exists in kiro-cli; permissive regex

### 3.5 Trust-all-tools flag (-a)
- **what**: Appends -a flag when trust_all=True
- **how-to-reach**: Launch with trust_all=True
- **probes**: trust_all=True adds `-a`; trust_all=False omits; verify combined with --resume-id
- **oracle**: `-a` appended to kiro_args
- **risks**: Security-sensitive; no confirmation gate at library level

### 3.6 Batch launch
- **what**: Launches multiple sessions sequentially, never aborts on single failure
- **how-to-reach**: Call `launcher.launch_batch(sessions_list, trust_all, terminal_override)`
- **probes**: Batch of 3 valid sessions; batch with 1 invalid (missing workspace) among valid ones; empty batch; batch with duplicate entries
- **oracle**: Returns list of LaunchResult; failures don't abort remaining; sequential
- **risks**: Sequential blocking; no rate limiting; no dedup; KeyError on missing 'workspace' key

### 3.7 Session ID validation
- **what**: Validates session_id against `^[\w\-]+$` regex
- **how-to-reach**: Launch with various session_id values
- **probes**: Valid ID (alphanumeric + hyphens); ID with special chars (rejected); empty string (falsy, skips validation); very long ID (accepted)
- **oracle**: Regex match required; failure returns LaunchResult with "Invalid session ID format"
- **risks**: Permissive regex; doesn't enforce actual kiro-cli format; empty string bypasses

### 3.8 Windows Terminal (wt) command building
- **what**: Builds `[wt, -d, cwd, --, ...kiro_args]` command
- **how-to-reach**: Detect terminal where stem is 'wt'; inspect built command
- **probes**: Normal path; path with spaces; verify `--` separator present
- **oracle**: `[terminal, "-d", cwd, "--", "kiro-cli", "chat", ...]`
- **risks**: Paths with spaces (wt handles via list args); version compatibility

### 3.9 PowerShell (pwsh) command building
- **what**: Builds `[pwsh, -NoExit, -Command, "Set-Location ...; & kiro-cli ..."]`
- **how-to-reach**: Detect terminal where stem is 'pwsh'
- **probes**: Normal path; path with single quotes (breaks command); verify -NoExit present
- **oracle**: Script string with Set-Location and & invocation
- **risks**: Single quotes in path break PowerShell command; args with spaces joined unsafely

### 3.10 CMD fallback command building
- **what**: Builds `[terminal, /k, 'cd /d "cwd" && kiro-cli ...']` for cmd or unknown terminals
- **how-to-reach**: Detect terminal where stem is not 'wt' or 'pwsh'
- **probes**: Normal path; path with double quotes; verify shell string construction; non-cmd terminal falling into this branch
- **oracle**: Shell string with cd /d and && chaining
- **risks**: Shell injection via unquoted kiro_args; double-quote in path breaks quoting; non-cmd terminals get cmd syntax

### 3.11 Custom terminal template
- **what**: Replaces {cwd} and {cmd} placeholders in custom terminal string
- **how-to-reach**: Set terminal_command to a template like `alacritty -e {cmd}` with placeholder
- **probes**: Template with both placeholders; template with only {cwd}; template with only {cmd}; path with spaces in {cwd} (split breaks it); missing placeholder
- **oracle**: Placeholders replaced then split on whitespace
- **risks**: split() breaks paths with spaces; no shell-aware splitting; missing {cmd} means kiro-cli never invoked

### 3.12 Platform-specific process spawning
- **what**: Uses CREATE_NEW_CONSOLE on Windows, start_new_session on others
- **how-to-reach**: Any successful launch_session call
- **probes**: Verify process created with new console (Windows); verify subprocess detached from parent
- **oracle**: Popen with platform-specific flags; no stdout/stderr capture
- **risks**: Fire-and-forget; no PID tracking; terminal errors invisible

### 3.13 LaunchResult dataclass
- **what**: Structured return with success, session_id, workspace, error fields
- **how-to-reach**: Any launch_session call
- **probes**: Verify success=True has empty error; verify success=False has descriptive error; verify all fields populated
- **oracle**: Dataclass with bool, optional str, str, str fields
- **risks**: success=True only means Popen didn't raise; no PID in result

---

## 4. Config


### 4.1 Config dataclass with typed defaults
- **what**: Dataclass providing default values for all config fields
- **how-to-reach**: Instantiate `Config()` with no arguments
- **probes**: Verify defaults: trust_all_tools=False, use_pywebview=True, terminal_command="", pinned_folders=[], pinned_sessions=[]; verify mutable default isolation (two Config() instances don't share lists)
- **oracle**: Each field has documented default; list fields use field(default_factory=list)
- **risks**: No dynamic schema; lists accept any string

### 4.2 Load config from TOML
- **what**: Reads config.toml, returns Config with values; falls back to defaults on error
- **how-to-reach**: Call `load_config()` with various file states
- **probes**: Valid TOML with all fields; valid TOML with subset of fields (missing get defaults); missing file (defaults); corrupt TOML (defaults); file with unknown keys (dropped silently); TOML with wrong types (accepted without validation)
- **oracle**: Missing file → defaults; corrupt → defaults; unknown keys dropped; known keys populate Config
- **risks**: Silent fallback on corruption; no type validation; wrong types accepted

### 4.3 Save config atomically
- **what**: Atomic write via .tmp → fsync → os.replace
- **how-to-reach**: Call `save_config(config)`
- **probes**: Normal save; verify .tmp doesn't persist after success; verify original untouched on write failure; verify file content is valid TOML after save; concurrent saves from multiple threads
- **oracle**: .tmp written, fsynced, replaced atomically; failure cleans up .tmp
- **risks**: OneDrive may conflict with os.replace; no backup of previous config

### 4.4 Thread-safe access via module lock
- **what**: threading.Lock protects all load/save operations
- **how-to-reach**: Concurrent calls to load_config/save_config from web server threads
- **probes**: Concurrent reads (should not block each other? — actually they do, single lock); concurrent read+write; verify no data corruption under contention
- **oracle**: Lock acquired before any file I/O; released after
- **risks**: Per-process only (multiple processes can race); non-reentrant; lock held during slow I/O

### 4.5 Config directory auto-creation
- **what**: save_config creates CONFIG_DIR if it doesn't exist
- **how-to-reach**: Delete CONFIG_DIR then call save_config
- **probes**: Verify directory created with parents; verify existing directory is no-op; verify correct path under LOCALAPPDATA
- **oracle**: mkdir(parents=True, exist_ok=True) before write
- **risks**: LOCALAPPDATA unset produces fallback path; mkdir inside lock blocks all access

### 4.6 trust_all_tools setting
- **what**: Global boolean controlling -a flag on all launches
- **how-to-reach**: Toggle via UI or API
- **probes**: Set True → verify launches include -a; set False → verify no -a; verify persisted across restarts
- **oracle**: Boolean in TOML; default False
- **risks**: Global (no per-workspace); no confirmation on enable

### 4.7 use_pywebview setting
- **what**: Boolean controlling native window vs browser mode
- **how-to-reach**: Settings page checkbox
- **probes**: Toggle and verify persisted; verify current behavior (appears unused in web.py — may be dead code)
- **oracle**: Boolean in TOML; default True
- **risks**: May require restart; pywebview import failure not handled gracefully

### 4.8 terminal_command setting
- **what**: String specifying terminal preference or empty for auto-detect
- **how-to-reach**: Topbar dropdown or settings page
- **probes**: Set to "wt" → verify launch uses wt; set to "" → verify auto-detect; set to custom template → verify placeholder replacement; set to invalid path → verify launch failure message
- **oracle**: Empty = auto-detect; non-empty passed to detect_terminal as override
- **risks**: No validation of path existence; typo causes silent launch failure

### 4.9 pinned_folders list
- **what**: List of workspace paths pinned for quick access
- **how-to-reach**: Pin/unpin via API; settings form hidden field
- **probes**: Pin folder → verify in config; unpin → verify removed; pin same folder twice (should be idempotent — checked in API); verify paths stored as-is (no normalization in config)
- **oracle**: List of strings in TOML array
- **risks**: No normalization (duplicates possible with different casing); no existence check; unbounded

### 4.10 pinned_sessions list
- **what**: List of session IDs pinned for quick access
- **how-to-reach**: Pin/unpin via API
- **probes**: Pin session → verify in config; unpin → verify removed; verify idempotent pin; stale session IDs persist
- **oracle**: List of strings in TOML array
- **risks**: No validation against actual sessions; unbounded growth

### 4.11 CONFIG_PATH location derivation
- **what**: Path computed from LOCALAPPDATA at module import time
- **how-to-reach**: Import config module; inspect CONFIG_PATH
- **probes**: Verify path is `%LOCALAPPDATA%\kiro-orchestrator\config.toml`; verify LOCALAPPDATA used
- **oracle**: LOCALAPPDATA env → Path / "kiro-orchestrator" / "config.toml"
- **risks**: Computed once at import; env changes invisible; non-Windows fallback wrong

### 4.12 Unknown-key tolerance and destructive round-trip
- **what**: Unknown keys in TOML are silently dropped on load and lost on save
- **how-to-reach**: Manually add an unknown key to config.toml; load then save
- **probes**: Add `my_custom_key = "foo"` to config.toml; load_config (should work); save_config; verify my_custom_key is gone from file
- **oracle**: Unknown keys filtered by `{k:v for k,v in data.items() if k in fields}`; save writes only dataclass fields
- **risks**: Manual edits lost; no warning; no migration path for renamed keys

---

## 5. Autostart


### 5.1 Enable autostart
- **what**: Creates .lnk shortcut in Windows Startup folder pointing to pythonw.exe -m kiro_orchestrator
- **how-to-reach**: Call `autostart.enable()` or toggle via API/UI
- **probes**: Enable → verify .lnk exists at expected path; verify shortcut TargetPath is pythonw.exe sibling of sys.executable; verify Arguments is "-m kiro_orchestrator"; verify WorkingDirectory is home; enable when already enabled (idempotent — overwrites)
- **oracle**: WScript.Shell COM creates shortcut; target = pythonw.exe next to python.exe
- **risks**: pythonw.exe may not exist in venv; COM failure unhandled; APPDATA empty → invalid path

### 5.2 Disable autostart
- **what**: Removes .lnk from Startup folder
- **how-to-reach**: Call `autostart.disable()` or toggle via API/UI
- **probes**: Disable when enabled → .lnk removed; disable when already disabled → no-op (missing_ok=True); verify no error
- **oracle**: unlink(missing_ok=True) — always safe
- **risks**: PermissionError if file locked by AV/Explorer indexer

### 5.3 Query autostart status
- **what**: Returns True if the .lnk file exists at the expected path
- **how-to-reach**: Call `autostart.is_enabled()`
- **probes**: Enabled (file exists) → True; disabled (no file) → False; different .lnk content at same path → still True (existence-only check)
- **oracle**: `_shortcut_path().exists()` — pure existence check
- **risks**: Stale shortcut pointing elsewhere still returns True

### 5.4 Startup shortcut target resolution
- **what**: Computes pythonw.exe path as sibling of sys.executable
- **how-to-reach**: Inspect shortcut after enable()
- **probes**: In venv → verify target points to venv's pythonw.exe; verify it actually exists on disk
- **oracle**: `Path(sys.executable).parent / "pythonw.exe"`
- **risks**: venv may not have pythonw.exe; frozen executables break this assumption

### 5.5 Startup directory path derivation
- **what**: Module-level constant STARTUP_DIR from APPDATA env var
- **how-to-reach**: Import autostart module; inspect STARTUP_DIR
- **probes**: Verify path matches `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`; verify APPDATA is set
- **oracle**: APPDATA env → standard Windows Startup folder path
- **risks**: Computed at import time; empty APPDATA produces relative path

---

## 6. System Tray (user-assisted)

### 6.1 Tray icon display
- **what**: Blue 16x16 icon with white "K" appears in system tray
- **how-to-reach**: Launch `kiro-orchestrator --foreground`; observe system tray
- **probes**: Verify icon appears; verify tooltip shows "Kiro Orchestrator" on hover; verify icon is readable (not garbled)
- **oracle**: pystray.Icon with 16x16 RGBA image; tooltip "Kiro Orchestrator"
- **risks**: Font fallback if arial.ttf unavailable; no DPI scaling

### 6.2 Open dashboard (default action)
- **what**: Double-click tray icon opens browser to server_url
- **how-to-reach**: Double-click tray icon (or single-click "Open" menu item)
- **probes**: Double-click → browser opens; verify URL is correct (dynamic port); verify page loads successfully
- **oracle**: webbrowser.open(server_url) called
- **risks**: No default browser configured; server not yet ready

### 6.3 Trust All Tools toggle (tray menu)
- **what**: Checkmark menu item that toggles trust_all_tools in config
- **how-to-reach**: Right-click tray → click "Trust All Tools"
- **probes**: Toggle on → verify checkmark visible; toggle off → verify no checkmark; verify config file updated; verify web UI reflects change on next load
- **oracle**: load_config → flip → save_config; in-memory config updated; checkmark via lambda
- **risks**: TOCTOU between load and save; no file locking across processes

### 6.4 Open log file
- **what**: Opens orchestrator.log with OS default handler
- **how-to-reach**: Right-click tray → click "Logs"
- **probes**: Click Logs when log file exists → opens in editor; click when log file doesn't exist → silent no-op; verify log file location
- **oracle**: os.startfile(log_path) if exists; no action if not
- **risks**: os.startfile is Windows-only; large log files may hang editor; no feedback if file missing

### 6.5 Quit application
- **what**: Stops tray icon and signals shutdown
- **how-to-reach**: Right-click tray → click "Quit"
- **probes**: Click Quit → tray icon disappears; verify process exits cleanly; verify server stops (port freed)
- **oracle**: _shutdown_event.set(); icon.stop(); main thread unblocks; server.should_exit = True
- **risks**: No confirmation dialog; 5s timeout on server thread join; in-flight requests killed

### 6.6 Shutdown event coordination
- **what**: Module-level threading.Event shared with main process for coordinated shutdown
- **how-to-reach**: Quit from tray; check event from other threads
- **probes**: After quit: verify get_shutdown_event().is_set() returns True
- **oracle**: Singleton Event; set on Quit; consumers can wait/poll
- **risks**: Single instance per process; no reset mechanism

### 6.7 Icon image generation
- **what**: Programmatically creates the tray icon image
- **how-to-reach**: Internal to run_tray; observe icon quality
- **probes**: Verify icon renders as blue square with white K; verify fallback font works if arial.ttf missing
- **oracle**: PIL Image 16x16 RGBA; blue fill (60,120,220); white "K" at (3,1) with arial.ttf size 11
- **risks**: Font path resolution platform-dependent; fallback bitmap font misaligns

### 6.8 Tooltip display
- **what**: Hover tooltip shows "Kiro Orchestrator"
- **how-to-reach**: Hover over tray icon
- **probes**: Verify tooltip text matches "Kiro Orchestrator"
- **oracle**: Static string passed to pystray.Icon constructor
- **risks**: No dynamic status info; some Linux implementations ignore tooltip

---

## 7. Main/Lifecycle

### 7.1 CLI --foreground flag
- **what**: Runs server+tray attached to current terminal
- **how-to-reach**: `kiro-orchestrator --foreground` or `kiro-orchestrator -f`
- **probes**: Verify process stays attached; verify Ctrl+C behavior; verify log output to file (not console)
- **oracle**: argparse flag; triggers _run_foreground()
- **risks**: No terminal validation; Ctrl+C handling relies on uvicorn defaults

### 7.2 Default detached background launch
- **what**: Re-execs itself detached then parent exits
- **how-to-reach**: `kiro-orchestrator` (no flags)
- **probes**: Verify parent exits after print; verify child process running; verify CREATE_NO_WINDOW flag (no visible console)
- **oracle**: Popen with CREATE_NEW_PROCESS_GROUP + CREATE_NO_WINDOW; parent prints message and exits
- **risks**: Orphaned child if crash after parent exits; no PID file; no health check

### 7.3 Single-instance guard (mutex)
- **what**: Only one instance can run; second silently exits
- **how-to-reach**: Launch twice — second should exit(0)
- **probes**: First launch succeeds; second launch exits with code 0; verify no error message on second launch; verify mutex released on process exit
- **oracle**: CreateMutexW; ERROR_ALREADY_EXISTS (183) → sys.exit(0)
- **risks**: Silent exit (no user feedback); Windows-only; hung process holds mutex indefinitely

### 7.4 Dynamic port allocation
- **what**: Server binds to port 0 (OS-assigned ephemeral port)
- **how-to-reach**: Start foreground; observe bound port in logs
- **probes**: Verify port is non-zero; verify server accessible at assigned port; verify port changes between restarts
- **oracle**: uvicorn.Config(port=0) → OS assigns; port extracted from server.servers[0].sockets[0]
- **risks**: Port not persisted; external tools can't discover it; firewall can't pre-configure

### 7.5 Server startup with ready-event sync
- **what**: Main thread waits up to 10s for server to bind
- **how-to-reach**: Start foreground; observe startup time
- **probes**: Normal startup (fast, <1s); verify ready_event times out → exit(1) with error message; verify port extracted correctly after ready
- **oracle**: server.startup monkey-patched to set ready_event; main blocks on ready_event.wait(timeout=10)
- **risks**: 10s timeout on slow machines; monkey-patching uvicorn internals fragile

### 7.6 Tray on main thread
- **what**: Tray runs on main thread (OS requirement for pystray)
- **how-to-reach**: Start foreground; verify tray blocks main thread
- **probes**: Verify tray icon appears after server ready; verify main thread blocked until quit
- **oracle**: run_tray(server_url, config) called after server ready
- **risks**: Exception in tray kills process; no watchdog

### 7.7 Graceful shutdown
- **what**: Tray quit triggers server shutdown with 5s timeout
- **how-to-reach**: Quit from tray; observe shutdown sequence
- **probes**: Quit → server stops; port freed; process exits 0; verify within 5s timeout; verify behavior if server hangs past 5s
- **oracle**: server.should_exit = True; server_thread.join(timeout=5); sys.exit(0)
- **risks**: 5s hard-coded; in-flight requests killed; hung server → abrupt exit

### 7.8 File logging
- **what**: INFO-level logging to CONFIG_DIR/orchestrator.log
- **how-to-reach**: Start foreground; check log file
- **probes**: Verify log file created; verify INFO messages written; verify format (timestamp + level + name + message); verify log grows on activity
- **oracle**: FileHandler to CONFIG_DIR/orchestrator.log; INFO level; format with timestamp
- **risks**: No log rotation; unbounded growth; failure to create log crashes process

### 7.9 Cross-platform detach fallback
- **what**: Non-Windows uses start_new_session=True instead of Windows flags
- **how-to-reach**: N/A on Windows (code path exists but won't be tested here)
- **probes**: Code inspection only (this is a Windows test environment)
- **oracle**: Platform branch in _relaunch_detached
- **risks**: Non-Windows foreground mode crashes on WinDLL call in _single_instance_guard

### 7.10 Config loading at startup
- **what**: Config loaded once at foreground startup for tray initialization
- **how-to-reach**: Start foreground; observe config-dependent behavior (trust toggle state)
- **probes**: Verify tray reflects config state; verify corrupt config → defaults used (process doesn't crash)
- **oracle**: load_config() called after mutex guard passes
- **risks**: Unhandled exception on corrupt config could crash; config not watched for changes

