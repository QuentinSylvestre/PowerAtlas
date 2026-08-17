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

### `_kiro.dev/commands/options` kills kiro-cli when called with `command: ""`

**Why**: Sending `_kiro.dev/commands/options` with an empty `command` field causes kiro-cli to exit with code 0, no stderr. Observed when the `/` command palette sent it as a debounced WS request during command-name filtering. The method is designed for argument completion *after* a command is selected — tui.js always passes a non-empty command name. Sending `command: ""` is not a supported call.
**How to apply**: Never send `_kiro.dev/commands/options` with `command: ""`. For command-name filtering (the dropdown while the user types `/prefix`), use client-side filtering of the `commands/available` catalogue only. Only call `_kiro.dev/commands/options` after the user has selected a specific command, passing that command's name.
**Source**: Live observation 2026-08-12, confirmed from tui.js `getCommandOptions` call sites (always pass `e.name`, never `""`); fixed in commit `a245bce` | **Verified**: 2026-08-12


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

### Real-config exposure in tests is closed by two module-level autouse fixtures — do not narrow them

**Why**: A test reaching `load_config`/`save_config` or the four remote-secret functions without isolation reads or writes `%LOCALAPPDATA%\power-atlas\config.toml` — a populated production file holding real pinned folders and, under `[custom_launchers.env]`, real credentials. **The write side is the worse half**: a test that patches `load_config` to return a *default* `Config()` while leaving `save_config` real does not write one wrong key, it writes an entirely default config over the populated one. This entry previously recorded "eighteen remain" on the read side; a census of all seven modules on 2026-08-03 measured **296 calls from 115 distinct tests across the six entry points, zero of which resolve to the real path**. Only `tests/test_config.py` (52 tests) and `tests/test_web.py` (63) touch the surface at all — the other five modules never call it — and both carry a module-level `autouse` `isolated_config` fixture. The class is closed; the entry now exists to keep it closed.
**How to apply**: Do not scope either `isolated_config` down to the tests that "mean to" write. That shape already failed: a test asserting a *refusal* reaches `save_config` the moment the guard it probes is mutated, which is exactly what a mutation run does — and that is how four stray keys and a live `remote-secret` landed in the real config during Phase 3. A new config-touching test inherits isolation automatically; write the config you want into `tmp_path`. **`REMOTE_SECRET_PATH` must be patched by name**: it is computed at *import time* as `CONFIG_DIR / "remote-secret"`, so rebinding `CONFIG_DIR` does not move it (that gap was live in `tests/test_config.py` until 2026-08-03). To re-verify: wrap the six entry points, repoint the module attributes at a sentinel directory, and record which tests resolve there — and run a positive control, because an instrument that reports zero and one that is broken look identical.
**Source**: `plans/260731_ACP_REMOTE_CLIENT_PRODUCTIZATION.md` F-11 — census across all 7 modules, with positive control | **Verified**: 2026-08-03 (session)

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

### A new context variable added to a shared Jinja partial must be added at EVERY route that renders it

**Why**: `partials/workspace_card.html` is rendered from two call sites in `web.py` (updated 2026-08-17: lines ~3151 and ~3178, both inside `_render_workspace_groups` — before the 260817 pipeline unification plan there were four direct call sites at lines 3015, 3048, 3300, 3329). A session added `hover_launchers` to the `partials_workspaces` route only; the `search` route's two render calls omitted it, so `{% for hl in hover_launchers %}` iterated an undefined and silently produced zero hover buttons. 862 pytest checks passed and the change was committed and pushed (`dde7de5`); the user found the defect as the opening turn of the next session (fixed in `e4fced3`). Jinja's default undefined is falsy-iterable, so neither the template nor the suite can see the omission — only a call-site census can.
**How to apply**: When adding a variable to a template's render context, grep for every `get_template("<that template>").render(` call site before running tests, and add the variable at each. After the 260817 pipeline unification, all `workspace_card.html` renders go through `_render_workspace_groups` — a new variable added there propagates to both `partials_workspaces` and `search` automatically, but the helper itself is the new single call-site to check. Do not rely on the pytest suite or `tests/acp_page.test.mjs` to catch a missing context key — a green suite is compatible with the bug.
**Source**: kiro-cli sessions 848ea02d (introduced, `dde7de5`) and ff0b8d69 (reported and fixed, `e4fced3`), 2026-07-29/31; call-site census updated 2026-08-17 after 260817 pipeline unification | **Verified**: 2026-08-17 (grep confirmed 2 call sites inside `_render_workspace_groups` at ~3151, ~3178)
**Evidence-quote**: "When searching for workspaces, quick provider actions do not show on hover, why?"

### `launcher.py` passes no `env=`, so launched sessions inherit the parent's `CLAUDE_CODE_*` markers

**Why**: Measured on the live tray process 2026-08-03: the PowerAtlas process carries `CLAUDECODE`, `CLAUDE_CODE_CHILD_SESSION`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_BRIDGE_SESSION_ID` and `CLAUDE_PID`, inherited from whichever Claude Code session started it. `launcher.py:191-192` calls `subprocess.Popen(cmd, **kwargs)` with no `env=` (verified 2026-08-05: no `env=` on any Popen in the file), so every session launched from the quick-access button receives that whole block — which is why a launched Claude reports transcript saving off. It leaks more than a nested flag: `CLAUDE_CODE_SESSION_ID` hands the child a *specific other session's* identity.
**How to apply**: When debugging launched-session identity or transcript-persistence problems, check `launcher.py`'s `Popen` call for an `env=` argument first — as of 2026-08-05 there is none. A fix must scrub the `CLAUDE_CODE_*` / `CLAUDECODE` / `CLAUDE_PID` keys from the child environment rather than relying on the child to ignore them. Before trusting a recollection that something was fixed, run `git log -- <the file the fix would touch>`: commit `a4f8c72` (2026-07-28) is a `docs(roadmap)` commit touching only `plans/ROADMAP.md` — it recorded the problem without implementing anything, which reads like a fix in `git log`.
**Source**: session 361b50c5 (2026-08-03), PEB measurement of the live tray process | **Verified**: 2026-08-05 (sweep, cross-validated — code and commit both re-checked)

### kiro-cli subagent nesting is capped at depth 2 — scale with `stages`, not recursion

**Why**: Measured on kiro-cli 2.16.1 over `kiro-cli acp -a`, five probes with on-disk witnesses. The delegation tool is named **`subagent`**, not `use_subagent` as `~/.kiro/agents/kiro_default.json`'s system prompt still claims — that prompt has drifted from the implementation, so an agent following it looks for a tool that does not exist. A spawned subagent does **not** receive the tool: dumping both tool inventories to disk gave `parent only: [subagent, todo_list]`, `child only: [summary]`, 13 shared. The stripping is structural, not advisory — a child asked to recurse called `tool_search` for `"subagent blocking spawn"`, got nothing, and reported `DEPTH 2 BLOCKED`. What *does* scale is one call's `stages` DAG: ten stages chained by `depends_on` were all accepted and ran strictly serially (mtimes 20-25 s apart, monotonic; stage 9 saw exactly 8 predecessors, stage 10 saw 9), each as its own ACP session id. A subagent under `-a` can still bypass the ceiling by shelling out to `kiro-cli chat --no-interactive`, which reached depth 4 on demand — but those levels emit **zero ACP frames**, so the ceiling is real for anything the protocol can observe and worthless as containment.

**How to apply**: Design kiro-cli delegation wide, not deep — one orchestrator plus an N-stage DAG in a single `subagent` call. Do not plan for a subagent that itself delegates; it cannot, and no config knob was found that restores the tool. For PowerAtlas specifically, note the consequence of subagent frames carrying a session id `_Supervisor.sessions` never created: `_stamp_activity` (`acp.py:2271`) early-returns on the unknown id, so the parent's `last_activity` is not advanced for the whole crew — measured **212.5 s of parent silence** for ten *trivial* stages while the connection stayed busy (max gap across all frames 20.7 s). A crew exceeding `PROMPT_SILENCE_SECONDS` (900 s) is therefore cancelled by `acp.py:2012` while healthy, and because `_emit` routes through `_registry.broadcast(session_id, …)` with no socket subscribed to a child id, `/acp` renders nothing until the crew finishes. Verify against the current constants before acting — both were re-measured on 2.16.0/2.16.1 and may move.

**Source**: session 0b648eba (2026-08-06) — five ACP probes (depth-2 sanity, 3-deep recursion, tool-inventory diff, 10-stage DAG, shell-out escape); harness and raw JSONL streams were scratchpad-only and are **not** retained | **Verified**: 2026-08-06 (session, empirical — every depth claim witnessed by a file on disk or a session id on the wire, never by model self-report) | **Stale-when**: kiro-cli minor version changes — the tool name, the stripped-tool set and the stage cap are all build-specific

**Evidence-quote**: "The `subagent` tool is not available in this harness. I cannot spawn a child agent." — the depth-2 child, after `tool_search` returned nothing

**See also**: [[kiro-cli-subagent-wire-shapes-were-unmeasured-until-2026-08-11]] — field-level wire shape (`status.type` vocabulary, channel reach, `task` sizing) rather than fan-out depth/width/timing.

### kiro-cli subagent wire shapes were unmeasured until 2026-08-11 — corrected in `acp.py`

**Why**: `acp.py`'s SUBAGENT_LIST_METHOD/SUBAGENT_ACTIVITY_METHOD/`_SUBAGENT_ACTIVE_STATUSES` comments were explicitly self-flagged as "inferred, not measured against a live kiro-cli" — corroborated only against kirodotdev/kirocrew's independent ACP client, never against PowerAtlas's own traffic. Direct capture against a real `kiro-cli acp -a` subprocess (spawned and driven outside PowerAtlas — no server involved) found three concrete corrections: (1) the only `status.type` values kiro-cli 2.16.2 ever sends are `working` and `terminated` — `done`/`completed`/`failed`/`error` were never observed even once, including for a sub-agent whose own shell command was deliberately made to fail, so `_on_subagent_list`'s error-message extraction (gated on `stype in ("failed", "error")`) is unreachable against every vocabulary measured so far; (2) `SUBAGENT_ACTIVITY_METHOD` (`_kiro.dev/session/update`) is not sub-agent-exclusive — it also fires for the parent session's own tool calls, and is safe only because the `tool_call_chunk` handler gates on `subagent_sessions` membership rather than on the method name; (3) a crew entry's `task` field (from `initialQuery`, which always won over the short `sessionName` label since both were always populated) rode the `subagents` wire frame completely untruncated, unlike every other agent-authored string this file renders. Complements [[kiro-cli-subagent-nesting-is-capped-at-depth-2-scale-with-stages-not-recursion]], which covers fan-out depth/width/timing rather than field-level wire shape.

**How to apply**: To verify or extend an ACP wire-shape assumption against a real kiro-cli build, don't route through PowerAtlas's own server — spawn `kiro-cli acp -a` directly as a subprocess (`subprocess.Popen(["kiro-cli", "acp", "-a"], stdin=PIPE, stdout=PIPE, ...)`), drive the `initialize` → `session/new` → `session/prompt` handshake yourself using the exact JSON-RPC shapes `_Supervisor._request`/`_notify` build, log every raw NDJSON line (both directions) to a file, and grep it for the method(s) in question. Prompting the agent to "use the `subagent` tool to dispatch N parallel stages doing trivial tasks" reliably exercises `_kiro.dev/subagent/list_update` and the tool-call channels within under a minute; adding one stage pointed at a path guaranteed to fail is what pins the failure-path vocabulary rather than leaving it assumed. All the corrected facts above are now in `acp.py`'s own comments (SUBAGENT_LIST_METHOD, SUBAGENT_ACTIVITY_METHOD, `_SUBAGENT_ACTIVE_STATUSES`, `_SUBAGENT_ROLE_KEYS`/`_SUBAGENT_TASK_KEYS`, `MAX_SUBAGENT_TASK_CHARS`, the `error =` line in `_on_subagent_list`) — read those rather than this entry for the wire shape itself; this entry is the reusable *method*, not a snapshot of the facts, which will drift as kiro-cli updates. Two behavior fixes shipped alongside the doc corrections: `task` is now clipped to `MAX_SUBAGENT_TASK_CHARS` (4000 chars), and a `tool_call_update` carrying only a streamed `content` block (a shape `_tool_payload` cannot read) is no longer forwarded as an all-blank `tool_update` frame that could transiently blank a populated row.

**Source**: this session (2026-08-11) — two direct `kiro-cli acp -a` 2.16.2 subprocess captures (a 3-stage all-succeed fan-out, and a 2-stage fan-out with one stage's command deliberately failed), both logged to session scratchpad only (not retained, same practice as the depth-2 probe above); corrections landed in `acp.py` (SUBAGENT_LIST_METHOD/SUBAGENT_ACTIVITY_METHOD/`_SUBAGENT_ACTIVE_STATUSES`/`_SUBAGENT_ROLE_KEYS`/`_SUBAGENT_TASK_KEYS`/`MAX_SUBAGENT_TASK_CHARS` comments, the `error =` line, the `agent_message_chunk` dispatch comment, and the content-only `tool_call_update` skip) and `tests/test_web.py` (matching comment corrections plus two new regression tests, `test_an_oversized_task_is_clipped` and `test_a_content_only_update_is_not_forwarded`) | **Verified**: 2026-08-11 (session, empirical — every claim backed by a raw captured JSON-RPC line, not model self-report)
**Stale-when**: kiro-cli minor version changes past 2.16.2 — like the depth-2 entry, the exact status vocabulary and channel behavior are build-specific and should be re-measured rather than assumed to persist.

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

### `acp.py` and `presence.py` may not import each other — wiring goes through `web.py`

**Why**: Both directions are closed, for different reasons, and neither is visible from either file alone. `acp.py` declares an isolation boundary in its own module header — it imports exactly two names from the package (`config.CONFIG_DIR`, `launcher._SESSION_ID_RE`) and a plan exit criterion greps the file for module names to keep it honest — so `acp → presence` breaks a documented invariant. The reverse is barred by D9: `presence` runs on worker threads and `_supervisor.sessions` is loop-owned and unlocked, so reading it there races every mutation the loop makes. Both obvious designs were written and undone before this was understood, while closing D32's false-live residual.
**How to apply**: When something must connect the two, inject it from `web.py`, which already imports both — a module-level hook in `acp` set at lifespan (`acp.set_sessions_changed_hook`), not an import in either module. Pass an **immutable snapshot** rebound by the loop, never a live view of loop-owned state; that is what makes the read thread-safe without a lock and is the reason D9's objection does not apply to it. Verify the boundary afterwards: the package imports in `acp.py` must still be exactly `config` and `launcher`.
**Source**: `plans/done/260803-1103_ACP_REMOTE_CLIENT_PRODUCTIZATION.md` § Deferred Follow-ups F-5, and D9/D32 in that plan's Design Decisions | **Verified**: 2026-08-03 (session)

### Rejected integration paths live in `plans/CLOSED_INVESTIGATIONS.md`, not in the roadmap

**Why**: Two rounds of spikes on 2026-07-24 rejected three kiro-cli paths on measurement, not on taste: `_kiro.dev/session/list` (6.5x slower cold than the filesystem scan, byte-identical data, and it loses the 3 sqlite-only workspaces), `kiro-cli serve` (reads the dormant v3 store rather than the `cli/` store `status_classifier.py` tails, and masks every externally-owned `in_progress` down to `idle` by documented design), and kiro-cli remote control (blocked behind serve's two walls). All three read as obvious wins from their own documentation, so they resurface easily. The evidence was moved out of `plans/ROADMAP.md` on 2026-07-25 to keep that file forward-looking.
**How to apply**: Before proposing any kiro-cli enumeration or control path, read `plans/CLOSED_INVESTIGATIONS.md` — each verdict carries its numbers and an explicit reopen condition. The verdicts are pinned to kiro-cli `2.14.0`/`2.14.1` and Claude Code `2.1.219`, all self-updating, so re-measure rather than re-argue if the binaries have moved. New rejections belong in that file, not as struck-through roadmap bullets. **Two of the three verdicts have since moved, and how they moved is the reusable part.** *Remote control*: its "no independent path" clause was **falsified 2026-08-01** — `260731_ACP_REMOTE_CLIENT_PRODUCTIZATION` shipped a NetBird-reachable `/acp` by having PowerAtlas host the sessions itself, needing no `serve` capability at all. The walls were load-bearing for the **route** (attaching to a session another process owns, still refused by the session lock) and not for the **goal** (reaching kiro-cli from another device). **Before accepting any "blocked" verdict here, separate the two.** *ACP*: open, not closed, and now product rather than prototype — `acp.py` was kept and hardened (D1), not rebuilt. What that plan still does **not** answer is unattended use: it ships `-a` knowingly, so every `## Automation & Workflows` item remains gated on posture, not capability. The two unattended blockers this entry used to name are both retired — `session/request_permission` round-trips in both directions with deny genuinely enforced (measured 2026-07-31 on 2.16.0), and the "unidentified trigger" for the unprompted `shell` write is identified as PowerAtlas's own `~\.kiro\steering\session-tab-title.md` under `-a`, `allowed_write_paths` being descriptive metadata with nothing enforcing it.
**Source**: `plans/CLOSED_INVESTIGATIONS.md` — extracted from `plans/ROADMAP.md` 2026-07-25; falsification and blocker retirements from `260731_ACP_REMOTE_CLIENT_PRODUCTIZATION` Phase 0/6 and the 2026-07-31 permission spike | **Verified**: 2026-08-01 (session, by re-reading the shipped `web.py` allowlist and `plans/CLOSED_INVESTIGATIONS.md` against the plan's Phase 0 measurements) | **Stale-when**: kiro-cli moves off 2.16.0 — the permission and terminate findings are pinned to it, and one measured behaviour has already regressed across a version bump once

### A deferred follow-up's claim about the code is a hypothesis — re-verify before acting

**Why**: Follow-up items record what was true when someone wrote them, and they are written at the moment of least patience. F-9 stated that a brittle text-pinning test "has no unique coverage" and that "the mjs harness already covers the same property behaviourally" — acting on that would have deleted it. Mutation showed the harness covered **1 of its 5 properties**; the other four were guarded by nothing else, so the deletion would have dropped real coverage while reading as cleanup. The item was written by someone who had just been blocked by that test twice, which is exactly when a convenient claim goes unchecked.
**How to apply**: Before executing a follow-up that deletes, retires, or relies on something existing, re-derive its factual claim rather than inheriting it. For a coverage claim that means mutating the behaviour and watching what fails — reading two tests and judging them equivalent is what produced the wrong claim in the first place. The same applies to a follow-up's severity: F-2's "reachable by all 17 peers" was inferred, not measured, and was wrong (see [[a-bound-socket-is-not-a-reachable-service]]).
**Source**: `plans/done/260803-1103_ACP_REMOTE_CLIENT_PRODUCTIZATION.md` § Deferred Follow-ups F-9 | **Verified**: 2026-08-03 (session)

### A bound socket is not a reachable service — read the firewall before recording an exposure

**Why**: A Phase 3 socket census recorded "TCP 139 listens on `100.78.142.124`, reachable by all 17 peers" and it sat in `plans/ROADMAP.md` as `[SECURITY — open]` for two days. Measured 2026-08-03: all 19 inbound File and Printer Sharing rules are **disabled**, including both `NB-Session-In` rules — the only ones that admit TCP 139 — so it is bound and unreachable, and SMB on 445 with it. The genuine residual was a different port and a much smaller one: UDP 137/138, admitted by two `Network Discovery` rules scoped to the Private profile. The wrong finding nearly drove a firewall-profile change with real downside to local device discovery.
**How to apply**: `Get-NetTCPConnection` answers what is **bound**. Reachability needs the rules — `Get-NetFirewallRule` filtered to inbound + enabled + Allow, plus the interface's `Get-NetConnectionProfile` category, since Windows scopes rules by profile and a WireGuard tunnel classified Private admits things Public does not. State which of the two you measured when recording a finding. Note `Get-NetFirewallPortFilter` needs elevation while `Get-NetFirewallRule` does not.
**Source**: `plans/done/260803-1103_ACP_REMOTE_CLIENT_PRODUCTIZATION.md` § Deferred Follow-ups F-2 | **Verified**: 2026-08-03 (session)

### PowerAtlas runs only on the checkout venv, enforced by a re-exec guard

**Why**: Four entry points chose an interpreter independently — the pip console script, `python -m power_atlas`, the autostart shortcut, and `_relaunch_detached` — so the app drifted onto the global interpreter while the suite ran in `.venv-PowerAtlas`, three starlette majors apart. That split hid a security defect the suite was structurally unable to observe: `_ws_origin_ok` accepted `Host: evil.com@127.0.0.1:4915` as loopback on starlette 0.37.2, because the raw Host reaches `URL` unvalidated, while 1.3.1's `_HOST_RE` rejects `@` before the function is reached. Closed 2026-07-28 — `interpreter.ensure_project_interpreter()` re-execs into the checkout venv at the top of `main()`, `autostart.enable()` resolves the venv from the checkout instead of from `sys.executable`, and the global editable install was uninstalled.
**How to apply**: Verify in `.venv-PowerAtlas` — it is the interpreter the app runs on, so a green suite is now evidence about the running app. Never `pip install -e .` into a global interpreter. Detection is by `sys.prefix`, not `sys.executable`: on Windows the venv's `python.exe` is a redirector whose image path is the base install, so an executable comparison reports a false negative (WMI `ExecutablePath` shows the same trap). Two silent-fallback edges: `project_venv_dir()` returns None when a checkout holds two off-convention `.venv*` directories, and a venv missing the package makes every entry point fail invisibly under `pythonw`. The `power-atlas` command is a shim at `~/.local/bin/power-atlas.cmd`, not a pip console script — the venv's `Scripts` is deliberately kept off PATH because it also carries pip/pytest/ruff.
**Source**: Session 7d812251 (2026-07-28) — verified by launching from the global interpreter and confirming the surviving process mapped only venv site-packages | **Verified**: 2026-07-28

### `_session/steer` is available on kiro-cli 2.16.x, accepts raw text, echoes via AgentExecutionSteeringInjected

**Why**: O1/O2 from the ACP UI Feature Batch exploration were open items about whether `_session/steer` existed on the installed build and whether KiroCrew's `<user_message>` wrapping was required. Both verified by live probe (2026-08-12, kiro-cli 2.16.x).
**How to apply**:
- `_session/steer` is available and works. Call it as a JSON-RPC request (it returns `{"result": {"queued": true}}`), NOT as a notification — unlike `session/cancel`. Send raw text in the `message` field; no `<user_message>` wrapping needed (that is KiroCrew's own convention, not a protocol requirement).
- `agentCapabilities` from `initialize` does NOT include a `supports_steer` flag. No runtime check is possible from capability negotiation. Since `acp.py` spawns kiro-cli itself, assume steer is always available.
- The injection echo arrives as `_kiro.dev/session/update` with `sessionUpdate: "AgentExecutionSteeringInjected"` carrying `messageId` and `content`.
- Model may refuse a steer instruction it judges unhelpful — this is model behavior, not a protocol failure.
**Source**: session 2026-08-12 — live probe, `acp_steer_probe3.py`, kiro-cli 2.16.x | **Verified**: 2026-08-12
**Stale-when**: kiro-cli minor version changes past 2.16.x

### `subagent_sessions` must survive turn-end — it is the click-to-view routing key

**Why**: Phase 1 of 260813-1559_ACP_INLINE_CREW_PANEL popped `subagent_sessions[child_id]` at turn-end as part of crew cleanup. This produced a High finding: after a fan-out completes, `_handle_subscribe(child_id)` routes to `_handle_subagent_subscribe` via `subagent_sessions.get(child_id)` — without it, every post-turn click on a sub-agent row returns `unknown_session`. `subagent_sessions` looks like a crew membership cache (alongside `crews` and `_bubbles`, which ARE safe to pop at turn-end), but it doubles as a live routing key for a user interaction that occurs after the turn ends.
**How to apply**: In `_evict_crew_children` (or any equivalent cleanup helper), always use `keep_history=True` at turn-end — preserving both `subagent_sessions` and `subagent_history` for click-to-view. Only the turn-start full-evict path (`keep_history=False`, before a new fan-out begins) clears them. Add a comment at the `subagent_sessions` field declaration noting it is the routing key for click-to-view, not just a fan-out membership cache.
**Source**: `260813-1559_ACP_INLINE_CREW_PANEL` — Phase 1, H1 finding | **Verified**: 2026-08-13

### No-anchor fan-out JS slot: assign a sentinel key on first creation, reuse on updates

**Why**: The initial Phase 3 design computed a fresh `_na_N` sequence key on every `setCrew` call when `toolCallId` was empty. Each status update to the same no-anchor fan-out created a new slot, a new orphaned panel, and a new running `setInterval` timer. A single fan-out with 5 updates produced 5 invisible panels and 5 live timers (High finding, Phase 3+4 review). The fix: a module-level `_noAnchorKey` sentinel stores the key assigned on first creation; subsequent updates reuse it. The sequence counter still advances for genuinely new fan-outs (SC3), but not for updates.
**How to apply**: Whenever a JS slot map is keyed by a server-provided identifier that can be empty/null, assign a locally-generated key (`_na_` + `++_noAnchorSeq`) on first creation, store it in a sentinel variable, and return that sentinel on all subsequent calls until `removeAllCrewPanels()` resets it to `null`. Guard the empty-entries early-return before the key computation to avoid ghost-key side effects from spurious empty frames.
**Source**: `260813-1559_ACP_INLINE_CREW_PANEL` — Phase 3+4 review, H1 finding | **Verified**: 2026-08-13

### Use `patch.object` coroutine injection to seed mid-turn dict state in cleanup tests

**Why**: Two Phase 2 tests (`test_turn_end_cleanup_pops_toolcallid`, `test_turn_start_clears_stale_toolcallid`) were vacuous: the dict key they asserted was removed had never been set during the synthetic turn. Without a real `_on_subagent_list` call inside `_handle_prompt`, `crew_spawn_toolcallids` stayed empty, so both a correct and a broken cleanup passed identically (Medium findings M4+M5). Pre-seeding outside the call fails because the turn-start unconditional pop clears the value before the turn-end pop can be tested.
**How to apply**: To prove a specific cleanup site (turn-start vs turn-end) fired, inject state using `patch.object(_Supervisor, 'prompt', ...)` — replace the coroutine with an async lambda that seeds the target dict mid-turn (after turn-start pop has run) before returning. This gives discriminating coverage for both sites independently. Verify discrimination by commenting out the production cleanup line and confirming the test fails.
**Source**: `260813-1559_ACP_INLINE_CREW_PANEL` — Phase 2 review, M4+M5 findings | **Verified**: 2026-08-13

## Declined

<!-- Declination records: the user's Skip of an agent-initiated memory proposal. A live row here suppresses re-proposal of that subject for 60 days (window owned by shared/skills/qdream/memory-rules.md § Memory File Format → Declined records). NOT a fourth type and rows are NOT entries (no Type/Usage/Outcome; excluded from the Size advisory and the prune order). Sessions append rows only; the /qdream sweep prunes expired rows and rows whose subject is now a live entry. This heading is guarded by verify-citations — never remove it, even with zero rows. Row format: - "<proposed heading>" — declined <YYYY-MM-DD> (<reason, if given>) -->
- "`commands_execute` must whitelist every item type the palette can dispatch" — declined 2026-08-14
- "A new `acp.py` frame type must be added to `SERVER_TYPES` atomically with its `_emit` call" — declined 2026-08-14
