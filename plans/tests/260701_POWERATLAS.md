# Test Plan: PowerAtlas (full-app refresh)

approach: organic
created: 2026-07-01
last_executed: 2026-07-01
last_run_summary: "32 findings (1 High, 14 Medium, 16 Low verified; 1 plausible; H6 refuted/dropped). Deep run + GUI + lifecycle completion. Config/Autostart snapshot+restore. GUI covered via standalone Playwright (headless): +1 Low (stale action bar on htmx swap); tabs/search/tooltip/expand/pin/aria-busy all pass, 0 console errors. Lifecycle covered (authorized kill/restart): --stop/--restart/bare-launch/single-instance-guard/flag-precedence all verified; H8 0.5s race plausible-not-reproduced. Remaining scoped-out: native tray/peek clicks + all Linux paths."
supersedes: plans/tests/260618_KIRO_ORCHESTRATOR.md
scope: automatable-only (native tray/peek clicks and Linux-specific paths are scoped out — see Coverage)

PowerAtlas (internally `power-atlas`; formerly `kiro-orchestrator`) is a cross-platform desktop
system-tray app + FastAPI/htmx web dashboard that discovers, resumes, and batch-launches AI-coding
sessions from two providers: **kiro-cli** and **Claude Code**. This plan reflects the current
(2026-07-01) source: multi-provider data layer, three-panel UI, custom launchers, extracted icons,
peek overlay, and per-provider settings.

---

## Environment & Resource Constraints

- **This machine is Windows 11.** Linux code paths are code-inspection-only (cannot be exercised).
- **Web UI + Data + Config** share one browser instance and one server process (start uvicorn on a
  dynamic port; drive via browser MCP). Serialize browser work.
- **Launcher** spawns real terminal windows and detached processes — test in isolation, close spawned
  windows after. Prefer asserting on built command lists (library) over actually spawning where possible.
- **Config** mutates the real `%LOCALAPPDATA%\power-atlas\config.toml` — snapshot before, restore after.
- **Icons** writes PNGs to `%LOCALAPPDATA%\power-atlas\icons\` — snapshot/clean the dir after.
- **Autostart** mutates the real Start Menu Startup folder (`PowerAtlas.lnk`) — snapshot enabled/disabled
  state before, restore after. No reboot needed (existence-only check).
- **Main/Lifecycle** is process-level (named mutex `PowerAtlasMutex`, PID file, port binding) — isolate;
  do not run against a live user instance.
- **Real provider data** on disk (read-only): kiro-cli at `~/.kiro/sessions/cli/` + `%LOCALAPPDATA%\Kiro-Cli\data.sqlite3`;
  Claude Code at `~/.claude/projects/` + `~/.claude/history.jsonl`. Never modify or delete.
- **Existing unit coverage is strong** (254 test functions in `tests/`). Runtime testing targets what unit
  tests can't reach: interaction gates, cross-provider behavior, real-data quirks, and the risk hotspots below.

## Risk hotspots (priority probe targets)

These are behaviors whose code structure predicts a defect. Confirm or refute during run mode.

- **H1 — Pinned-folder provider loss (Config, High).** `/api/settings` and `/api/save-setting` write
  `pinned_folders` as `list[str]`; the load-time migration then hardcodes every entry to `kiro-cli`.
  A pinned Claude Code folder should silently revert to kiro-cli on reload. (`config.py:60-62`, `web.py:130`, `web.py:497-514`)
- **H2 — Cross-provider cache asymmetry (Data, Med).** Kiro tail (5s) + first-prompt (60s) are cached;
  the Claude adapter caches neither — identical hover tooltips differ in cost/freshness. (`data_kiro.py:254-336` vs `data_claude.py:327-402`)
- **H3 — Unlocked `_cache` race (Data, Med).** The module-level 30s discovery cache has no lock; htmx
  requests, the 30s background refresh, and the warmup thread all touch it. (`data.py:15,144-178`)
- **H4 — cmd.exe injection surface (Launcher, Med).** Only `cwd` is metachar-guarded; joined command
  args are interpolated into a `/k` shell string. (`launcher.py:283-290`)
- **H5 — `shlex.split` on Windows (Launcher, Med).** POSIX splitting mangles backslash paths in
  `default_args`; malformed quoting raises `ValueError` the `OSError`-only handler won't catch. (`launcher.py:141-142`)
- **H6 — Toast `innerHTML` XSS (Web, Med).** Server-supplied error text injected via `innerHTML`. (`index.html` showToast)
- **H7 — Claude path-index collision (Data, Med).** `_path_to_folder_name` maps every non-alnum char to
  `-`, collapsing distinct real paths onto one folder key. (`data_claude.py:33-38`)
- **H8 — `--restart` 0.5s race (Main, Low/Med).** Fixed sleep; if the old instance still holds the mutex,
  the new guard silently `exit(0)` and nothing restarts. (`__main__.py:319-326`)
- **H9 — GDI handle leak (Icons, Low).** `hbm_mask`/`hbm_color` freed only on the success path. (`icons.py:140-188`)
- **H10 — Cross-provider lexical sort (Data, Low/Med).** Kiro string timestamps vs Claude ISO-UTC compared
  lexically → mis-ordered workspace recency across providers. (`data.py:176`)

---

## 1. Data layer (`data.py`, `data_kiro.py`, `data_claude.py`)

### 1.1 Cross-provider workspace discovery with counts
- **what**: `discover_workspaces_with_counts(provider)` merges kiro + claude workspaces into `(cwd, count, updated_at, provider)` tuples, cached 30s.
- **how-to-reach**: `data.discover_workspaces_with_counts()` directly; or `GET /partials/workspaces?provider=all|kiro-cli|claude-code`.
- **probes**: provider=None (all) vs single provider; both providers present; one provider's dir absent; verify 30s cache hit returns same result then re-scan after TTL; concurrent calls during background refresh (H3); a provider raising inside `discover_workspaces` (bare `except: continue` → 0 workspaces, indistinguishable from empty).
- **oracle**: sorted by `updated_at` desc; unavailable providers skipped; results cached per `workspaces_with_counts:<provider|all>` key.
- **risks**: H3 unlocked `_cache`; H10 lexical cross-provider sort; 30s stale window hides just-created workspaces; broken provider silently yields zero.

### 1.2 Provider availability gating
- **what**: `available_providers()` filters the registry by each adapter's `is_available()` (kiro: `~/.kiro/sessions/cli` is a dir; claude: `~/.claude/projects` is a dir with ≥1 entry).
- **how-to-reach**: `data.available_providers()`; drives provider-tab rendering and launcher tiles.
- **probes**: both available; kiro dir missing; claude dir empty vs non-empty; claude `iterdir` OSError (returns False → provider vanishes); verify tab bar only renders when >1 provider available.
- **oracle**: only providers with on-disk data appear; order follows registry insertion (kiro-cli, claude-code).
- **risks**: re-stats disk every call (no memoization) — OneDrive latency; transient unreadable dir silently drops a provider.

### 1.3 Compound-keyed SessionCache
- **what**: thread-safe in-memory cache keyed `(provider, normalize_path(cwd))`, storing sessions + file stats + original cwd.
- **how-to-reach**: `data.get_sessions(cwd, provider)`; `session_cache.clear()` via `/api/refresh`.
- **probes**: miss→load→put→hit; `clear()` empties all four dicts; two real paths normalizing to same key collide (last-writer-wins on `_original_cwds`); mutate a returned Session and confirm it leaks to next caller (shallow `list()` copy); unbounded growth across many workspaces (no eviction).
- **oracle**: `get()` returns a shallow copy or None; `last_refresh` stamped on every `put()`.
- **risks**: no TTL/eviction; shared Session objects; single global `last_refresh` string not per-key.

### 1.4 Path normalization
- **what**: `_normalize_path` — Windows: `/`→`\`, strip trailing `\`, casefold; POSIX: strip trailing `/`.
- **how-to-reach**: internal to every cache key, discovery grouping, cwd filter.
- **probes**: trailing slash; mixed slashes `C:/foo\bar`; case differences; unicode; verify Windows collapses slash+case, POSIX does NOT (documented gap); UNC / `\\?\` prefixes treated as distinct.
- **oracle**: Windows canonicalizes slash+case; forward-vs-back slash NOT normalized on Linux.
- **risks**: Linux slash/case mismatch → duplicate workspaces + missed cache hits; casefold collision can merge distinct paths; no symlink/realpath resolution.

### 1.5 Get sessions for a workspace (provider-scoped)
- **what**: `get_sessions(cwd, provider)` cache-first, dispatches to the provider adapter's `load_sessions` on miss.
- **how-to-reach**: `GET /partials/sessions?cwd=...&provider=...` (card expand / warmup).
- **probes**: cold load (full dir scan, synchronous — time it on a large session dir); unknown provider name → `[]` (indistinguishable from empty workspace); workspace with 0 sessions; adapter raising internally.
- **oracle**: cache miss → disk load → populate → return; unknown provider silently `[]`.
- **risks**: cold call blocks the request thread; typo'd provider silently empty.

### 1.6 Stale-entry refresh (change detection)
- **what**: `refresh_stale_entries()` walks loaded (provider, cwd) pairs, reloads only when a file's mtime/size changed or a new matching session file appeared.
- **how-to-reach**: `data.refresh_stale_entries()`; runs every 30s via `_background_refresh`.
- **probes**: unchanged files (stat-only, no reload); edit a session then verify reload; delete a file (treated stale); OneDrive-locked file mid-scan (stat raises → treated as deleted → needless reload); same-size in-place edit (missed); measure O(files × loaded_cwds) new-file re-glob cost.
- **oracle**: reload iff stale True; per-cwd errors skipped (blanket `except`).
- **risks**: mtime+size misses same-size edits; OneDrive locks force spurious reloads; persistently-erroring cwd never refreshes and never reports.

### 1.7 Warmup (pinned folders + pinned sessions)
- **what**: `warmup_all` runs discovery, preloads pinned folders under every available provider, and resolves pinned sessions to their workspaces (kiro metadata scan only).
- **how-to-reach**: startup thread in `_run_foreground`; `POST /api/refresh`; `GET /partials/pinned-sessions`.
- **probes**: pinned folder existing for only one provider (other yields empty cache entry); pinned Claude session in an un-warmed workspace (never resolved — kiro-only scan, asymmetric); hundreds of metadata files (early-break once all found); corrupt metadata file silently dropped.
- **oracle**: pinned folders loaded per provider; pinned kiro sessions resolved via `SESSION_DIR` glob.
- **risks**: pinned Claude sessions can't be located; wasted scans loading a folder under the wrong provider; TOCTOU on `exists()`→load.

### 1.8 Session-tail + first-prompt dispatch (H2)
- **what**: `get_session_tail` / `get_first_prompt` route to kiro or claude; kiro ignores cwd (global dir), claude requires cwd (resolve folder). The endpoint additionally passes `session_id` (from the `sid` query param) and `last_prompt` (from the session cache, or empty string on cache miss) to the template context. `first_prompt`, `last_prompt`, and all `messages` items are rendered through `mistune.create_markdown(escape=True)` before passing to the template (markdown rendering uses `mistune>=3.3.0,<4`; `escape=True` causes HTML entity-encoding, e.g. `<script>` → `&lt;script&gt;`). The `tail-empty` early-return guard fires only when ALL THREE of `messages`, `first_prompt`, and `last_prompt` are empty — a session with only a `last_prompt` will not short-circuit.
- **how-to-reach**: `GET /partials/session-tail?sid&provider&cwd` (300ms hover tooltip).
- **probes**: kiro tail with empty cwd (tolerated) vs claude tail with empty/wrong cwd (silently `[]`); repeated hovers — confirm kiro caches (5s/60s) but claude re-reads every time (H2); 128KB tail truncation dropping newest messages in a huge session; kiro `"toolUse"` substring skip dropping a legit message mentioning the literal; `last_prompt` populated — verify it appears in rendered output under "User last message" label; `last_prompt` empty (cache miss, `session_cache.get` returns None) — verify "User last message" label appears with `—` (em dash) fallback; XSS probe: `<script>alert(1)</script>` as message input — verify entity-encoded to `&lt;script&gt;` in output (never raw `<script>`); JS-URL probe: `[click](javascript:alert(1))` as message input — verify `javascript:alert` is not present in rendered output (mistune's HTMLRenderer.safe_url() replaces `javascript:` hrefs with `#harmful-link` unconditionally).
- **oracle**: oldest-first assistant messages; kiro `.history`-preferred first prompt; not-found → `[]`/`""`. `session_id` and `last_prompt` passed to template; all text fields HTML-entity-encoded via mistune before template rendering; output safe for Jinja2 `| safe` filter.
- **risks**: H2 asymmetric caching; inconsistent cwd contract; textual `"toolUse"` heuristic fragility; negative-cache blanks kiro tooltip for 60s.

### 1.9 Kiro discovery (metadata + sqlite union)
- **what**: `data_kiro.discover_workspaces` globs `*.json` metadata + unions `conversations_v2` sqlite keys; filters sub-agents (`parent_session_id`); 1MB skip guard.
- **how-to-reach**: via 1.1 with provider=kiro-cli.
- **probes**: sub-agent session excluded; sqlite-only workspace shows count 0; DB locked by kiro-cli (5s `busy_timeout` block — time it, UI stall risk); DB missing; metadata >1MB skipped; corrupt JSON dropped; string `updated_at` compared non-lexicographically (mis-order).
- **oracle**: read-only sqlite (`mode=ro`), never raises; sub-agents never counted.
- **risks**: 5s DB stall on discovery; raw-string timestamp ordering; OneDrive read_text drops files silently.

### 1.10 Kiro prompt/content extraction
- **what**: `.history` first line preferred for first_prompt, else first-50-line `Prompt` scan; tail via `deque(maxlen=100)`; multi-format `_extract_content` (str / list-of-blocks / nested data).
- **how-to-reach**: indirectly via `load_sessions` and tail/first-prompt endpoints.
- **probes**: first prompt beyond line 50 (missed); last assistant message >100 lines back (empty tail); content as string vs list-with-`kind:text` vs `type:text` vs nested `data.text`; toolUse entries skipped; corrupted line returns "".
- **oracle**: first_prompt[:200]; last_reply_tail[-100:]; parse failures → "".
- **risks**: 50-line/100-line caps; brittle to schema changes.

### 1.11 Claude discovery + path index (H7)
- **what**: `data_claude.discover_workspaces` counts UUID `.jsonl` files per project folder; `_build_path_index` (60s TTL) maps mangled folder names → real paths from `history.jsonl`.
- **how-to-reach**: via 1.1 with provider=claude-code.
- **probes**: distinct real paths that mangle to the same folder name (H7 collision → wrong reverse resolution); folder absent from history (displays mangled `-` path); `iterdir` OSError hides ALL claude workspaces; uppercase/non-canonical UUID filename ignored; mtime-based ordering (a `touch`/OneDrive re-sync reorders without real activity).
- **oracle**: `(real_path, count, latest_mtime_iso)`; folder→path via first-wins index, fallback to mangled name.
- **risks**: H7 many-to-one collision; whole-discovery abort on one bad dir; mangled path shown in UI.

### 1.12 Claude session parse
- **what**: `_parse_session_file` reads the file in binary — first 500 lines for `ai-title`/`custom-title` + first user message (skipping metadata/`hook_*` types), 256KB tail for last user/assistant; created_at from first-message epoch-ms → ctime → updated_at. Once `first_prompt` is known, lines not containing the bytes `b"title"` are skipped without parsing (only title lines can still change the result). Two caches sit in front: `_parse_cache` keys the whole result by `(mtime, size)`; `_head_cache` keys the immutable head (`first_prompt`, `first_timestamp`) and is reused when the file grew, or is unchanged in both size and mtime.
- **how-to-reach**: via `load_sessions` (provider=claude-code) and tail/first-prompt endpoints.
- **probes**: session with only metadata lines (title falls back to UUID stem); long session missing both ends (500-line/256KB caps); non-ms timestamp (wrong created_at); last message all tool_use blocks (empty tail); content str vs list-of-text-blocks; the literal word "title" inside message content after `first_prompt` is set (prefilter false positive — costs a redundant parse, must stay correct); rename appending `custom-title` past line 500 (never seen); invalid UTF-8 line (re-decoded with `errors="replace"`, not dropped); repeated load of an unchanged file (must not re-read); append to a live session (head reused, tail re-parsed).
- **oracle**: title from custom-title else ai-title else first_prompt[:80] else stem; text-only extraction; byte-identical output whether served from cache or a cold parse.
- **risks**: multi-step title fallback surfaces raw UUID; epoch-ms assumption; tool-only tail renders empty; an in-place rewrite that preserves both size and mtime reuses a stale cached head (not reachable for append-only JSONL — accepted).

---

## 2. Web API + UI (`web.py` + `templates/`)

> **2.1–2.25 is the dashboard surface, and it is no longer the whole web surface.** The `/acp` page
> and the `/ws/acp` WebSocket added on 2026-07-26 have no brief here, deliberately: the module behind
> them is a throwaway prototype, and exercising it spawns a real `kiro-cli acp --trust-all-tools` and
> writes a permanent session into the user's ~13,300-entry store — a side-effecting surface this
> plan's probe style assumes away. Their security controls (per-process handshake token, raw-`Host`
> allowlist, `Sec-Fetch-Site` guard on `GET /acp`, per-response CSP nonce) do have unit coverage in
> `tests/test_web.py`. A run of this plan should report the web surface as covered **except** those
> two routes rather than as covered outright.

### 2.1 Three-panel dashboard bootstrap
- **what**: `GET /` renders topbar + 3 panels; htmx `hx-trigger=load` fires 4 partials (launchers, pinned-sessions, pinned-workspaces, workspaces).
- **how-to-reach**: navigate to `/`.
- **probes**: normal load; empty data (empty-state per panel); a partial erroring (toast, skeletons); verify `aria-busy` removed after workspace swap; slow backend keeps skeletons; 4 parallel partial fetches racing.
- **oracle**: skeletons → cards; `startPinnedPoll` begins after workspace swap.
- **risks**: parallel-fetch races; poll starts before cards settle; failed htmx leaves skeletons forever.

### 2.2 Workspace card expand + lazy-load
- **what**: clicking a card header toggles collapse and lazy-loads sessions on first expand.
- **how-to-reach**: click `.card-header`; `GET /partials/sessions?cwd&provider`.
- **probes**: expand→load; collapse+re-expand (no re-fetch, `data-loaded=true`); rapid double-click (double-fetch — `data-loaded` set before fetch completes); expand a 0-session workspace (`+ New session`); expand while endpoint errors (`Failed to load`).
- **oracle**: first expand fetches; subsequent toggles don't; provider read from `card.dataset.provider`.
- **risks**: double-fetch; selection lost on htmx swap.

### 2.3 Session-tail hover tooltip
- **what**: hovering a session row 300ms fetches and positions a tail tooltip.
- **how-to-reach**: hover `.session-content`; `GET /partials/session-tail`.
- **probes**: hover shows tooltip; empty → "No recent output"; row swapped mid-hover (timer not cleared); scroll after show (stale position); kiro vs claude freshness (H2); provider/cwd passed from dataset.
- **oracle**: 300ms debounce; positioned above or below the row depending on available viewport space. Specifically: when space above ≥ space below AND the tooltip fits above, opens above (`transform: translateY(-100%)`); when space below > space above (or above doesn't fit), opens below (`top: rect.bottom + 4px`, `transform: none`); when BOTH sides < 100px, suppressed (`display: none`). `hideTail` resets `left`, `top`, `transform`, and `maxHeight` on close.
- **risks**: leaked timers; stale position; per-provider cost divergence (H2).

### 2.4 Provider tab switching
- **what**: All / Kiro CLI / Claude Code tabs filter the right panel; only shown when >1 provider.
- **how-to-reach**: click `.provider-tab`; `GET /partials/workspaces?provider=X`.
- **probes**: switch each tab; `window._activeProvider` updates via `htmx:configRequest`; single-provider (no tabs); disabled provider hidden; empty per-provider states (distinct messages).
- **oracle**: cards filtered per provider; active tab reflects selection.
- **risks**: `_activeProvider` desync; refresh uses active provider.

### 2.5 Row/card selection + action bar
- **what**: clicking session rows / workspace cards toggles `.selected`; action bar shows count.
- **how-to-reach**: click a row or card body; `updateActionBar`.
- **probes**: select one (bar "1 sessions selected"); multi-select; Clear; selection lost after search/refresh/swap (DOM-only); launcher badges update with selected-workspace count.
- **oracle**: `.selected` class; bar visible when n>0.
- **risks**: DOM-only selection wiped by any htmx swap; badge desync.

### 2.6 Launch selected (batch)
- **what**: launches all selected sessions/cards; confirm dialog >5.
- **how-to-reach**: click "Launch selected"; `POST /api/launch-batch`.
- **probes**: launch 1; launch 5 (no confirm); 6+ (confirm); mixed valid/invalid workspaces (partial-success warning); mixed providers (per-provider default_args lookup); verify which sessions failed is lost in aggregate toast.
- **oracle**: `>5` confirm; toast level success/warning/error by outcome.
- **risks**: selection cleared by swap before launch; no post-launch reset; per-session failures not surfaced.

### 2.7 Pin / unpin session
- **what**: toggles a session in `pinned_sessions`; pinned sessions render as a flat list in the left panel.
- **how-to-reach**: hover row → click pin; `POST /api/pin-session` / `/api/unpin-session`; then `refreshCards(true)`.
- **probes**: pin → appears in "Pinned sessions"; unpin → removed; pin from a stale workspace; pin multiple across workspaces; full refresh loses expanded state; optimistic toast before refresh.
- **oracle**: `pinned_sessions` list mutated + saved; cache-first render, metadata fallback for kiro.
- **risks**: full refresh cost; pinned Claude session in un-warmed workspace shows blank prompts (kiro-only fallback).

### 2.8 Pin / unpin workspace folder
- **what**: toggles a `{folder, provider}` entry in `pinned_folders`; pinned workspaces render in the center panel.
- **how-to-reach**: hover card → click pin; `POST /api/pin-folder` / `/api/unpin-folder`.
- **probes**: pin → center panel; unpin → back to right; pin a stale folder (count 0, "missing" badge); dedup per `(folder, provider)`; pin same folder under both providers; **then open Settings and Save — verify the pinned Claude folder does NOT revert to kiro-cli (H1)**.
- **oracle**: `{folder, provider}` appended if not duplicate; stale merged with count 0.
- **risks**: H1 provider loss via settings save; provider defaults kiro-cli when missing.

### 2.9 Workspace icon emoji picker
- **what**: sets a per-workspace emoji/custom icon stored in `workspace_icons` keyed by normalized path.
- **how-to-reach**: click card icon → emoji picker → pick/custom/Enter; `POST /api/set-workspace-icon`.
- **probes**: set emoji → persists on reload; Reset → default 📁; custom string; optimistic `textContent` before save; normalize-path key mismatch (icon not applied to a differently-cased/slashed card); outside-click close race.
- **oracle**: non-empty icon stored at normalized key; empty clears.
- **risks**: optimistic UI desync; normalize-path keying gaps; unvalidated icon value.

### 2.10 Launcher create / edit / delete modal
- **what**: CRUD for custom launchers via a modal; icon extracted on save, removed on delete.
- **how-to-reach**: click `+` (new) or gear (edit); `POST /api/launcher/create|update|delete`.
- **probes**: create minimal; edit fields; delete; env textarea lines without `=` dropped; color swatch select/deselect; icon extraction async after save (tile may show fallback briefly); `_launchers` JS cache staleness after save; malformed entry missing `id` (KeyError 500 on delete).
- **oracle**: entry persisted with uuid; tile re-rendered from `/partials/launchers`.
- **risks**: env parse silently drops lines; icon side-effects outside config lock (no rollback).

### 2.11 Provider-settings modal (gear)
- **what**: edit a provider's `default_args`, `color`, `enabled` via a locked-field modal reusing the launcher modal.
- **how-to-reach**: click provider-tile gear; `GET /api/provider/{key}` → `POST /api/provider/save`.
- **probes**: change default_args (e.g. `-a`) → affects subsequent launches; change color → tile + cards recolor; disable provider → its tab + tile + workspaces hidden; verify readonly fields restored on modal close; **saving any provider_settings permanently suppresses the trust_all_tools migration** (H1 sibling).
- **oracle**: `provider_settings[key]` replaced wholesale + saved.
- **risks**: disabling hides a provider's data everywhere; no schema validation of enabled/color.

### 2.12 Launcher run (single + selection-aware batch)
- **what**: clicking a launcher tile runs it once (no selection) or once per selected workspace.
- **how-to-reach**: click tile; `POST /api/launcher/run` or `/api/launcher/run-batch`; provider tiles → `/api/launch-batch`.
- **probes**: no selection → single run at launcher cwd; selection → batch per workspace; provider tile with no selection → "Select workspaces first" error; `pass_workspace_arg` only when non-terminal + use_selected; duplicate workspaces not deduped server-side.
- **oracle**: batch iterates workspaces; aggregate toast.
- **risks**: server no-dedup (relies on client); which workspace failed is lost.

### 2.13 Launcher icon serving
- **what**: `GET /api/launcher-icon/{id}` serves cached PNG or SVG fallback; `provider--<key>` triggers on-demand extraction.
- **how-to-reach**: `<img src>` in each tile; `img onerror` fallback.
- **probes**: cached PNG served; missing → SVG (terminal vs app glyph, color-tinted); `provider--kiro-cli` first request extracts from binary then serves; unknown id → generic terminal SVG (not 404); blocking extraction on first request (latency via `to_thread`); repeated failures re-attempt every miss (Linux/non-extractable).
- **oracle**: FileResponse PNG when present; else SVG by `terminal`/`color`.
- **risks**: first-request extraction latency; provider `-- ` icons never cleaned up on disable.

### 2.14 Search
- **what**: debounced search filters workspaces by cwd substring + matches pinned sessions by title.
- **how-to-reach**: type in search; `hx-trigger=input changed delay:300ms`; `GET /search?q`.
- **probes**: query matching a folder name; no match (empty_state); clear (full list); rapid typing (debounce); pinned-session title match; verify it does NOT search session content despite `_session_matches` existing; pinned scan globs `SESSION_DIR` each keystroke (cost); search with status filter active (q + status param).
- **oracle**: substring on cwd, case-insensitive; empty q restores all.
- **risks**: cwd-only match; per-keystroke metadata glob; selection wiped by swap.

### 2.15 Manual refresh + last-refresh
- **what**: refresh button clears caches, warms up, re-renders; last-refresh time shown.
- **how-to-reach**: click `↻`; `POST /api/refresh`; `GET /api/last-refresh`.
- **probes**: button → `...` disabled → re-enabled; full cache clear + warmup cost; `refreshTime` shows HH:MM:SS; visibilitychange re-refresh on tab focus.
- **oracle**: `session_cache.clear()` + `_cache.clear()` + warmup; returns `last_refresh`.
- **risks**: full clear expensive on many workspaces.

### 2.16 Lifespan: background refresh loop + ACP teardown
- **what**: `lifespan` owns two concerns. Before the `yield`, `_background_refresh` calls `refresh_stale_entries` every 30s in an asyncio task. After the `yield`, since 2026-07-26, it tears down the ACP agent process tree.
- **how-to-reach**: lifespan task; observe over a sustained window. Teardown is reached only on the tray-quit route.
- **probes**: edit a session file, wait ≤30s, confirm UI reflects it after a card interaction; exception in refresh (logged, loop continues); no UI signal of background update; **teardown**: with an ACP session open, quit from the tray and confirm `Get-Process kiro-cli` goes to zero.
- **oracle**: 30s cadence; exceptions swallowed/logged; no `kiro-cli` process survives a tray quit.
- **risks**: silent updates; H3 race with request-path cache access; **teardown is a fast path, not the guarantee** — `--stop`/`--restart` and any hard kill never run `lifespan` at all and are covered instead by a Windows Job Object, so a green result here says nothing about those routes and must not be reported as covering them.

### 2.17 Unified background scheduler (client)
- **what**: one 5s tick drives all three client-side background jobs by counter — status every tick, active-session content every 2nd tick, workspace refresh every 3rd tick while `_tick <= _BURST_TICKS` and every 6th tick after. Net cadence is 15s burst for the first 120s then 30s steady; there is no separate pinned-panel timer.
- **how-to-reach**: `startPolling` / `startStatusPoll` / `startActiveSessionPoll` → `_schedulerTick`; observe over ~3 min.
- **probes**: workspace refresh fires at 15s for the first 2 min then drops to 30s; a slow refresh does not stack (`aria-busy` on `#workspace-cards` makes the tick skip); `pollActiveSessions` skipped while a full refresh is in flight; `visibilitychange` to hidden stops all three and clears the timer, returning restarts them; re-render wipes expanded card state.
- **oracle**: `_TICK_MS=5000`, `_BURST_TICKS=24`; refresh every 3rd tick during burst, every 6th after; timer cleared only when all three jobs are off.
- **risks**: `_ensureScheduler` resets `_tick` to 0, so every tab return re-enters the 2-min burst window; expanded state lost on each refresh; all three jobs share one timer, so a blocking handler delays the others.

### 2.18 Toast notifications (H6)
- **what**: `showToast(html)` appends a toast, auto-removed after 4s.
- **how-to-reach**: any action returning a `toast.html` partial.
- **probes**: success auto-dismiss 4s; error styling; × dismiss; rapid actions stack (no cap); **inject markup via an error path (e.g. a launcher error containing HTML) and confirm unsanitized `innerHTML` renders it (H6)**.
- **oracle**: container created lazily; 4000ms auto-remove.
- **risks**: H6 XSS via innerHTML; unbounded stacking.

### 2.19 Terminal preference quick-set
- **what**: topbar `<select>` saves `terminal_command` immediately on change.
- **how-to-reach**: change dropdown; `POST /api/save-setting key=terminal_command`.
- **probes**: select each detected option; verify persisted; no "custom" option in topbar (only settings page); diverges from `/api/settings` form path.
- **oracle**: immediate save; value persisted.
- **risks**: two save paths (`/api/save-setting` vs `/api/settings`) can diverge.

### 2.20 Autostart toggle (topbar + settings)
- **what**: toggles Windows autostart; optimistic class flip.
- **how-to-reach**: click topbar toggle or settings checkbox; `POST /api/autostart`.
- **probes**: on → `.lnk` created; off → removed; visual state matches actual `is_enabled()`; optimistic class toggled before server confirm (desync if COM fails); platform label ("Start with Windows").
- **oracle**: `POST /api/autostart` returns `{enabled: bool}`.
- **risks**: optimistic UI ignores response; COM failure unhandled.

### 2.21 Peek hotkey save
- **what**: topbar input saves `peek_hotkey` (lowercased) on change.
- **how-to-reach**: edit peek input; `POST /api/save-setting key=peek_hotkey`.
- **probes**: valid combo persists; invalid syntax accepted by API (validation only at peek startup → fallback); requires restart to rebind the live listener.
- **oracle**: lowercased value persisted.
- **risks**: no syntax validation at save; rebind needs restart.

### 2.22 Settings page form save
- **what**: `/settings` full form saves terminal + pinned folders (pipe-joined hidden field).
- **how-to-reach**: `GET /settings` → submit `POST /api/settings`.
- **probes**: values pre-populated; custom terminal row toggles on select; submit persists; **pinned_folders written as `list[str]` → H1 provider coercion**; full-page reload vs partial divergence.
- **oracle**: form fields saved; page re-rendered.
- **risks**: H1; read-modify-write clobbers concurrent edits.

### 2.23 save-setting allowlist endpoint
- **what**: `POST /api/save-setting` writes only allowlisted keys (`terminal_command`, `peek_hotkey`, `pinned_folders`, `pinned_sessions`) with type checks.
- **how-to-reach**: `POST /api/save-setting {key, value}`.
- **probes**: valid key/type saved; unknown key rejected; wrong type rejected; list with non-str element rejected; `pinned_folders` as list[str] accepted → re-introduces legacy shape (H1).
- **oracle**: `{ok:true}` on success; `{ok:false,error}` otherwise.
- **risks**: shallow validation; H1 legacy shape re-introduction.

### 2.24 Session actions (resume / new / copy / open in ACP)
- **what**: per-row resume, per-card new session, copy session id, and — added 2026-07-26, kiro-cli rows only — **open in `/acp`**, which navigates to the ACP page for that session id.
- **how-to-reach**: hover reveal → click; `POST /api/launch` / `/api/new-session`; clipboard for copy; `GET /acp?sid=` for the ACP action.
- **probes**: resume valid session (toast); resume hidden on stale workspace; new session (no resume flag); copy → clipboard toast; provider from card dataset may be empty string; ACP action present on kiro-cli rows and absent on claude-code rows; ACP action does **not** toggle multi-select (it sits inside `.session-actions`, the container the row's own `onclick` excludes) — the same collision that killed the terminal-focus feature.
- **oracle**: launch toast success/error; resume hidden when stale; ACP action navigates with the row's `sid`.
- **risks**: empty provider dataset; clipboard permission; no loading state on button; **the ACP action is a state-changing GET** — rendering `/acp?sid=` auto-loads the session, which spawns `kiro-cli acp -a` and can write to the user's real session store, so probing it is not a read-only act.

### 2.25 App restart endpoint
- **what**: `POST /api/restart` sets the tray restart flag and stops icon/peek.
- **how-to-reach**: `POST /api/restart`.
- **probes**: triggers restart flow; mutates `tray` module globals directly; no auth on endpoint.
- **oracle**: `{ok:true}`; relies on `__main__` honoring `restart_requested()`.
- **risks**: direct global mutation; unauthenticated restart.

---

## 3. Launcher (`launcher.py`)

### 3.1 Terminal auto-detection + config override
- **what**: `detect_terminal` returns config override verbatim, else probes wt>pwsh>cmd (Windows).
- **how-to-reach**: `detect_terminal("")` / `detect_terminal("path")`.
- **probes**: wt present (first); pwsh/cmd fallback; none on PATH → None; override returned unvalidated (nonexistent path passes detection, fails at Popen).
- **oracle**: override wins; else first-on-PATH by priority; None if none.
- **risks**: override no existence check; which≠launchable.

### 3.2 Available terminals list (settings source)
- **what**: `available_terminals` returns `(value,label)` incl Auto-detect + Custom, cached for process lifetime.
- **how-to-reach**: settings page render.
- **probes**: found terminals listed in Auto-detect label; none found label; cache never invalidates (install/remove after start not reflected).
- **oracle**: `[("", auto), ...found, ("custom","Custom")]`.
- **risks**: stale cache until restart.

### 3.3 Resume / new / batch session launch (provider-aware)
- **what**: `launch_session` / `launch_batch` build provider commands, apply per-provider `default_args`, never raise.
- **how-to-reach**: `POST /api/launch`, `/api/new-session`, `/api/launch-batch`.
- **probes**: resume adds `--resume-id`(kiro)/`--resume`(claude); new omits it; batch never aborts on one failure; missing-workspace → per-item error; per-provider default_args in a mixed batch; unknown provider used as literal binary.
- **oracle**: `LaunchResult(success, session_id, workspace, error)`; sequential batch.
- **risks**: fire-and-forget (no PID); aggregate-only failure reporting.

### 3.4 default_args parsing (H5)
- **what**: non-empty `default_args` appended via `shlex.split`.
- **how-to-reach**: provider settings default_args (e.g. `-a`, or a path arg).
- **probes**: `-a` splits fine; **a backslash Windows path in default_args mangled by POSIX shlex (H5)**; **malformed quoting (`"` unbalanced) raises ValueError NOT caught by the OSError handler (H5) — confirm crash vs graceful error**.
- **oracle**: POSIX split appended to cli args.
- **risks**: H5 backslash mangling + uncaught ValueError.

### 3.5 Windows command builders (wt / pwsh / cmd)
- **what**: `_build_command` for wt (`--title -p PowerShell -d cwd -- args`), pwsh (WindowTitle + Set-Location + `&`, single-quote doubled), cmd (`/k title&& cd /d "cwd" && cmd`, metachar-guarded).
- **how-to-reach**: inspect built list per terminal stem.
- **probes**: paths with spaces; single quotes in cwd (pwsh `''` escaping); **cmd metachar in cwd → None ("unsafe")**; **H4: joined args interpolated into cmd `/k` string (only cwd guarded)**; args with spaces joined unsafely in pwsh.
- **oracle**: discrete argv for wt; script string for pwsh; guarded shell string for cmd.
- **risks**: H4 cmd injection via args; pwsh arg-join splitting.

### 3.6 Template command builder
- **what**: `{cwd}`/`{cmd}` placeholders split around, values inserted as discrete elements.
- **how-to-reach**: custom terminal string containing `{cwd}`/`{cmd}`.
- **probes**: both placeholders; only one; **literal path-with-spaces in the template torn by `.split()`**; missing `{cmd}` (CLI never invoked); no safety guard on output.
- **oracle**: placeholders replaced, literal text whitespace-split.
- **risks**: space-splitting breaks literal paths; fully user-trusted.

### 3.7 Launch guards (binary / terminal / cwd / session-id / title)
- **what**: pre-launch checks: binary on PATH, terminal found, cwd exists, session-id regex `^[\w\-]+$`, title sanitized.
- **how-to-reach**: `launch_session` chain.
- **probes**: missing binary → "not found on PATH"; no terminal → platform message; cwd missing → "Folder not found"; invalid session-id → "Invalid session ID format"; empty session-id skips validation; title with `; $ \`` survives sanitizer (only `"'&|` stripped); TOCTOU cwd deleted after check.
- **oracle**: each guard → descriptive `LaunchResult.error`, never raises.
- **risks**: permissive regex (no length bound); incomplete title sanitizer; TOCTOU.

### 3.8 Custom launcher run (terminal vs detached)
- **what**: `launch_custom` runs a command in a terminal or detached (`shell=True`, `DETACHED_PROCESS|CREATE_NO_WINDOW`); env merge; `pass_workspace_arg` quoting.
- **how-to-reach**: `POST /api/launcher/run`, `/api/launcher/run-batch`.
- **probes**: terminal mode (new console); detached mode (`shell=True` — full injection by design); `%VAR%` in cwd env-expands under shell=True; `pass_workspace_arg` quotes only paths-with-spaces on Windows; cwd missing → error; unsafe cwd for terminal → None.
- **oracle**: LaunchResult; detached success = Popen didn't raise.
- **risks**: shell=True injection surface (by design, user-authored); fire-and-forget.

---

## 4. Icons (`icons.py`)

### 4.1 Extraction on create / update, removal on delete
- **what**: `extract_icon` at launcher create/update; `remove_icon` on delete.
- **how-to-reach**: `POST /api/launcher/create|update|delete`.
- **probes**: create with a real `.exe` command → PNG appears in `icons/`; update to a different binary → re-extracted; update to non-extractable → stale PNG unlinked, SVG served; delete → PNG removed; extraction failure at create is silent (return ignored).
- **oracle**: PNG at `CONFIG_DIR/icons/<id>.png` on success; unlink on fail.
- **risks**: failure not surfaced; provider icons never cleaned.

### 4.2 Windows PE icon extraction (H9)
- **what**: `_extract_windows_icon` via `PrivateExtractIconsW` at 48×48 → GetIconInfo → CreateBitmapFromHandle → PIL BGRA → PNG.
- **how-to-reach**: `extract_icon` with an `.exe`/`.msi` on Windows.
- **probes**: extract from a known exe (e.g. a terminal binary) → valid 48×48 PNG; Electron app with 256px icon; count==0 / null handle → False; **H9: GDI handle leak — hbm_mask/hbm_color freed only on success path**; broad `except` hides pywin32/PIL errors.
- **oracle**: True + PNG on success; False on any failure.
- **risks**: H9 handle leak; undocumented API; monochrome-icon unhandled.

### 4.3 `.cmd`/`.bat` shim + binary resolution
- **what**: `_resolve_cmd_to_exe` (3 regex patterns, 64KB guard); `_resolve_binary` (whole-command / first-token / `shutil.which`).
- **how-to-reach**: `extract_icon` with a `.cmd`/`.bat` command (e.g. npm-installed Electron shim).
- **probes**: `%~dp0`-relative shim; quoted-abs; unquoted-abs; shim >64KB skipped; non-standard shim (env vars, forward slashes) missed → SVG; command with quoted spaced path + args mis-split on first token.
- **oracle**: resolves to real `.exe` if a pattern matches and file exists; else None → SVG.
- **risks**: pattern gaps; 64KB guard; first-token split on spaces.

### 4.4 SVG fallback + color tint
- **what**: `default_icon_svg(is_terminal, color)` returns terminal/app glyph, recoloring `stroke="currentColor"`.
- **how-to-reach**: `GET /api/launcher-icon` when no PNG.
- **probes**: terminal vs app glyph; color tint applied to stroke only (fill unaffected); **unescaped color value injected into SVG markup (potential injection)**; provider fallback uses provider color.
- **oracle**: correct glyph; stroke recolored when color set.
- **risks**: stroke-only recolor; unvalidated/unescaped color string.

---

## 5. Config (`config.py`)

### 5.1 Load: missing / corrupt / valid
- **what**: `load_config` → defaults on missing/corrupt, per-field type check, then migrations.
- **how-to-reach**: called at start of nearly every route + startup + tray.
- **probes**: missing file → defaults; corrupt TOML → defaults (no error surfaced, no backup); valid subset (missing keys default); wrong scalar type reverts to default; **wrong nested types accepted** (`pinned_folders=[123]`, `provider_settings={'x':'str'}` pass shallow check → downstream crash).
- **oracle**: unknown keys dropped; only top-level type validated.
- **risks**: corrupt=first-run indistinguishable; shallow validation; per-process lock only.

### 5.2 Atomic save
- **what**: `save_config` `.tmp`→fsync→`os.replace`; pops legacy `trust_all_tools`; cleans `.tmp` on failure.
- **how-to-reach**: every mutating endpoint.
- **probes**: normal save (no `.tmp` left); write failure preserves original + removes `.tmp`; valid TOML after save; **cross-process race: two load-modify-save cycles (web + tray) last-writer-wins drops a pin/setting**; OneDrive `os.replace` contention.
- **oracle**: atomic replace; legacy key never written.
- **risks**: no backup; cross-process last-writer-wins; OneDrive replace failures.

### 5.3 pinned_folders migration list[str]→list[dict] (H1)
- **what**: on load, a legacy `list[str]` becomes `[{folder, provider:"kiro-cli"}]`.
- **how-to-reach**: load a config with `pinned_folders = ["/a","/b"]`; **or trigger it live via a settings save**.
- **probes**: **migration IS reachable** (shallow type filter validates only the outer list); provider hardcoded kiro-cli; guard inspects only element [0] (mixed/empty-first-element edge cases); **H1: `/api/settings` + `/api/save-setting` write list[str] every save → a pinned Claude folder reverts to kiro-cli on next load**.
- **oracle**: legacy list → dict list, provider=kiro-cli.
- **risks**: H1 active provider loss; element-[0]-only guard.

### 5.4 trust_all_tools migration
- **what**: legacy `trust_all_tools=true` (with no provider_settings) → `provider_settings["kiro-cli"].default_args="-a"`.
- **how-to-reach**: load a config with `trust_all_tools = true` and no `provider_settings`.
- **probes**: reachable (read from raw dict, not filtered kwargs); suppressed if ANY provider_settings exists (even claude-only) → trust intent lost; once loaded+saved, legacy key popped so migration never re-runs; verify the client migration toast fires once (localStorage-gated).
- **oracle**: adds kiro-cli entry only when provider_settings empty.
- **risks**: suppressed by unrelated provider settings; one-shot.

### 5.5 Type validation, unknown-key round-trip, path capture
- **what**: shallow isinstance check; unknown keys dropped on load + lost on save; CONFIG_PATH captured at import.
- **how-to-reach**: hand-add `my_key="x"` to config.toml; load then save; inspect env-path behavior.
- **probes**: unknown key dropped silently (destructive round-trip, no versioning); newer-version key erased by current save; import-time path capture ignores later `LOCALAPPDATA` change; empty env var falls back.
- **oracle**: only dataclass fields persisted; path frozen at import.
- **risks**: manual/newer-version edits lost; env changes invisible post-import.

---

## 6. Autostart (`autostart.py`) — Windows

### 6.1 Enable / disable / query
- **what**: Windows `enable()` creates `PowerAtlas.lnk` (WScript.Shell COM, target pythonw.exe, args `-m power_atlas`, icon); `disable()` unlinks; `is_enabled()` existence check.
- **how-to-reach**: library `autostart.enable()/disable()/is_enabled()`; `POST /api/autostart`. **Snapshot state, restore after.**
- **probes**: enable → `.lnk` at Startup with TargetPath = `.venv-PowerAtlas\Scripts\pythonw.exe`, resolved from the checkout by `interpreter.venv_python()` and **not** from the enabling process's `sys.executable` — probe it from a non-venv interpreter, which is the case that used to record the wrong target; **verify that pythonw.exe actually exists — may be absent from a venv built with `--without-pip` or a stripped copy**; verify IconLocation `poweratlas.ico` exists; disable removes it; disable when absent (no-op); enable twice (overwrite); is_enabled True/False; **stale shortcut pointing elsewhere still reports enabled** (existence-only).
- **oracle**: `.lnk` presence = enabled; COM creates shortcut; target is a function of the checkout, not of the caller.
- **risks**: pythonw.exe missing in venv breaks autostart silently; COM Dispatch failure unhandled; existence-only check ignores wrong target; APPDATA fallback path drift; a checkout with two off-convention `.venv*` directories resolves to None and silently falls back to `sys.executable`.

---

## 7. Main / Lifecycle (`__main__.py`) — Windows-automatable subset

### 7.1 Bare detached launch
- **what**: no-flag run acquires mutex, re-execs `-m power_atlas --foreground` detached, parent prints + exits.
- **how-to-reach**: run `power-atlas` (isolate; not against a live instance).
- **probes**: parent exits after two prints; child running with CREATE_NO_WINDOW (no console); child crash after detach → orphan with no feedback.
- **oracle**: Popen detached; optimistic prints only.
- **risks**: no confirmation the child actually started; orphan on early child crash.

### 7.2 Foreground launch
- **what**: `-f` runs server+tray attached; logging to `orchestrator.log`; blocks until quit.
- **how-to-reach**: `power-atlas -f`.
- **probes**: server binds a dynamic non-zero port; `/` reachable at it; log file created + INFO written; foreground terminal shows almost nothing (no console handler); 10s ready timeout on slow start.
- **oracle**: uvicorn port 0 → OS-assigned; ready within 10s.
- **risks**: 10s timeout aborts a slow-but-valid start; file-only logging.

### 7.3 --stop / --restart
- **what**: `--stop` reads PID and terminates; `--restart` stops, sleeps 0.5s, guards, relaunches.
- **how-to-reach**: `power-atlas --stop`, `power-atlas --restart`.
- **probes**: stop running → TerminateProcess (hard kill, skips cleanup, may orphan tray/webview) → PID removed; stop not-running → message; **H8: --restart 0.5s race — old instance still holds mutex → new guard silent exit(0), no restart**; stop failure ignored, relaunch proceeds.
- **oracle**: stop returns bool; restart = stop then relaunch.
- **risks**: H8 restart race; hard-kill skips graceful shutdown; PID reuse targets wrong process.

### 7.4 Single-instance guard
- **what**: Windows named mutex `PowerAtlasMutex`; second instance `os._exit(0)` silently.
- **how-to-reach**: launch twice.
- **probes**: first acquires; second silent exit(0) (no feedback — looks like a no-op); mutex released on process exit; hung process holds mutex indefinitely.
- **oracle**: ERROR_ALREADY_EXISTS(183) → exit(0).
- **risks**: silent refusal; guard acquired before display/pid so a crashing guard-holder leaves orphaned lock state.

### 7.5 PID file lifecycle
- **what**: write PID at foreground start; read (file→alive check) for stop/restart; remove on shutdown.
- **how-to-reach**: inspect `CONFIG_DIR/power-atlas.pid`.
- **probes**: PID written; alive-check via OpenProcess; removed on clean shutdown; stale PID after `os._exit` path that skips removal; PID reuse false-positive; remove errors swallowed.
- **oracle**: PID file reflects running instance when alive.
- **risks**: staleness/reuse; silent removal failures.

### 7.6 Server readiness + shutdown
- **what**: monkeypatched `server.startup` sets a ready event (10s wait); shutdown sets `should_exit`, joins 5s, optionally relaunches, `os._exit(0)`.
- **how-to-reach**: foreground start/quit sequence.
- **probes**: ready within 10s → port extracted; timeout/no-servers → "Server failed to start" exit(1); 5s join cutoff drops in-flight requests; `os._exit` skips atexit/finally; monkeypatch depends on uvicorn `sockets` kwarg (upgrade fragility).
- **oracle**: port from `server.servers[0].sockets[0]`; graceful within timeouts.
- **risks**: 10s/5s hard cutoffs; monkeypatch fragility; os._exit skips cleanup.

### 7.7 Venv re-exec guard (`interpreter.py`)
- **what**: `main()` calls `ensure_project_interpreter()` before argparse, the mutex and any config read. Off the checkout's venv it re-launches `<venv-python> -m power_atlas <argv[1:]>` — `subprocess.run` + `SystemExit(returncode)` on Windows, `os.execv` elsewhere — with `POWER_ATLAS_VENV_REEXEC=1` set on the child.
- **how-to-reach**: invoke `python -m power_atlas` from any interpreter that is not the checkout venv. Requires a second interpreter that can import the package (the repo's own venv is the only one that can post-2026-07-28, so use a scratch venv with `pip install -e .`).
- **probes**: from a foreign interpreter → surviving process cmdline names the venv python and `psutil.Process(pid).memory_maps()` shows exactly one `site-packages` root, the venv's; from the venv itself → no extra process; with the sentinel pre-set → no re-exec even when detection says otherwise (fork-bomb guard); `--foreground` output and exit code survive the Windows subprocess hop; args are forwarded verbatim; no checkout (wheel install) → no guard, runs in place; two off-convention `.venv*` dirs → no guard.
- **oracle**: `sys.prefix` equals the venv dir, not `sys.executable` — on Windows the venv `python.exe` is a redirector whose image path is the base install, so an executable comparison reports a false negative.
- **risks**: a venv that exists but lacks the package makes every entry point fail, and silently under `pythonw` (autostart); the sentinel is inherited by launched child processes; `--stop` pays a process hop it does not need.

### 7.8 Crash handler (`faulthandler` → `crash.log`)
- **what**: `_enable_crash_handler()` runs immediately after `logging.basicConfig` in `_run_foreground`. It appends a `=== pid N started <ts> ===` header to `%LOCALAPPDATA%\power-atlas\crash.log` and installs `faulthandler` with `all_threads=True` writing to that file's descriptor. Any failure is logged and non-fatal.
- **how-to-reach**: start the app; force a fault from any thread with `ctypes.string_at(0)`. Redirect `__main__.CONFIG_DIR` to a tmp path first — the probe must not append to the real log.
- **probes**: header appears once per start; a fault appends `Windows fatal exception: access violation` plus a Python traceback **per thread**, with the faulting one first; verified under `pythonw`, where `sys.stderr` is None and the default `faulthandler.enable()` would raise — that is the login configuration, so a console-only check proves nothing; append mode preserves an earlier crash; a `CONFIG_DIR` that cannot be created degrades to a warning and startup continues.
- **oracle**: the traceback lands in `crash.log`, never `orchestrator.log` — `logging.FileHandler` holds a buffered handle on the latter, and faulthandler's descriptor-level writes would land mid-line.
- **risks**: the module-level `_crash_log` reference is load-bearing — if it is ever dropped the object is collected, the descriptor closes and the handler writes into a closed fd, which no test currently guards; `crash.log` is never rotated; a fault that corrupts the interpreter badly enough to break traceback walking still yields nothing.

---

## Scoped-out (not given full briefs — reason noted)

Per the automatable-only scope decision, these are documented but NOT part of the run-mode probe set:

- **Native tray menu interaction** (`tray.py` Open / Logs / Restart / Quit) — require a human clicking the
  native system-tray icon; not drivable by browser/MCP tooling. *Library-testable bits ARE in scope indirectly*:
  `_create_icon` fallback, `set_peek_stop_callback`, `get_shutdown_event`, `restart_requested` (exercise as units).
- **Peek native behavior** (`peek.py` global hotkey hold-to-show, fullscreen overlay display, Escape dismiss,
  Windows win32 keystroke suppression) — user-assisted; requires a real desktop + human keypresses.
  *Library-testable logic (create_peek validation/fallback, `_parse_hotkey`, `_normalize_key` control-code
  mapping, `_vk_to_name`) can be unit-verified but is deferred here as low-value vs. the existing 32 peek unit tests.*
- **All Linux-specific paths** — Linux terminal command builders (kitty/alacritty/gnome-terminal/konsole/xterm),
  `.desktop` autostart, X11/Wayland display probing (`_ensure_display`), `/proc` PID fallback, flock single-instance.
  This is a Windows machine; these are code-inspection-only. A Linux run of this plan would promote them to full briefs.

## Coverage manifest (for run mode)

In scope (full briefs): Data layer (1.1–1.12), Web API+UI (2.1–2.25), Launcher Windows subset (3.1–3.8),
Icons (4.1–4.4), Config (5.1–5.5), Autostart Windows (6.1), Lifecycle Windows subset (7.1–7.6).
Scoped-out: native tray clicks, peek native behavior, all Linux paths (see above), and the ACP surface
(`/acp`, `/ws/acp`) — see the note under §2. **2.1–2.25 is the dashboard web surface, not the whole
web surface**; a run that covers all of §2 must not report the web layer as fully covered.

### Run status (2026-07-01/02)
- **Web GUI (2.x client-side): COVERED** — driven headless via standalone Playwright (installed into the venv). Verified: bootstrap/skeleton→cards/aria-busy removal, card expand + lazy-load + no re-fetch on re-expand, session-tail hover tooltip (show/hide), provider-tab filtering (All/Kiro/Claude), debounced search + empty-state + restore, row selection + action bar, pin→toast→refresh→unpin, 4s toast auto-dismiss, zero console errors. Finding: selection is DOM-only; after an htmx swap the action bar goes **stale** (selCount stuck, bar stays visible though 0 rows selected) — `updateActionBar()` not called on `htmx:afterSwap` (`index.html`). Launch-selected safely no-ops on the phantom selection.
- **Main/Lifecycle (7.1–7.6): COVERED** (user authorized kill/restart/stop). Verified: `--stop` (kills + removes PID), `--stop` when not running (graceful), bare detached launch (child + dynamic-port server ready ~1s + peek), single-instance guard (`-f` → silent `exit(0)`, no second instance), `--restart` (stop+relaunch), flag precedence (`--stop --restart` → stop-only, `--restart` silently ignored). H8 (0.5s restart mutex race) and the 5s-join/os._exit-skips-cleanup edges remain **plausible** (source-confirmed, not deterministically reproduced — CLI hard-kill releases the mutex within 0.5s; graceful/tray restart path is the realistic trigger). Peek/tray were exercised only as they start during app launch (log-confirmed); native tray *clicks* remain scoped-out.
