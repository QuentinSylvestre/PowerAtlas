# Codex as a Built-in Provider: Sessions, Live Dot and Overview

> **Date**: 2026-10-01
> **Status**: Exploring  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Add OpenAI Codex as a built-in provider at parity with Claude Code and kiro-cli: launch, discovery and transcripts, live dot and Resume state, Overview tiles and usage, plus measured Codex knowledge in `docs/KNOWLEDGE.md`.

---

## Intent

### Problem statement & desired outcomes

PowerAtlas shows and launches sessions for three providers: Claude Code (`claude-code`), kiro-cli v3 (`kiro-cli-v3`) and Kiro IDE (`kiro-ide`). OpenAI's Codex is installed on the user's machine and keeps its sessions under `~/.codex`, but PowerAtlas cannot list, read, launch or show the live state of them.

The outcome is a fourth built-in provider, id `codex`, display name "Codex", behaving like Claude Code and kiro-cli in the dashboard. Three tiers were chosen by the user (Q1, "iso kiro-cli and claude code, A+B+C"):

- **A.** Launch, discovery and transcripts: Codex in the filter chips, "New AI session" menu, launcher tile and provider settings; sessions in the workspace rail; full transcript view; New and Resume launch.
- **B.** Live dot and Resume state.
- **C.** Overview: live tiles and the usage section.

A documentation deliverable rides with it: improve the stored knowledge about Codex in `docs/KNOWLEDGE.md`, the way the Claude Code and kiro-cli findings are already stored there.

### Success criteria

- **SC1. Provider surfaces.** `codex` appears in `/api/available-providers` (so the filter chip, "New AI session" menu and launcher tile), with display name "Codex", default colour white (`#ffffff`), and the tinted terminal glyph as icon (`codex.exe` has no embedded icon, see Discovery item 4). A "White" swatch is added to the Card color swatch group in `launcher_modal.html`. A provider-settings entry for `codex` works (enabled, colour, default args, default directory).
- **SC2. Sessions in the rail.** Codex top-level sessions are listed per workspace. Sub-agent threads are excluded. Sessions that originate from the VS Code extension and the desktop app are included. Workspace identity goes through `data._normalize_path`, so the same folder written with an upper-case and a lower-case drive letter groups as one workspace. Title, first prompt, last prompt and reply tail are shown, taken from real user prompts, not from injected context.
- **SC3. Transcript.** The full-transcript view renders user and assistant text and tool calls for a Codex session, with tool success taken from the command exit code. A rollout of tens of MB opens without hanging the dashboard.
- **SC4. Launch and Resume.** New session and Resume run `codex` and `codex resume <id>` in a terminal in the workspace, with no baked flags (Q3). Per-provider `default_args` apply. Resume is shown as unavailable while the thread's writer lock is held.
- **SC5. Live dot (Claude rule, Q5).** A terminal `codex.exe` process marks a session live by id when `resume <id>` is on its argv, or by workspace plus a rollout change within 300 s. The daemon and helper `codex.exe` processes (`app-server`, `exec-server`, and similar subcommands) never mark anything live.
- **SC6. Overview.** Live tiles show Codex events from rollout tails. The usage section shows Codex agent time, per-day session counts and token totals with the cache-hit ratio. The three "any other provider falls through to the Claude or kiro parser" sites are replaced by explicit per-provider dispatch, and tests pin that an unknown provider is not parsed as another provider's format.
- **SC7. Parser hardening.** The Codex reader caches by (mtime, size) and never assumes a file only grows. It tolerates torn or partial lines, in-place rewrites (compaction), unknown record shapes and unknown `cli_version`, with bounded head and tail reads for lists, tails and status, and streamed reads for the transcript. Legacy rollouts without the `payload` wrapper and `archived_sessions/` are ignored without error.
- **SC8. Tests.** Existing test files are updated (`tests/test_data.py`, `tests/test_launcher.py`, `tests/test_web.py`, `tests/test_config.py`, `tests/acp_page.test.mjs` for the Resume gate). Adapter tests patch a module-level Codex sessions root so no unpatched test reads the developer's real `~/.codex`. `pytest` (with `--timeout=300`) and `node tests/acp_page.test.mjs` pass.
- **SC9. Docs.** README lists Codex (providers, features, config example, usage notes). `docs/KNOWLEDGE.md` gains a dated, measured Codex section: store layout, writer lock, process shape, record shapes, hardening evidence with upstream issue references, and the explicit statement that OpenAI publishes no stability guarantee for the rollout files.
- **SC10. Probe cleanup.** At the end of execution, the Codex probe artifacts listed in Discovery item 9 are removed.

### Scope boundaries & non-goals

- No driving of Codex from `/acp`. The ACP layer is kiro-cli only, and the roadmap already treats a second ACP provider as a driver-abstraction spike (`plans/ROADMAP.md`, "Spike: drive Claude Code over ACP through an adapter").
- No Working/Waiting/Errored verdict for Codex rows. Only held kiro-cli v3 sessions get one today.
- No notifications for terminal Codex sessions. Notifications are ACP-only today.
- No context-pressure metric for Codex (kiro-cli only), and no cost estimate.
- No SQLite accelerator, no `codex app-server` integration. Both are deferred with reopen triggers (Discovery item 6, Q4).
- No bundled Codex logo (Q6). No baked launch flags (Q3).
- Linux is best-effort: lock and process behaviour were measured on Windows only.

---

## Exploration Discovery

<!-- Transient: /qplan folds these into the planning sections and removes this section. -->

Step 1.5 dispatched the code-tracing trio (directed, subsystem, mutation-finder) because the in-scope files are predominantly Python and JS. Their reports agree on facts; the one correction between them is in item 5. Measurements marked "measured" were taken on 2026-10-01 with codex-cli 0.159.2 on Windows 11, on the user's own `~/.codex`. Message text, `auth.json`, `config.toml` and `secrets/` were never read. Line numbers for `index.html`, `style.css` and `launcher_modal.html` predate commit `4348d4c` and the in-flight edits described in item 5, so re-verify them.

### 4. Existing patterns & constraints

**Provider contract (code).**
- A provider is a string id. `data.PROVIDERS` (`src/power_atlas/data.py:176`) maps id to a module. The interface is duck-typed with eight functions: `is_available`, `discover_workspaces`, `load_sessions`, `refresh_stale_entries_for_cwd`, `find_session_workspace`, `get_session_tail`, `get_first_prompt`, `get_full_transcript` (call sites `data.py:185, 326, 347, 362, 390, 431, 439, 453`). The Claude implementations are `data_claude.py:56, 145, 190, 732, 714, 497, 563, 624`. A partial module raises `AttributeError` on some paths and is swallowed on others.
- "Installed provider" means its session data exists on disk, not that the binary is on PATH (`data.available_providers`, `data.py:183`). `/api/available-providers`, `/partials/launchers`, the "New AI session" menu and the filter chips all derive from it. A launcher-only provider would not appear.
- `Session.extra_fields` must stay the last field (`data.py:48`). `presence.Snapshot` constructor parameters must be appended at the end (`presence.py:386-391`).
- `_normalize_path` (`data.py:112-124`) casefolds and expands 8.3 names on Windows. Cache keys use the normalised cwd and the original spelling is kept separately.
- The Claude adapter is the model for bounded reads: a head scan of at most 500 lines and a 256 KB tail, parse cache keyed by (mtime, size) (`data_claude.py:263-474`). Its "JSONL is append-only" head cache assumption (`:293-297`) must not be copied (Codex rewrites rollouts).
- `data.get_session_tail` and `data.get_first_prompt` have no callers in `src/` (grep), so the Codex module must provide them but they are not on a live path.

**Layers keyed by provider id (what a new id does).**
- Launch: `launcher.py:81-97` has three dicts (display, binary, terminal). `launcher.py:100-117` `_build_provider_args` raises `ValueError` for an unknown id, so launch fails with a toast rather than starting by accident. The id-to-binary lookup falls back to the id itself, so `codex` resolves its binary without a table entry. Id regexes already accept Codex's UUID ids (`data.SESSION_ID_RE`, `launcher._SESSION_ID_RE`, measured).
- Web: `web.py:94-113` has colour, display name, badge (dead, no readers) and binary-display dicts, with grey `#888` and raw-id fallbacks (`web.py:147-150`). `/api/launch`, `/api/launch-batch`, `/api/new-session` and `/api/session-transcript` default `provider` to `kiro-cli-v3` when omitted (`web.py:5699, 5768, 5807, 5834`). `POST /api/provider/save` accepts any key without checking `PROVIDERS` (`web.py:5025-5064`). `_acp_availability` returns `available` for any id not starting with `sess_` (`web.py:2423-2471`), so Codex rows are always "available" there. That is harmless because Codex's Resume state comes from the lock.
- Front end (`index.html`): `DASH_OV_PROVIDERS` (~1441, tile icon), `DASH_OV_USAGE_PROVIDERS` (~1725-1731, fixed three-entry array that silently drops any other provider from the usage bars, legend and tooltip), the Resume button id whitelist (~6588-6589, a Codex row would get no Resume button), `_providerBinaryDisplay` and `_providerTerminal` (~8126-8127, must mirror the Python dicts; missing the JS map was a shipped bug for Kiro IDE, project memory). Delete and live attach are kiro-cli-v3-only and stay so (~6611, ~3114). `style.css` has `.dash-ov-bar-seg.is-claude/is-kiro-cli/is-kiro-ide` and legend rules (~1362-1364); a new class is needed.
- Presence: `presence.py:66-69` `_PROVIDER_SPECS` covers only `claude-code` and `kiro-cli-v3` (binary names plus an id flag). `_match_provider` (`:526-545`) matches process name or argv0 basename and rejects argv with a `--type=` switch. `_extract_session_id` (`:514-523`) is position-agnostic: it takes the token after the flag, so flag `resume` matches `codex resume <id>`. A provider absent from the specs is never live.
- Status: `status_classifier.py:93-122` resolves a transcript path per provider (None for unknown). `_classify_from_path` (`:497-501`) returns None for unknown. `web._session_is_live` (`web.py:243-271`) is `snapshot.is_live` or (a provider process in the cwd and the transcript mtime within 300 s); the second branch needs a transcript-path resolver for the provider.
- Overview: `overview.py:573-575` `_line_events` sends `claude-code` to the Claude parser and every other provider to the kiro v3 parser. `:688-701` `_transcript_cwd` treats every non-kiro provider as Claude. `:1343-1345` `_parse_usage_file` treats every non-kiro provider as Claude. `_usage_roots()` (`:1085-1093`) returns a 3-tuple unpacked at `:1380` and indexed at `:1439`, and patched with 3-tuples at `tests/test_web.py:113-114, 31206-31228`; changing its arity breaks those fixtures, so add a separate Codex seam instead. `_usage_files` (`:1372-1399`) globs only kiro, Claude and Claude sub-agent files. `_USAGE_SCHEMA = 1` (`:1076`) must be bumped when a parser's output shape changes. Held tiles are hard-coded to kiro-cli v3 (`:419`).
- Icons: `/api/launcher-icon/provider--<id>` (`web.py:6004-6031`) extracts the icon embedded in the provider binary through `icons.extract_icon` and caches `provider--<id>.png`; on failure it serves the 24x24 outline terminal SVG (`icons.py:43-47`) with its stroke set to the provider colour. Resolved colour: user setting in provider settings, else `PROVIDER_COLORS`, else `#888`.
- Colour list: the "Card color" swatch group (`launcher_modal.html` ~61-75, 13 entries including "No color") is used by the launcher modal that provider settings reuse (`openProviderLauncherModal`, `index.html` ~8370). There is no white swatch. A 14th swatch fits the 7-column grid (`style.css` ~816) as two even rows; the comment at `style.css` ~813 needs updating. The UI is dark-only (single `:root`), so a white glyph is visible. The tag palette (`index.html` ~8034) and the workspace colour picker are separate lists and stay unchanged.
- Notifications (`notifications.py`, `web._notify_from_acp`) fire only from ACP events (kiro-cli v3). Config has no provider schema: `provider_settings` is an open dict (`config.py:101`, stale-key drop at `:911-913`).
- Tests that exist: provider adapter tests live inside `tests/test_data.py` (Claude `TestClaude*`, Kiro IDE `TestKiroIde*`) and kiro-cli v3 has its own file; classifier and Overview tests live inside `tests/test_web.py` (`TestClassifyClaude`, `TestOverviewLive`, `TestOverviewUsage`). Nothing enumerates `PROVIDERS` by equality, so adding an id breaks no test by itself. `tests/acp_page.test.mjs` (~18799) already loops over provider strings including `"codex"` as an unknown-provider canary, and keeps passing for a real provider.
- Project rules (`AGENTS.md`): Python changes need a PowerAtlas restart and an agent must not restart without a per-task grant; template, CSS and static JS changes need only a hard reload; `tests/acp_page.test.mjs` is not part of pytest or CI; no new test files unless requested; update README for user-visible surface; use `--timeout=300` for the full pytest run; in a git worktree set `PYTHONPATH=src`; the pre-commit hook `_check_test_names.py` rejects duplicate test or fixture names.
- Precedents: `plans/done/260706-1653_KIRO_IDE_PROVIDER.md` and `plans/done/260701-2250_PROVIDER_LAUNCHER_UNIFICATION.md`. Recorded lessons: the browser-side maps must mirror the Python dicts; `.cmd` shims need `shell=True` for non-terminal providers; `_group_workspaces` must dedupe and sort providers deterministically; the README config example must list the new provider. `plans/done/260818-2227_KIRO_CLI_V3_DASHBOARD_SUPPORT.md` is the precedent for an adapter with its own test file, which AGENTS.md otherwise forbids.

**Codex facts (measured 2026-10-01, codex-cli 0.159.2, Windows 11).**
- Binaries: `shutil.which("codex")` resolves to a native `codex.exe` (0.159.2). A second install, an npm shim, reports 0.124.0 and is shadowed by PATH order. `codex.exe` has 0 embedded icon resources (as `kiro-cli.exe`; `claude.exe` has 2).
- Store: `~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl`, one thread per file. 613 files, about 575 MB, largest about 28 MB. Each parseable file starts with a `session_meta` record (`ordinal`, `timestamp`, `type`, `payload`) whose payload carries `id`, `cwd`, `source`, `originator`, `cli_version`, `model_provider`, `parent_thread_id`, `history_mode` and a large `base_instructions` (first line p50 about 17 KB, max about 42 KB).
- Population: 93 top-level sessions (source `vscode` 69: 65 from the VS Code extension, 4 from the desktop app; source `cli` 24) and 509 sub-agent threads (`source` is an object with a `subagent` key; 244 are `guardian` safety reviewers). A simple `source == "subagent"` comparison is wrong because the value is an object. 11 oldest files (2025-08/09) lack the `payload` wrapper and are listed by Codex itself as skipped in its rollout migration. 40 threads are archived under `archived_sessions/`, outside `sessions/`.
- Catalogue: `state_5.sqlite` table `threads` (id, rollout_path, created_at, updated_at, source, model_provider, cwd, title, archived, tokens_used, ...) agrees exactly with the files on the top-level set (93 = 93). `thread_history_1.sqlite` is a projection of the rollouts (it stores a byte offset into each file), not a source of truth. `has_user_event` was 0 on all 93 rows, so not every column is trustworthy. `session_index.jsonl` holds `id`, `thread_name`, `updated_at` (83 lines for 81 ids, so last entry wins).
- cwd forms: plain backslash drive paths only (no `\\?\`). The VS Code extension writes a lower-case drive letter and the CLI an upper-case one. `_normalize_path` collapses 22 raw cwds into 18 workspaces.
- Cost of the file-first discovery: reading the first line of all 613 files took about 0.8 s cold; the SQLite query took about 42 ms.
- Records: top-level `type` in `session_meta`, `response_item`, `event_msg`, `turn_context`, `token_usage_record`, `world_state`, `inter_agent_communication_metadata`. `response_item` payload types include `message` (role, `input_text` content), `reasoning` (encrypted), `function_call` (`name`, `arguments`), `function_call_output`, `custom_tool_call` (`name` such as `exec`, `input`), `custom_tool_call_output`, `compaction`, `agent_message`. `event_msg` types include `task_started`, `task_complete` (`duration_ms`, `started_at`, `completed_at`), `token_count`, `item_completed`, `thread_settings_applied`. `item_completed` items include `UserMessage`, `AgentMessage`, `CommandExecution` (`command`, `cwd`, `exit_code`, `status`, `stdout`, `stderr`, `duration`), `Reasoning`, `SubAgentActivity`. `token_usage_record.usage` has `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens`, `total_tokens`. Tool names are version-dependent.
- The first user-role `response_item` message in every sampled session is injected context (a `# AGENTS.md instructions ...` block of about 44 KB). Real prompts come as later user messages and as `UserMessage` items.
- Live session shape (live probes): an interactive TUI is its own `codex.exe` process whose cwd is the workspace. A new session has no id on argv. `codex resume <id>` carries the id right after the `resume` token. A bare `codex` with no prompt wrote no rollout and no lock within 30 s. The rollout appeared about 15 s after launch, with the first prompt. It was readable while Codex held it open. Its Windows mtime stayed frozen while its size grew from about 23 KB to 166 KB during a turn, so (mtime, size) must both be keyed. Every `codex.exe` already running on the machine before the probes was infrastructure (an `app-server` daemon, an `exec-server`, a `daemon pid-update-loop`), not a session. `codex --help` lists no `acp` command; the programmatic surface is `codex app-server` (stdio, WebSocket, Unix socket).
- Writer lock: `~/.codex/thread-writer-locks/<thread-id>.lock`, a 0-byte file. A live session holds a byte lock on it: a non-blocking lock attempt from another process failed while the session lived and succeeded once the owner was dead (stale file left by a kill). Clean exits remove the file (only `.coordination.lock` remained after 600+ past sessions). A TUI also creates a second lock for a helper thread that has no rollout. Resuming a thread whose lock is held fails in Codex itself ("thread already has an active writer").
- Upstream evidence (web, no maintainer guarantees): issue #45251 asks which rollout behaviours tooling may rely on and has no maintainer answer ("nothing in the current docs states what a downstream consumer may rely on"). The rollout is the canonical store and the SQLite projection is derived and has had desync bugs (#41079, #38792, #40342). Compaction has rewritten rollouts in place (#45350). An upgrade corrupted a rollout (#45150). Logs reach 0.7 to 2 GB (#24948). The desktop app keeps the writer lock for every thread opened in that run until the app exits (#37450, Windows), a TUI orphaned by an SSH disconnect keeps it (#44063), and the panel holds it (#45406). The documented stable interface is the app-server's `thread/list` and `thread/read`; `thread/list` has a `useStateDbOnly` option that skips JSONL scanning. All issues are in `openai/codex`. `history_mode: paginated` appears from about 0.146.

### 5. Risks & mitigations

- **Silent wrong-parser fallbacks (high).** `overview.py:573-575`, `:688-701`, `:1343-1345` would parse a Codex file as kiro or Claude without any error. Mitigation: replace each with explicit dispatch, with a test that an unknown provider yields nothing.
- **Daemon processes mistaken for sessions (high).** A name-only match on `codex.exe` would mark the daemons live. Mitigation: match TUI processes only (exclude `app-server`, `exec-server`, `exec`, `daemon`, and other helper subcommands by argv), and test it against the process shapes measured here.
- **Stale mtime (high).** The status cache and `_resolved` guards key on mtime (`status_classifier.py:23, 402-415`); a held-open Codex rollout does not update it. Mitigation: key the Codex caches on (mtime_ns, size).
- **No stability guarantee, in-place rewrites, torn lines, huge files (high).** Mitigation: SC7; record the evidence in `docs/KNOWLEDGE.md`; a version-drift test fixture with an unknown record shape.
- **Test isolation (medium).** A Codex adapter reading the real home leaks the developer's `~/.codex` into unpatched tests. Mitigation: a module-level root constant patched in fixtures, as `CLAUDE_PROJECTS_DIR` is.
- **`_usage_roots` arity (medium).** See item 4; add a separate seam.
- **Concurrent edits (medium).** Another session committed `4348d4c` ("Polish launcher settings modal") after this exploration started and had about 400 uncommitted lines in `launcher_modal.html`, `workspace_settings_modal.html`, `index.html` and `style.css`, the files the White swatch, the JS maps, the Resume gate and the usage chart touch. Mitigation: sequence the front-end phases after that work lands, re-read those files before editing, and stage only this plan's own files.
- **Idle terminal session older than 300 s shows no dot (accepted).** This is the same known gap Claude has; the lock-assisted refinement was dropped after a break test (Q5).
- **Resume on VS Code or desktop-origin sessions (unverified).** `codex resume --help` documents `--include-non-interactive` for non-interactive sessions only, so these sessions are interactive and should resume, but this has not been exercised.
- **Falsified hypotheses (do not re-derive).** (1) "Match `codex.exe` by name plus a resume flag" fails: new sessions have no id on argv and the daemons carry the name. (2) "mtime guards work" fails on Windows for a held-open rollout. (3) "Lock held plus a terminal process in the same cwd means live" fails when a desktop-held idle thread shares a folder with a terminal session. (4) My first top-level count (about 100, then 602) was wrong because `source` is an object for sub-agent threads; the correct count is 93.
- **Report correction.** One mutation-finder claim that toasts fire on Working-to-Waiting transitions is wrong: notifications fire only from ACP events (`acp.py:4863, 5436, 7046`, `web.py:429-464`).

### 6. Resolved decisions

- Q1: Which tier does "built-in provider" cover? — A: "iso kiro-cli and claude code. A+B+C. This is a good opportunity to improve the stored knowledge about codex, like we did for claude code and kiro." — Decision: Tiers A, B and C at parity with Claude Code and kiro-cli, plus a Codex section in `docs/KNOWLEDGE.md`. `/acp` driving stays out of scope (D).
- Q2: May a throwaway interactive Codex session be started to measure live behaviour? — A: "yes you can. cleanup if you can when you're done. cleanup can be done at the end of the plan execution" — Decision: probes run (three launches from a scratch folder, `-c` trust override, one trivial prompt, descendants of the probe killed only). Cleanup is deferred to the end of plan execution (SC10, item 9).
- Q3: What launch flags should the Codex launcher bake in? — A: "A" (none) — Decision: launch plain `codex` and `codex resume <id>`; the user's own `config.toml` governs; flags can be added per provider through Provider Settings default args.
- Q4: Where should discovery read Codex sessions from? — A: "ok" after the revisit that followed an online check — Decision: option A, rollout JSONL as source of truth (first-line `session_meta`, cached by (mtime, size), sub-agent threads rejected and cached as rejects, title from `session_index.jsonl` `thread_name` else first real prompt), with the five hardening rules in SC7. SQLite as an accelerator and the app-server are deferred.
- Q5: What should the live dot mean for a Codex session? — A: "same as claude code" — Decision: the Claude rule (SC5). The lock-assisted refinement I proposed afterwards was dropped by my own break test, not by the user. The writer lock is used only for the Resume state.
- Q6: Provider colour and icon (raised by the user's question about where the Claude Code icon comes from, then "let's pick white as default color (add if not in the list of colors)") — A: white — Decision: `PROVIDER_COLORS["codex"] = "#ffffff"`; add a "White" swatch to the Card color list; the icon is the tinted terminal glyph (no bundled logo).
- Q7: Where should the project file live? — A: "in-workspace plan (you shouldn't have to ask here, note for a future qdream session)" — Decision: `plans/` in the workspace; the friction is recorded under Harness Improvement Opportunities.

### 7. Open items (execution-contingent)

- Does the VS Code extension or the desktop app hold the same writer lock, and for how long? Does `codex resume` work on a VS Code or desktop-origin session? Verify in QA on a safe session.
- Lock and process behaviour on Linux (locking primitive, `psutil` cwd). Windows is verified; Linux is best-effort.
- Do summed sub-agent rollouts double-count tokens and time against their parent's totals? Verify against one parent and its children before including sub-agent rollouts in usage.
- Do `UserMessage` items exclude all injected context, so first-prompt extraction is clean? Verify on a handful of sessions from each origin.
- Which Codex tool names exist across versions (the sampled sessions used `exec` and the multi-agent tools). The translator falls back to kind `other`, so this only affects icons and titles.
- Whether a version-gated `cli_version` check is needed or tolerant parsing suffices.

### 8. Recommended approach

Four phases in order, each leaving the app working.

1. **Phase 1: Tier A.** New `data_codex.py` registered in `data.PROVIDERS` with the eight-function contract, a patchable sessions root, and the SC7 reader. Launcher entries (`launcher.py` three dicts plus `_build_provider_args`: `codex`, `codex resume <id>`, terminal provider). Web entries (`web.py` colour, display name, binary display). Front-end entries (Resume whitelist, JS maps, `DASH_OV_PROVIDERS`). The White swatch. Tests in existing files. README.
2. **Phase 2: Tier B.** `presence.py` spec for `codex` (TUI-only matching, flag `resume`) with the helper-subcommand exclusion; `status_classifier._resolve_jsonl_path` branch for the 300 s rule; a writer-lock probe for the Resume state, surfaced through the existing availability path so Resume is disabled while the lock is held. Tests including daemon-shaped argv fixtures.
3. **Phase 3: Tier C.** Explicit dispatch in the three Overview sites; Codex event parser for tiles; usage parser (agent time from `task_complete.duration_ms`, tokens from `token_usage_record.usage`, per-day sessions, sub-agent rollouts included once item 7's double-count check passes); a separate Codex root seam; `_USAGE_SCHEMA` bump; `DASH_OV_USAGE_PROVIDERS` entry and CSS class; tests.
4. **Phase 4: Docs and cleanup.** `docs/KNOWLEDGE.md` Codex section, README usage notes, ROADMAP touch if an entry is affected, probe cleanup (item 9).

Phases 1 to 3 change Python, so each live check needs a PowerAtlas restart under the user's grant. Front-end phases are sequenced after the in-flight launcher-modal work (item 5).

### 9. QA environment

- PowerAtlas serves on loopback; every page and API needs the `pa_local` cookie (see `AGENTS.md`, "Every loopback page and API needs the `pa_local` cookie"). Drive pages with standalone Playwright from the venv, one script per call. A restart needs the user's grant per task.
- Codex is installed locally (codex-cli 0.159.2) with 93 real top-level sessions to browse read-only. Real sessions must not be resumed or mutated in QA without the user's consent.
- Live-state QA recipe (used in this exploration, safe to repeat): from a scratch folder, launch `codex -c 'projects."<scratch path with doubled backslashes>".trust_level="trusted"' "<trivial prompt that needs no tools>"` in a new console, sample the process table (argv, cwd), the new rollout (size and mtime) and `thread-writer-locks/` names every 0.4 s with a Python script, then terminate only descendants of the launched process. A bare `codex` (no prompt) and `codex resume <id>` cover the other shapes. Test lock ownership with a non-blocking byte lock on the lock file.
- Never read `auth.json`, `config.toml`, `secrets/` or message text in QA output; print types, key names and counts.
- Verification: `PYTHONPATH=src .venv-PowerAtlas/Scripts/python -m pytest tests --timeout=300`, `node tests/acp_page.test.mjs`, `.venv-PowerAtlas/Scripts/python _check_test_names.py`. The page test cannot see CSS, so check the White swatch, the glyph colour and the usage bar in a real browser.

**Probe cleanup ledger (item for SC10).** Remove the Codex probe session `01a0f93d-619b-71f3-8933-fc4935797f8e` with `codex delete`; remove the three stale 0-byte lock files left by killing the probe processes, whose names begin `01a0f93d-619b`, `01a0f93d-642c` and `01a0f93e-8ffb`; remove the scratch probe folder and the probe scripts from the session scratchpad. Show the id and files to the user before deleting.

### Assumptions (unconfirmed)

Resolved by the model, not by a user answer. The user can veto any of these before `/qplan`.

- Provider id `codex`, display name "Codex" (not `codex-cli`), because the provider covers CLI, VS Code and desktop sessions in one store. (Naming.)
- Sessions from the VS Code extension and the desktop app are included. Sub-agent threads are excluded from lists and included in usage, subject to the double-count check. (Session set.)
- Real prompts come from `UserMessage` items, not from the first user-role message. Title is `session_index.jsonl` `thread_name` (last entry wins), else the first real prompt truncated. (Titles.)
- Legacy rollouts without the `payload` wrapper and `archived_sessions/` are ignored. (Scope of reader.)
- Tool success comes from `CommandExecution.exit_code`; unknown tool names map to kind `other`. (Transcript.)
- Windows verified, Linux best-effort. (Platforms.)
- The launcher uses the first `codex` on PATH; the older npm shim is shadowed. (Launch.)
- Tests go into existing files with a patchable sessions root; no new test file. README and `docs/KNOWLEDGE.md` are the documentation. (Test and doc policy, per `AGENTS.md`.)
- Deferred, with the condition that reopens each: a lock-owner lookup for a sharper live dot (idle-session or desktop-noise complaints); a SQLite accelerator for discovery (cold scan grows past about 2 s); app-server integration (Codex publishes an on-disk contract or changes the format).

---

## Harness Improvement Opportunities

- `/qexplore` Step 3 "Store confirmation" asked where to write the project file although the workspace already has an established `plans/` convention (57 archived plans and a ROADMAP, no workspace-store entry) — cost: one extra interruption round-trip in a long session (the user said "you shouldn't have to ask here, note for a future qdream session") — suggested change: skip the prompt and write to the workspace's `plans/` when that folder already holds archived project files and no private store entry exists for the workspace; ask only when no convention is detectable. Default/override pair: (ask for in-workspace vs private store when resolution returns `plans/`, in-workspace `plans/`).
- `/qexplore` Probe gate has no guidance for probes that start a live external CLI process (consent text, isolating the run, killing only descendants, listing cleanup items) — cost: the recipe was improvised and the user had to approve it blind — suggested change: add a short worked example to the Probe gate: name the side effects, run from a scratch folder, record cleanup items in the project file before execution.
- Sub-agent reports contained claims that the orchestrator's own probes contradicted (a claimed toast path that does not exist; an incorrect top-level count that I produced myself from a wrong `source` comparison) — cost: two correction rounds in chat — suggested change: in the Probe gate, require a sanity cross-check of any derived count against a second source (here, files versus the SQLite catalogue) before the number is shown to the user.
