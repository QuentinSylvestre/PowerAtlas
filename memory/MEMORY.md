# Project Memory — PowerAtlas

## Pattern

### Session-panel updates must be event-driven, not poll-gated (pin/unpin + startup warmup)

**Why**: Pinned sessions took ~10s to move on pin/unpin (waiting for the next `refreshCards()` polling cycle) and ~20s to appear after restart (waiting for the warmup cache to fill on the 15-30s burst timer). The client relied on periodic polling instead of reacting to state changes and lifecycle events.
**How to apply**: For user-initiated state changes (pin/unpin), move the DOM row immediately in JS (insert before/after `.pinned-separator`, manage the separator lifecycle) rather than waiting for the next `refreshCards()` cycle. For startup latency on cache-dependent UI, emit a `warmup_done` event in `data.py`, expose `/api/warmup-status`, and have the client short-poll (2s) to trigger `refreshCards(true)` the moment warmup finishes.
**Source**: Session 1ecfaedc (2026-07-17) — pinned-session latency fix; verified `warmup_done` / `warmup-status` / `pinned-separator` present in web.py/data.py/index.html | **Verified**: 2026-07-18

### Cache getters must return copies, not references

**Why**: The workspace-count cache returned a raw list reference. A downstream consumer (`partials_workspaces`) appended pinned folders to it, corrupting the cache across requests. The same class of bug was pre-emptively prevented in `SessionCache.get()` by returning `list(sessions)`.
**How to apply**: Any cache `get()` method that returns a mutable collection must return a shallow copy. Callers should not be trusted to avoid mutation — enforce at the cache boundary.
**Source**: `plans/done/260618-1901_SESSION_PRELOAD_CACHE.md` — Post-Implementation Review finding #1 | **Verified**: 2026-06-18


### pywebview main-thread + pynput Ctrl-code quirks on Windows

**Why**: Two non-obvious platform behaviors caused runtime bugs despite passing unit tests: (1) pywebview enforces main-thread execution on Windows too (not just Linux/GTK) — `webview.start()` raises `WebViewException` from any non-main thread, and (2) pynput reports ASCII control codes (0x01–0x1a) instead of letter chars when Ctrl is held on Windows (e.g. Ctrl+Z → `\x1a`, not `'z'`).
**How to apply**: When working with pywebview, always use the main thread regardless of platform. When processing pynput key events with Ctrl held, normalize control codes back to letters via `chr(ord(ch) + ord('a') - 1)`.
**Source**: `plans/done/260630-1607_PEEK_WINDOW.md` — post-implementation empirical testing | **Verified**: 2026-06-30



### Custom htmx-mini requires manual `process()` after every innerHTML swap

**Why**: The `htmx.min.js` in PowerAtlas is a 56-line custom implementation (not the real htmx library). It only attaches event handlers at `DOMContentLoaded`. Any content inserted via innerHTML (htmx swaps, manual `fetch().then(innerHTML=...)`) needs an explicit `process(targetEl)` call to attach handlers to new `hx-get`/`hx-post` elements. This caused tabs to be unresponsive on first implementation.
**How to apply**: After any innerHTML assignment in JS (refreshCards, manual fetches, etc.), always call `htmx.process(el)` on the container. The htmx-mini's internal swap handler already does this, but manual JS bypasses it.
**Source**: `plans/done/260701-1817_MULTI_PROVIDER_TABS_AND_LAUNCH.md` — tabs unresponsive bug, fixed in de1f68a | **Verified**: 2026-07-01


### Playwright MCP server drops connections under sustained use - retry without re-diagnosis

**Why**: In session 945f4664, the user had to say 'try again' 4 times and report 'Transport to MCP server playwright is closed' twice. The agent kept retrying the same approach instead of diagnosing the root cause (MCP server instability).
**How to apply**: When Playwright MCP connection drops during /qqa or /qbrowser-test, report the infrastructure issue immediately rather than retrying silently. If 2 connection attempts fail consecutively, state the MCP server is unstable and offer to verify via code inspection instead.
**Source**: Session 945f4664 (2026-07-01) - Provider-Launcher Unification /qqa phase | **Verified**: 2026-07-05


### Windows .cmd/.bat wrappers fail with subprocess.Popen without shell=True

**Why**: `kiro` on Windows resolves to `kiro.cmd` (a batch wrapper around `Kiro.exe`). `subprocess.Popen(["kiro", "path"])` with `DETACHED_PROCESS` silently fails because `.cmd` files need `cmd.exe` to execute. This caused the "1 failed" launch error for the Kiro IDE provider.
**How to apply**: When launching a binary on Windows via `subprocess.Popen` without a terminal, resolve via `shutil.which()` and check if the result ends in `.cmd`/`.bat`. If so, set `shell=True`.
**Source**: `plans/done/260706-1653_KIRO_IDE_PROVIDER.md` — post-implementation launch failure fix | **Verified**: 2026-07-06


### JS-side display maps must mirror Python-side dicts for provider metadata

**Why**: The Python `_PROVIDER_BINARY_DISPLAY` dict had the correct `kiro-ide: "kiro"` entry, but the JS `_providerBinaryDisplay` object in `index.html` was not updated — causing the provider settings modal to show `"kiro-ide"` as the command. Similarly, `_providerTerminal` was missing, showing "Open in terminal: yes" for a non-terminal provider.
**How to apply**: When adding a new provider, update both the Python dicts in `web.py`/`launcher.py` AND the JS objects in `index.html` (`_providerBinaryDisplay`, `_providerTerminal`). These are duplicated because the modal JS runs client-side.
**Source**: `plans/done/260706-1653_KIRO_IDE_PROVIDER.md` — post-implementation UI fixes | **Verified**: 2026-07-06


### Workspace grouping must deduplicate providers and sort deterministically

**Why**: A workspace with sessions from the same provider in multiple discovery passes produced duplicate provider icons. Provider order was non-deterministic (dict insertion order), causing gradient colors to appear in different orders across page loads.
**How to apply**: In `_group_workspaces`, track seen providers per workspace with a set, merge counts on duplicates, and sort the provider list alphabetically before returning. Also sort the launchers grid and filter tabs.
**Source**: `plans/done/260706-1653_KIRO_IDE_PROVIDER.md` — post-implementation duplicate icon fix | **Verified**: 2026-07-06


### CSS image sizing requires understanding aspect ratio math before iterating — calculate first, style second

**Why**: Agent made 8 CSS change attempts to fix banner sizing (object-fit, max-width, padding, explicit height) before diagnosing that a 1920x219 image in a 48px-high container with max-width:220px needed simple aspect-ratio math. The user explicitly complained "didn't work. image ratio changed now it looks horrible" and "keep iterating and check the results yourself before handing over."
**How to apply**: When a CSS image sizing issue is reported, first check the image's intrinsic dimensions, calculate the needed container dimensions from the aspect ratio, then apply the single correct CSS change. Don't iterate on CSS properties without understanding the math first.
**Source**: Session 52f28138 — banner sizing back-and-forth (8+ turns) | **Verified**: 2026-07-10 | **Outcome**: not-recurred 2026-07-16


### Session-file parsing must be skipped, not just made faster

**Why**: PowerAtlas felt slow on start, card expansion, and updates. Profiling showed the cost was redundant work, not slow work: `_parse_session_file` unconditionally JSON-parsed the first 500 lines of every session file (~472 `json.loads` per file) to recover a title and first prompt that sit in the first few lines, and `refresh_stale_entries` reloaded a whole workspace when any one file changed. The instinct to reach for a faster language would have made the wasted scan faster without making it smaller.
**How to apply**: Before optimizing a parse loop, ask what it can skip. Three layers, cheapest first: (1) don't walk what you can index — `data_kiro._cwd_to_files()` keys metadata by cwd against the session directory's mtime, so `load_sessions` never touches the flat store; (2) a byte prefilter — once `first_prompt` is known, only title lines matter, and every title line contains the bytes `b"title"`, so non-matching lines skip `json.loads`; (3) `(mtime, size)`-keyed parse caches (`data.BoundedCache`) so unchanged files are never re-read, which makes a refresh tick cost work proportional to what changed. Measured on claude-code: 2-3x cold, 37x on a refresh tick, 130-180x on a warm sweep. Note that reading JSONL in binary changes error semantics: text mode with `errors="replace"` turns invalid UTF-8 into U+FFFD *inside* the JSON string and still parses, while `json.loads(bytes)` raises `UnicodeDecodeError` — catch it before `ValueError` (it subclasses it) and re-decode with `errors="replace"` to preserve behavior.
**Source**: `plans/done/260725-1542_PARSE_AND_POLL_PERFORMANCE.md` — differential-tested against 800 corpus files + 15 edge cases | **Verified**: 2026-07-25


### Memoizing a path lookup requires the lookup's roots in the cache key

**Why**: Memoizing `_resolve_jsonl_path` keyed only on `(provider, session_id, cwd)` broke two existing tests: they patch `SESSION_DIR` / `_V3_SESSIONS_ROOT` to different tmp_paths but reuse the same session id and cwd, so one test got another's resolved path back. Production never rebinds those globals, so the bug was invisible outside tests — but the cache was genuinely under-keyed.
**How to apply**: When caching a filesystem lookup, include every module-level root the lookup reads in the key (`str(SESSION_DIR)`, `str(_V3_SESSIONS_ROOT)`). Also scope the cache to the branch that is actually expensive — only the kiro-cli v3 fallback walks directories; the claude-code branch is two syscalls and caching it added staleness for no gain. Revalidate positive entries with `is_file()` so a deleted file re-resolves, and give negative entries a short TTL so a newly created session is still picked up.
**Source**: `plans/done/260725-1542_PARSE_AND_POLL_PERFORMANCE.md` — caught by `TestResolveJsonlPath` regressions | **Verified**: 2026-07-25


### `plans/tests/260701_POWERATLAS.md` describes internals and drifts on refactors

**Why**: The test-harness doc records function names, line caps, and JS timer identifiers (`_parse_session_file`'s head cap, `startPinnedPoll`, `_pinnedPollMax`). The parse-and-poll performance plan invalidated two of its sections, and one figure ("reads first 100 lines") had already been stale before that plan touched it — drift accumulates silently across plans. The project's `## Doc & Test Guidelines` trigger is "user-visible changes", which by its own wording does not fire for internal refactors, so nothing prompts the update during the work; `/qclose` Pass 4 only catches it at archival, after the change has shipped.
**How to apply**: When a PowerAtlas plan changes parse strategy, cache layers, or client timer topology, grep `plans/tests/260701_POWERATLAS.md` for the affected identifiers and add the file to the plan's Documentation Updates table at planning time. Sections 1.6, 1.12, 2.16 and 2.17 are the internals-heavy ones.
**Source**: `plans/done/260725-1542_PARSE_AND_POLL_PERFORMANCE.md` — `/qclose` Pass 4 doc-ripple sweep | **Verified**: 2026-07-25


### `(mtime, size)`-keyed caches make a family of `test_data.py` tests timing-flaky

**Why**: Eight tests fail intermittently under full-suite timing and pass when run standalone, because the cache keys are finer-grained than the filesystem's timestamp resolution — a test writes, reads back, and the cache cannot tell the file changed. `test_kiro_index_picks_up_a_newly_created_session` fails roughly 3 of 5 runs *even standalone*. This is the blast radius of the parse-and-poll cache optimisation (see [[session-file-parsing-must-be-skipped]]), and at this density a genuine `test_data.py` regression can hide in the noise — during one session the same suite reported 2, 3 and 4 failures on consecutive identical runs.
**How to apply**: Before attributing a `test_data.py` failure to your change, re-run that test standalone; if it passes, it is this family, not a regression. Known members: `TestKiroPromptsCache::test_changed_jsonl_is_reparsed`, `test_kiro_load_sessions_sees_rewritten_metadata`, `test_cache_miss_triggers_load`, `test_kiro_index_picks_up_a_newly_created_session`, `test_missing_jsonl_still_returns_session`, `TestKiroPromptsCache::test_missing_jsonl_bypasses_cache`, plus `test_web.py::TestWarmupPinned::test_populates_cache_for_existing_folders` and `test_web.py::TestGetAllSessionsPaginated::test_sort_order_by_updated_at`. A durable fix means giving the cache an explicit invalidation hook the tests can call, not sleeping.
**Source**: `plans/260725_KIRO_CLI_ACP_CLIENT_PROTOTYPE.md` — observed across ~15 full-suite runs during Phase 2 | **Verified**: 2026-07-25

### Tests that skip `load_config` read the developer's real `config.toml`

**Why**: There is no `tests/conftest.py` and the `client` fixture in `tests/test_web.py` provides no config isolation, so any test exercising a route that calls `load_config()` reads `%LOCALAPPDATA%\power-atlas\config.toml` — a populated production file holding real pinned folders and, under `[custom_launchers.env]`, real credentials. Such tests pass or fail depending on the developer's machine state. Twenty were found by wrapping `load_config` in a pytest plugin and recording which tests reached the real function; two were fixed, eighteen remain.
**How to apply**: When adding or debugging a `tests/test_web.py` test that hits a route, patch `power_atlas.web.load_config` with a controlled `Config()` — `test_search_with_tag_filter` is the house pattern. When a route test fails only on your machine, suspect this before suspecting your change. Note the fix is not uniformly mechanical: tests rendering the full workspaces partial depend on real pinned folders for their assertions, so a bare `Config()` changes what they see. The durable fix is a shared autouse fixture, not eighteen decorators.
**Source**: `plans/260725_KIRO_CLI_ACP_CLIENT_PROTOTYPE.md` — found by instrumenting `load_config` during Phase 2 | **Verified**: 2026-07-25

### The launched agent owns the terminal tab title — PowerAtlas cannot hold it

**Why**: kiro-cli sets the terminal title itself once its session starts, overwriting anything the launcher set. Several successive fix attempts (injecting `$Host.UI.RawUI.WindowTitle` into the bare `wt` path, then into `{pscmd}`, a `--suppressApplicationTitle` flag that is a settings.json profile key rather than a CLI flag, an ANSI escape, a default-template rewrite) all failed, and the `{pscmd}` variant broke session launching outright. The user diagnosed it, not the agent.
**How to apply**: Treat the tab title as owned by whatever the terminal launches, not by the launch command. PowerAtlas's `--title` only controls the window between `wt` spawn and agent startup; anything the agent sets afterwards wins. If a title must persist, fix it in the agent's own steering (kiro-cli `tab-title.md`), not in `launcher.py`. Do not re-attempt title injection into `{pscmd}` — that path broke launching once already.
**Source**: session 8cf565d0-a987-4616-a782-cb00af9ff6d7 (2026-07-24), user turn at line 65; work reverted the same day | **Verified**: 2026-07-28
**Evidence-quote**: "the tab name change works well, but kiro-cli sets the title to \"Windows Powershell\" when I open it, so I'll have to handle it from kiro-cli directly"

### Workspace dots must aggregate resolved session statuses, not raw signals

**Why**: A session row showed green (working) while its workspace card showed orange (waiting) for the same single live session at the same tick. `_session_status` let a non-empty provider report win outright, while `_workspace_status` folded the raw report and the raw classifier verdict into a max over `errored > waiting > working` — so a lagging transcript tail could only ever *raise* the card and silently outranked the provider's first-hand "busy".
**How to apply**: Aggregate `_resolved_session_status(...)` outputs, never the raw `(reported_status, semantic)` pair, so precedence is decided once per session. A card may still outrank a row, but only on the strength of a *different* session or of the errored verdict the row honours too. When touching either function, re-check that both surfaces read the same settled value — the pair has diverged twice.
**Source**: claude-code session 6ab328ed-b9e7-41e2-8e66-2efe2a1a3afa (2026-07-28), line 87; fixed in commit 09cbbe1 | **Verified**: 2026-07-28
**Evidence-quote**: "The real design smell is that `_workspace_status` aggregates *raw signals* rather than *resolved session statuses*. If it aggregated `_session_status` outputs, precedence would be decided once, per session"

## Feedback

### Provider context must be identified from visual cues in screenshots, not assumed

**Why**: In session 57a3df8b, the user corrected: 'My screenshot was a claude code session!' - the agent analyzed a screenshot but assumed kiro-cli. Claude Code and kiro-cli have visually distinct UI patterns.
**How to apply**: When the user shares a screenshot of a session or terminal output, identify the provider (kiro-cli vs Claude Code vs Kiro IDE) from visual cues before analyzing content. Ask if uncertain rather than assuming kiro-cli as default.
**Source**: Session 57a3df8b (2026-07-03) - session title hot-reload fix | **Verified**: 2026-07-05

### PowerAtlas UI features cluster into multi-plan sequences — scope early or defer split

**Why**: The July 5-9 sprint shows clustering: workspace cards → Kiro IDE provider → panel restructure → workspace tags → session panel style. Each plan builds on the previous. The tag feature required a follow-up to extend filtering to sessions panel.
**How to apply**: When exploring a PowerAtlas UI feature that touches panel structure, filtering, or workspace metadata, explicitly ask during /qexplore whether downstream panels/views should also be scoped in. Avoids the pattern of plan N+1 being 'extend plan N's feature to the other panel'.
**Source**: Plan cluster analysis: 260709-1146 (tags) followed by 260709-1352 (unified filtering) | **Verified**: 2026-07-09

### WinForms threading: UI property changes must be marshalled via Invoke from background threads

**Why**: Agent made 5 attempts at fixing peek window taskbar icon before landing on the Invoke pattern. User reported failure 3 times and a COM exception crash. The WinForms Invoke pattern for cross-thread UI changes is the canonical solution for pywebview on Windows.
**How to apply**: When modifying pywebview native window properties (ShowInTaskbar, window style, visibility) from a non-UI thread (pynput hotkey handler, event callback), always use native.Invoke(WinForms.MethodInvoker(lambda: ...)) to marshal the call to the UI thread. Never assign UI properties directly from background threads.
**Source**: Session 2ec9143d (2026-07-15) — 5 implementation attempts before correct pattern | **Verified**: 2026-07-16

### psutil must be installed in the runtime venv, not just listed in pyproject.toml

**Why**: Live status feature appeared completely broken because psutil was in pyproject.toml but not pip-installed. The web server silently fell back to is_available()=False. Required runtime debugging to discover.
**How to apply**: After adding a new dependency to pyproject.toml, verify it's actually installed in the active venv with `pip show <pkg>`. When live-testing features dependent on optional packages, check installation status FIRST before debugging behavior.
**Source**: Session 4f376bb5 (2026-07-12) — silent fallback masked root cause | **Verified**: 2026-07-16

### User expects agent to restart PowerAtlas itself during development iterations

**Why**: After 5 iteration attempts on the peek window fix, user said 'restart it yourself from now on' — indicating frustration with manual restart cycles.
**How to apply**: During PowerAtlas development iterations requiring runtime verification, kill the existing PowerAtlas process and restart it using the venv's Python before asking the user to test. Don't ask the user to restart manually.
**Source**: Session 2ec9143d (2026-07-15) — user correction | **Verified**: 2026-07-16

## Decision

### Rejected integration paths live in `plans/CLOSED_INVESTIGATIONS.md`, not in the roadmap

**Why**: Two rounds of spikes on 2026-07-24 rejected three kiro-cli paths on measurement, not on taste: `_kiro.dev/session/list` (6.5x slower cold than the filesystem scan, byte-identical data, and it loses the 3 sqlite-only workspaces), `kiro-cli serve` (reads the dormant v3 store rather than the `cli/` store `status_classifier.py` tails, and masks every externally-owned `in_progress` down to `idle` by documented design), and kiro-cli remote control (no independent path — it is blocked entirely behind serve's two walls). All three read as obvious wins from their own documentation, so they resurface easily. The evidence was moved out of `plans/ROADMAP.md` on 2026-07-25 to keep that file forward-looking.
**How to apply**: Before proposing any kiro-cli enumeration or control path, read `plans/CLOSED_INVESTIGATIONS.md` — each verdict carries its numbers and an explicit reopen condition. The verdicts are pinned to kiro-cli `2.14.0`/`2.14.1` and Claude Code `2.1.219`, all self-updating, so re-measure rather than re-argue if the binaries have moved. New rejections belong in that file, not as struck-through roadmap bullets. **ACP is the carve-out, and it is open, not closed**: a 2026-07-26 prototype (`plans/done/`, ACP client) established that `kiro-cli acp` both launches sessions and reopens exited ones — including terminal-created ones — with `session/load` measured non-mutating (`.json`/`.jsonl` byte-identical by sha256, only the `.lock` rewritten). Do not read this entry as blanket closure of kiro-cli control. What that prototype did *not* answer is unattended use: `session/request_permission` has never round-tripped, and under `-a` an unprompted `shell` command executed outside its session cwd on an unidentified trigger.
**Source**: `plans/CLOSED_INVESTIGATIONS.md` — extracted from `plans/ROADMAP.md` 2026-07-25 | **Verified**: 2026-07-25

### PowerAtlas runs only on the checkout venv, enforced by a re-exec guard

**Why**: Four entry points chose an interpreter independently — the pip console script, `python -m power_atlas`, the autostart shortcut, and `_relaunch_detached` — so the app drifted onto the global interpreter while the suite ran in `.venv-PowerAtlas`, three starlette majors apart. That split hid a security defect the suite was structurally unable to observe: `_ws_origin_ok` accepted `Host: evil.com@127.0.0.1:4915` as loopback on starlette 0.37.2, because the raw Host reaches `URL` unvalidated, while 1.3.1's `_HOST_RE` rejects `@` before the function is reached. Closed 2026-07-28 — `interpreter.ensure_project_interpreter()` re-execs into the checkout venv at the top of `main()`, `autostart.enable()` resolves the venv from the checkout instead of from `sys.executable`, and the global editable install was uninstalled.
**How to apply**: Verify in `.venv-PowerAtlas` — it is the interpreter the app runs on, so a green suite is now evidence about the running app. Never `pip install -e .` into a global interpreter. Detection is by `sys.prefix`, not `sys.executable`: on Windows the venv's `python.exe` is a redirector whose image path is the base install, so an executable comparison reports a false negative (WMI `ExecutablePath` shows the same trap). Two silent-fallback edges: `project_venv_dir()` returns None when a checkout holds two off-convention `.venv*` directories, and a venv missing the package makes every entry point fail invisibly under `pythonw`. The `power-atlas` command is a shim at `~/.local/bin/power-atlas.cmd`, not a pip console script — the venv's `Scripts` is deliberately kept off PATH because it also carries pip/pytest/ruff.
**Source**: Session 7d812251 (2026-07-28) — verified by launching from the global interpreter and confirming the surviving process mapped only venv site-packages | **Verified**: 2026-07-28

## Declined

<!-- Declination records: the user's Skip of an agent-initiated memory proposal. A live row here suppresses re-proposal of that subject for 60 days (window owned by shared/skills/qdream/memory-rules.md § Memory File Format → Declined records). NOT a fourth type and rows are NOT entries (no Type/Usage/Outcome; excluded from the Size advisory and the prune order). Sessions append rows only; the /qdream sweep prunes expired rows and rows whose subject is now a live entry. This heading is guarded by verify-citations — never remove it, even with zero rows. Row format: - "<proposed heading>" — declined <YYYY-MM-DD> (<reason, if given>) -->
