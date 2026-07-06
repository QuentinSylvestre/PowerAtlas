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

## Feedback

### Provider context must be identified from visual cues in screenshots, not assumed

**Why**: In session 57a3df8b, the user corrected: 'My screenshot was a claude code session!' - the agent analyzed a screenshot but assumed kiro-cli. Claude Code and kiro-cli have visually distinct UI patterns.
**How to apply**: When the user shares a screenshot of a session or terminal output, identify the provider (kiro-cli vs Claude Code vs Kiro IDE) from visual cues before analyzing content. Ask if uncertain rather than assuming kiro-cli as default.
**Source**: Session 57a3df8b (2026-07-03) - session title hot-reload fix | **Verified**: 2026-07-05