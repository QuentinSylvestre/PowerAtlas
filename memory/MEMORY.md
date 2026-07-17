# Project Memory — PowerAtlas

## Pattern

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


### test_presence_matches_claude_resume_id fails on Windows — known pre-existing platform failure, deselect rather than re-diagnose

**Why**: The /qdev run for 260715_SEMANTIC_SESSION_STATUS spent multiple turns (both phase sub-agents plus an orchestrator check against main) confirming this failure was pre-existing rather than caused by the change. The test (tests/test_data.py:1426) still asserts Unix-style '/home/u/proj' while _normalize_path produces backslashed paths on Windows (grep-verified 2026-07-16), so every future Windows test run in this project will hit it and risk misattributing it to the current change.
**How to apply**: When the PowerAtlas suite fails only on test_presence_matches_claude_resume_id on Windows, treat it as the known pre-existing platform failure and deselect it (as done in the 260715 run); fixing the path normalization belongs in its own task, not mid-plan.
**Source**: Session c431d086 (2026-07-15) — /qdev phases 1-2 test run | **Verified**: 2026-07-16


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

### Semantic session status uses 4-state vocabulary: Active, Needs-input, Idle, Errored

**Why**: User explicitly merged executing/thinking into 1 Active status during exploration. Design deliberately avoids v3-specific features but abstracts for future v3 support.
**How to apply**: When adding session status features or extending the classifier, use the 4-state vocabulary. Do not re-split Active into sub-states. v3 kiro-cli support is designed-for but deferred.
**Source**: Plan 260715-1407_SEMANTIC_SESSION_STATUS — user decision during /qexplore | **Verified**: 2026-07-16

## Declined