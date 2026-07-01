# Multi-Provider Tabs and Launch

> **Date**: 2026-07-01
> **Status**: In Progress  <!-- Status lifecycle: Exploring → Draft → In Progress → Complete -->
> **Scope**: Add Claude Code as a second provider alongside kiro-cli (tabbed UI, session discovery, contextual launch), plus selection-aware custom launchers for multi-app workspace launching.
> **Estimated effort**: 3-5 days

---

## Intent

### Problem statement & desired outcomes

PowerAtlas currently only discovers and launches kiro-cli sessions. Users also work with Claude Code and want unified visibility across both tools. Additionally, launching multiple apps (kiro-cli, Kiro IDE, Claude Code) in selected workspaces requires manual repetition. This plan adds multi-provider session discovery with a tabbed UI, and enables custom launchers to fire in multi-selected workspaces.

### Success criteria

1. **SC1 — Claude Code session discovery**: Sessions from `~/.claude/projects/` are parsed with iso-parity to kiro-cli (session_id, title from `ai-title`, cwd, timestamps, first/last prompt, last reply tail).
2. **SC2 — Provider tabs**: "All" | "kiro-cli" | "Claude Code" tabs in the workspace section. Tabs auto-show/hide based on available data. Pinned sessions/workspaces remain visible above tabs regardless of selection.
3. **SC3 — Provider-filtered views**: Each provider tab shows only workspaces with sessions for that provider. "All" tab shows all workspaces interleaved by recency, with colored left-border per provider (kiro purple, claude orange). Same workspace appears as separate cards per provider.
4. **SC4 — Contextual launch**: "Launch selected" button dispatches per-card using each card's native provider command. kiro-cli tab → `kiro-cli chat`, Claude Code tab → `claude`, All tab → mixed dispatch per card.
5. **SC5 — Claude Code resume**: Resume launches `claude --resume <session-id>` with cwd set to the decoded workspace path. Launch failures surface clearly to the user.
6. **SC6 — Selection-aware custom launchers**: Launchers gain a "Use selected workspaces" toggle. When on + workspaces selected: fires once per selected workspace. Badge shows selection count on the tile. Empty cwd defaults to `~`.
7. **SC7 — Provider settings modal**: Gear icon on tabs opens a modal with: name (read-only), color (editable), default args (text), enabled toggle. Accessible from settings page too.
8. **SC8 — Trust-all generalization**: Remove dedicated "Trust all" toggle from topbar. Replace with per-provider default args field (e.g., `-a` for kiro-cli).
9. **SC9 — Claude Code memory-source doc**: Document Claude Code's local data format in agent-playbook (parallel to `kiro-cli-local-data.md`).

### Scope boundaries & non-goals

**In scope**: Claude Code provider adapter (discovery + parsing), tab UI with auto-show/hide, provider-colored workspace cards, contextual batch launch, selection-aware custom launchers with toggle and badge, provider settings modal, trust-all removal/generalization, Claude Code memory-source documentation.

**Non-goals**: Kiro IDE session discovery (action-only launcher, no data source). Live/streaming session updates. kiro-cli v3 session support (separate roadmap item). User-configurable provider registry/plugins. Claude Code `--dangerously-skip-permissions` as a first-class toggle. WebSocket/real-time tail for Claude Code sessions.

---

## Discovery

### Existing patterns & constraints

- `data.py:12` — `SESSION_DIR` hardcoded to `~/.kiro/sessions/cli/`. No provider abstraction.
- `data.py:45-78` — `SessionCache` keyed by normalized cwd alone. Two providers sharing a workspace would collide.
- `data.py:232-250` — `_extract_content()` handles kiro-cli v2 format only (`kind` field discrimination).
- `web.py:167-224` — `partials_workspaces()` renders flat sections (Pinned sessions → Pinned workspaces → All workspaces). No tab infrastructure.
- `web.py:215-217` — "All workspaces" section label is the natural tab insertion point.
- `style.css` — `.emoji-tabs`/`.emoji-tab` classes provide an existing tab styling pattern.
- `launcher.py:96` — `launch_session()` hardcodes `kiro_args = ["kiro-cli", "chat"]`.
- `launcher.py:184-215` — `launch_custom()` already supports arbitrary commands with terminal/no-terminal mode.
- `config.py:23-31` — `Config` dataclass has `trust_all_tools: bool` and `custom_launchers: list[dict]`.
- Claude Code stores sessions at `~/.claude/projects/<encoded-path>/<session-id>.jsonl` with tree-structured messages.
- Claude Code project folder naming: path with non-alphanumeric chars replaced by `-` (e.g., `C--Users-QSylvestre-POLESTAR-OneDrive---Pole-Star-Documents-Dev-Perso-agent-playbook`).
- Claude Code session title stored as `{"type": "ai-title", "aiTitle": "...", "sessionId": "..."}` in the `.jsonl`.
- Claude Code user messages: `{"type": "user", "message": {"role": "user", "content": "..."}, "parentUuid": "..."}`.
- Claude Code `--resume <session-id>` must be run from the project directory (scoped lookup).
- `history.jsonl` at `~/.claude/history.jsonl` has per-prompt entries with `display`, `timestamp`, `project`, `sessionId`.
- Project MEMORY.md: cache getters must return copies (not references).
- AGENTS.md: update existing docs when user-visible changes; update tests when implementation changes.

### Risks & mitigations

- **Claude Code format instability**: Docs explicitly state the `.jsonl` format is internal and may change. Mitigation: isolate parsing in `data_claude.py` adapter; format changes only affect one file. Accept the risk.
- **Path decoding edge cases**: `C--Users-...` folder names use `-` as separator, but paths with consecutive hyphens or special characters may decode ambiguously. Mitigation: surface decode failures clearly in UI (error badge on card); test with real project folders on disk.
- **SessionCache key collision**: Two providers sharing a workspace would collide with cwd-only key. Mitigation: compound key `(provider, normalized_cwd)` in unified cache.
- **Trust-all removal UX regression**: Existing users lose the toggle. Mitigation: provider default args field replaces it; documented migration path.
- **Claude Code `.jsonl` files can be multi-MB**: Largest observed is 3MB. Mitigation: use same tail-reading strategy as kiro-cli (seek to end, read last N bytes, parse backward).
- **Tab switching latency**: Mitigation: data pre-warmed at startup for all detected providers; tab switch serves from cache, not disk.

### Resolved decisions

- Q1: Claude Code session parsing approach — A: full parsing, iso-parity with kiro-cli — Decision: Parse `.jsonl` for title (ai-title), prompts (type:user), replies (type:assistant), timestamps.
- Q2: Claude Code resume mechanism — A: `claude --resume <session-id>` in project cwd (per official docs) — Decision: Per-session resume supported; cwd derived from folder name.
- Q3: Tab behavior for workspaces with sessions in both providers — A: Provider-filtered views; each tab shows only workspaces with sessions for that provider — Decision: Option A (provider-filtered).
- Q4: Multi-app launch UX — A: Current launch button stays contextual to active tab — Decision: Contextual launch button; custom launchers for other apps.
- Q5: Custom launchers and workspace selection — A: Implicit (selection overrides cwd) + per-launcher opt-in toggle — Decision: Combined A+C model.
- Q6: Selection-aware launcher details — A: empty cwd defaults to ~; badge shows count ("3"); args uniform across workspaces; edited in modal — Decision: As stated.
- Q7: Tab auto-show/hide detection — A: ok — Decision: Hide tab when no session data exists; disappears on refresh if data removed; binary detection for launch button enable/disable.
- Q8: Claude Code title source — A: `ai-title` entry in `.jsonl`, fallback to first user message — Decision: Primary title from `{"type":"ai-title","aiTitle":"..."}`.
- Q9: Data architecture — A: Provider adapter pattern (separate modules) — Decision: `data_kiro.py`, `data_claude.py` implement discovery/loading; `data.py` orchestrates.
- Q10: Cache architecture — A: Unified cache with compound key `(provider, cwd)` — Decision: Single `SessionCache` in `data.py`.
- Q11: Tab rendering approach — A: Server-side `?provider=` param + pinned items always above tabs — Decision: htmx request per tab switch; pinned unfiltered above.
- Q12: Provider/launcher coupling — A: Providers are implicit (detected from data), color hardcoded with override — Decision: No explicit provider config in TOML; detected at runtime.
- Q13: Contextual launch in All tab — A: Dispatch per-card using each card's provider — Decision: Mixed dispatch in All tab.
- Q14: Trust-all handling for Claude Code — A: No trust-all equivalent initially; generalize to per-provider default args — Decision: Remove toggle, add default args field.
- Q15: Provider args location — A: Settings page with provider section, accessed via modal — Decision: Provider modal (gear icon on tab + settings page).
- Q16: Provider modal trigger — A: Gear icon on tab bar + accessible from settings — Decision: Both access paths, modal with name/color/args/enabled fields.
- Q17: Provider taxonomy — A: Closed set requiring custom dev per new provider — Decision: Hardcoded providers, adapter pattern for future extension.
- Q18: All-tab sort order — A: Interleaved by recency — Decision: All cards sorted by `updated_at` regardless of provider.

### Open items

- Exact hex values for kiro purple and claude orange (implementation detail — can reference brand colors or pick from existing CSS vars).
- Claude Code memory-source doc content (to be written based on findings from this exploration — format documented in Discovery section above).

### Recommended approach

Implement in phases ordered by dependency:

1. **Provider adapter infrastructure**: Create `data_kiro.py` (extract existing kiro-cli logic from `data.py`), `data_claude.py` (new Claude Code parser), refactor `data.py` to orchestrator with compound-keyed cache.
2. **Claude Code session parsing**: Implement discovery (scan `~/.claude/projects/`), path decoding, session loading (parse `.jsonl` for ai-title, user/assistant messages, timestamps).
3. **Tab UI**: Add tab bar to workspace section, `?provider=` param on `/partials/workspaces`, "All" tab with interleaved cards, colored left-borders, auto-show/hide logic.
4. **Contextual launch**: Extend `launch_session`/`launch_batch` to accept provider param, build provider-specific commands (`claude --resume` vs `kiro-cli chat --resume-id`), clear error messages on failure.
5. **Selection-aware custom launchers**: Add `use_selected_workspaces` toggle to launcher schema, badge rendering, multi-workspace dispatch logic.
6. **Provider settings**: Modal UI (gear on tab + settings page), per-provider default args and color override, remove `trust_all_tools` toggle.
7. **Claude Code memory-source doc**: Write `claude-code-local-data.md` in agent-playbook steering.


## 1) Current State

**Data layer** (`src/power_atlas/data.py`):
- `SESSION_DIR = Path.home() / ".kiro" / "sessions" / "cli"` (line 12) — hardcoded single provider.
- `SessionCache` (lines 45-78) keyed by `_normalize_path(cwd)` — no provider dimension.
- `discover_workspaces_with_counts()` (lines 112-147) scans only `SESSION_DIR` + sqlite.
- `_load_sessions()` (lines 158-198) reads kiro-cli `.json` metadata + `.jsonl` content format.
- `_extract_content()` (lines 232-250) parses kiro-cli v2 `kind` field (`Prompt` / `AssistantMessage`).
- `Session` dataclass (lines 28-36): shared shape works for both providers.

**Web layer** (`src/power_atlas/web.py`):
- `partials_workspaces()` (lines 167-224) renders flat sections with `<div class="section-label">`.
- No tab infrastructure; workspace data requested once with no provider filter.
- `api_launch()` / `api_launch_batch()` delegate to `launcher.launch_session()` unconditionally.

**Launcher** (`src/power_atlas/launcher.py`):
- `launch_session()` (line 96) hardcodes `kiro_args = ["kiro-cli", "chat"]`.
- `launch_custom()` (lines 184-215) already handles arbitrary commands with `use_terminal` flag.
- `launch_batch()` (lines 113-128) iterates and calls `launch_session()` for each entry.

**Config** (`src/power_atlas/config.py`):
- `Config` dataclass (lines 23-31): `trust_all_tools: bool`, `custom_launchers: list[dict]`.
- No provider-level configuration.

**Templates** (`src/power_atlas/templates/`):
- `index.html`: topbar has trust-all toggle, no tabs in workspace section.
- `partials/workspace_card.html`: no `data-provider` attribute.
- `partials/launcher_tile.html`: no selection-awareness.

**Claude Code local data** (`~/.claude/`):
- `projects/<encoded-path>/<session-id>.jsonl` — session content (tree-structured messages).
- `history.jsonl` — global command history with `display`, `timestamp`, `project`, `sessionId`.
- Folder names encode paths: non-alphanumeric chars → `-` (e.g., `C--Users-QSylvestre-POLESTAR`).
- Titles stored as `{"type": "ai-title", "aiTitle": "...", "sessionId": "..."}`.
- User messages: `{"type": "user", "message": {"role": "user", "content": "..."}, ...}`.

## 2) Goal

Introduce a provider adapter architecture that discovers sessions from both kiro-cli and Claude Code, presents them in a tabbed UI with provider-colored cards, and launches the correct CLI tool contextually. Extend custom launchers to support selection-aware multi-workspace dispatch.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Data architecture | Provider adapter pattern (`data_kiro.py`, `data_claude.py`, `data.py` orchestrator) | Single module with branching; single flat module | Isolates format-specific parsing; Claude Code format is internal/unstable — containment |
| Cache key | Compound `(provider, normalized_cwd)` in unified `SessionCache` | Separate caches per provider | Single refresh/warmup infrastructure; no duplication of staleness logic |
| Tab rendering | Server-side `?provider=` param, htmx request per tab switch | Client-side show/hide with data attributes | Matches existing htmx lazy-load pattern; smaller per-request payload |
| Provider detection | Implicit from data presence (no config) | Explicit provider registration in config | Zero-config; auto-show/hide tabs based on reality |
| Provider colors | kiro purple `#6c8cff`, claude orange `#f97316` (from existing CSS vars/swatches) | User-picks from scratch | Familiar brand association; overridable in provider modal |
| Trust-all replacement | Per-provider `default_args` text field | Keep dedicated toggle alongside args | Generalizes cleanly; no provider-specific boolean fields |
| Claude Code resume | `claude --resume <session-id>` with cwd = decoded workspace path | `claude --continue` (most-recent only) | Per-session resume matches kiro-cli's `--resume-id` parity |
| Selection-aware launchers | Per-launcher `use_selected_workspaces` toggle, implicit when on + selection exists | Always implicit; separate action-bar dropdown | Opt-in avoids surprising existing users; no extra UI surface |
| All-tab sort | Interleaved by `updated_at` descending | Grouped by workspace path | Simpler; colored borders provide provider discrimination |

## 4) External Dependencies & Costs

### Required external changes

None. All changes are local code + config. No CI/CD, IAM, cloud resources, DNS, or third-party services involved.

### Cost impact

None. No recurring cost changes.

## 5) Implementation Phases

### Phase 1: Provider adapter infrastructure and Claude Code parsing [QA] [P:2]

**Goal**: Extract kiro-cli logic into `data_kiro.py`, create `data_claude.py` for Claude Code session discovery/parsing, refactor `data.py` into a provider-aware orchestrator with compound-keyed cache.

**File scope**: `src/power_atlas/data.py`, `src/power_atlas/data_kiro.py` (new), `src/power_atlas/data_claude.py` (new), `src/power_atlas/web.py`, `tests/test_data.py`

**Why this phase is first**: All subsequent phases depend on the multi-provider data layer.

**Detailed changes**:

1. Create `src/power_atlas/data_kiro.py`:
   - Move `_load_sessions()`, `_extract_prompts()`, `_extract_content()` from `data.py`.
   - Expose: `discover_workspaces() -> list[tuple[str, int, str]]`, `load_sessions(cwd) -> list[Session]`, `is_available() -> bool`.
   - `is_available()`: returns `SESSION_DIR.is_dir()`.

2. Create `src/power_atlas/data_claude.py`:
   - `CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"`
   - `CLAUDE_HISTORY_PATH = Path.home() / ".claude" / "history.jsonl"`
   - `is_available() -> bool`: returns `CLAUDE_PROJECTS_DIR.is_dir() and any(CLAUDE_PROJECTS_DIR.iterdir())`.
   - `discover_workspaces() -> list[tuple[str, int, str]]`: Build a `{folder_name → real_path}` lookup by scanning `history.jsonl` for unique `project` values and matching them to existing project subdirs. For each resolved project folder, count `.jsonl` files and get latest mtime as `updated_at`. Fallback for folders not in history: show raw folder name with a "path unresolved" indicator.
   - `_build_path_index() -> dict[str, str]`: scan `history.jsonl` entries (each has a `project` field with the verbatim path), compute the expected folder name for each path (non-alphanumeric → `-`), and return `{folder_name: real_path}`. Cache result with 60s TTL.
   - `load_sessions(cwd: str) -> list[Session]`: for the matching project folder, iterate `.jsonl` files. For each:
     - `session_id` = filename stem
     - `title` = first `{"type": "ai-title", "aiTitle": "..."}` line; fallback chain: (1) ai-title, (2) first `type: "user"` message content[:80] (skipping attachment/meta lines), (3) session-id UUID as last resort
     - `created_at` = file creation time (or first message timestamp)
     - `updated_at` = file mtime
     - `first_prompt` / `last_prompt` = first/last `{"type": "user", ...}` message content `[:200]`
     - `last_reply_tail` = last `{"type": "assistant", ...}` message content `[-100:]`
   - Efficient parsing: for `first_prompt` scan first 100 lines (skip metadata lines: `mode`, `bridge-session`, `last-prompt`, `file-history-snapshot`, `hook_*`); for tail fields, seek last 256KB and parse backward (same strategy as kiro-cli). "Last" = last by file offset (acceptable approximation — Claude Code appends chronologically despite tree structure).

3. Refactor `src/power_atlas/data.py`:
   - Keep `Session` dataclass, `SessionCache`, `_normalize_path()`, cache TTL logic.
   - Change `SessionCache` key from `str` to `tuple[str, str]` = `(provider, normalized_cwd)`.
   - Change module-level `_cache` dict key from `"workspaces_with_counts"` to `f"workspaces_with_counts:{provider}"` (provider-aware caching).
   - Add `PROVIDERS = {"kiro-cli": data_kiro, "claude-code": data_claude}` registry.
   - `discover_workspaces_with_counts(provider: str | None = None)`: if provider specified, call that adapter; if None ("All"), merge results from all available providers, adding provider name to each tuple → returns `list[tuple[str, int, str, str]]` = `(cwd, count, updated_at, provider)`. **Breaking change**: all callers in `web.py` (`partials_workspaces`, `search`) and their test mocks must be updated to destructure the 4-tuple — include these updates in this phase to avoid runtime breakage.
   - `get_sessions(cwd, provider)`: delegate to adapter's `load_sessions()`.
   - `available_providers() -> list[str]`: returns provider names where `is_available()` is True.
   - Update `refresh_stale_entries()`, `warmup_pinned()`, `warmup_all()` to iterate all providers. For Claude Code staleness: stat `.jsonl` files in project folders (same pattern as kiro-cli but without separate `.json` metadata files — track `.jsonl` mtime/size directly).
   - Keep `get_session_tail()` and `get_first_prompt()` provider-aware (dispatch based on session source).

4. Update `tests/test_data.py`:
   - Adapt existing tests to work with refactored imports.
   - Add tests for `data_claude.py`: path decoding (multiple real folder names), `ai-title` extraction, first/last prompt extraction, `is_available()` with/without the directory.
   - Add tests for compound cache key behavior.

**Exit criteria**:
- [x] `data_kiro.py` passes all extracted tests (existing kiro-cli behavior unchanged)
- [x] `data_claude.py` discovers real Claude Code sessions from `~/.claude/projects/`
- [x] Path decoding tested against all real project folders on disk
- [x] `data.py` orchestrates both providers, `available_providers()` returns correct list
- [x] SessionCache correctly isolates same-cwd entries from different providers
- [x] All existing `test_data.py` tests pass (may need import path updates)
- [x] New Claude Code parsing tests pass

**Implementation (2026-07-01, code: d917186)**
Refactored the data layer into a multi-provider architecture: created `data_kiro.py` (extracted kiro-cli logic), `data_claude.py` (Claude Code adapter using history.jsonl path index), and refactored `data.py` into a thin orchestrator with compound `(provider, cwd)` cache keys. Claude Code adapter discovers 5 real workspaces from `~/.claude/projects/` with all 13 folder names resolved via history.jsonl. Both providers integrated: `available_providers()` returns `['kiro-cli', 'claude-code']`, `discover_workspaces_with_counts()` returns 41 total workspaces across both. All 177 tests pass.

### Phase 2: Tab UI and provider-filtered rendering [QA] [P:1]

**Goal**: Add a tab bar to the workspace section, render provider-filtered workspace cards with colored left-borders, and wire htmx tab switching.

**File scope**: `src/power_atlas/web.py`, `src/power_atlas/templates/index.html`, `src/power_atlas/templates/partials/workspace_card.html`, `src/power_atlas/static/style.css`, `tests/test_web.py`

**Detailed changes**:

1. Update `web.py`:
   - `partials_workspaces(request, provider: str = "all")`: accept `provider` query param. Call `data.discover_workspaces_with_counts(provider=None if "all" else provider)`. Render tab bar HTML above filtered cards.
   - Add `available_providers()` call to determine which tabs to show.
   - Pass `provider` to workspace card template for `data-provider` attribute and colored border.
   - Pinned sections remain above tabs (rendered unconditionally with all providers).

2. Update `templates/index.html`:
   - Replace the `hx-get="/partials/workspaces"` with initial tab bar + content area.
   - Tab bar: `<div class="provider-tabs">` with one tab per available provider + "All" default.
   - Each tab: `<button class="provider-tab active" hx-get="/partials/workspaces?provider=all" hx-target="#provider-cards" hx-swap="innerHTML">All</button>`.
   - Content splits: pinned sections load once (separate `hx-get`), tab content below.

3. Update `partials/workspace_card.html`:
   - Add `data-provider="{{ provider }}"` attribute to `.workspace-card`.
   - Add inline style `border-left: 3px solid {{ provider_color }}` (same pattern as launcher tiles).

4. Update `style.css`:
   - `.provider-tabs` container (flex, gap, matches `.emoji-tabs` pattern).
   - `.provider-tab` button styles (pill shape, active state with accent underline).
   - `.provider-tab.active` highlight.
   - Gear icon button style next to each tab.

5. Update `tests/test_web.py`:
   - Test `?provider=kiro-cli` returns only kiro-cli cards.
   - Test `?provider=all` returns cards from both providers with correct `data-provider`.
   - Test tab auto-hide when only one provider available.

**Exit criteria**:
- [x] Tab bar renders with correct tabs based on `available_providers()`
- [x] Clicking a tab fetches filtered workspaces via htmx
- [x] Cards show colored left-border per provider AND a small text provider badge (e.g., "K" / "C") for colorblind accessibility
- [x] Pinned sessions/workspaces remain visible above tabs regardless of active tab
- [x] "All" tab shows interleaved cards sorted by `updated_at`
- [x] Tab bar hidden when only one provider has data
- [x] Empty tab shows helper message (e.g., "No Claude Code sessions found — start one with `claude` to see it here")
- [x] Tests pass for filtered rendering

**Implementation (2026-07-01, code: a5f8db4)**
Added multi-provider tab UI: `partials_workspaces()` now accepts `?provider=` param; tab bar renders conditionally (only when 2+ providers available); workspace cards get `data-provider` attribute, colored left-border, and "K"/"C" text badge for colorblind accessibility. JS functions (`launchFresh`, `launchSelected`, `resumeSession`) updated to pass provider. Empty-state helper messages show per provider. 7 new tests added. All existing tests updated to 4-tuple API.

### Phase 3: Contextual launch and Claude Code resume [QA]

**Goal**: Make `launch_session`/`launch_batch` provider-aware, implement Claude Code launch commands, and surface errors clearly.

**File scope**: `src/power_atlas/launcher.py`, `src/power_atlas/web.py`, `src/power_atlas/templates/partials/workspace_card.html`, `src/power_atlas/templates/index.html`, `tests/test_launcher.py`, `tests/test_web.py`

**Detailed changes**:

1. Update `launcher.py`:
   - `launch_session(cwd, session_id=None, provider="kiro-cli", default_args="", terminal_override="")`:
     - Build args based on provider:
       - `kiro-cli`: `["kiro-cli", "chat"] + (["--resume-id", session_id] if session_id else []) + shlex.split(default_args)`
       - `claude-code`: `["claude"] + (["--resume", session_id] if session_id else []) + shlex.split(default_args)`
     - Remove `trust_all` parameter (replaced by `default_args`).
     - Title: `f"{provider_display} - {Path(cwd).name}"` where `provider_display` maps provider to friendly name.
   - `launch_batch(sessions, ...)`: each session dict now includes `provider` key. Dispatch per entry.
   - Binary detection: `shutil.which("claude")` for Claude Code, `shutil.which("kiro-cli")` for kiro-cli.
   - Clear error on binary not found: `f"'{binary}' not found on PATH. Install {provider_display} or check your PATH."`

2. Update `web.py`:
   - `api_launch()`: read `provider` from request body, pass to `launch_session()`.
   - `api_launch_batch()`: each session in the batch carries `provider`.
   - Pass provider default args from config (see Phase 5).
   - Remove `trust_all=config.trust_all_tools` references.

3. Update templates:
   - `workspace_card.html`: pass `data-provider` to JS context so `launchFresh()` and session resume know which provider to send.
   - `index.html` JS: `resumeSession()` and `launchSelected()` include `provider` in request body (read from `dataset.provider` on the card/row).

4. Update `tests/test_launcher.py`:
   - Test `launch_session(provider="claude-code")` builds correct `claude --resume <id>` command.
   - Test `launch_session(provider="kiro-cli")` preserves existing behavior.
   - Test `default_args` are appended correctly.
   - Test binary-not-found error message.

**Exit criteria**:
- [x] kiro-cli sessions launch with existing behavior (regression-free)
- [x] Claude Code sessions launch `claude --resume <id>` in correct cwd
- [x] New sessions launch bare `claude` / `kiro-cli chat` based on provider
- [x] `default_args` appended to launch commands
- [x] Clear error toast when provider binary not on PATH
- [x] Batch launch handles mixed providers correctly
- [x] All launcher tests pass

**Implementation (2026-07-01, code: 2a99314)**
Made `launch_session` and `launch_batch` provider-aware. Replaced `trust_all: bool` with `provider: str` and `default_args: str` parameters. Added `_PROVIDER_DISPLAY` and `_PROVIDER_BINARY` lookup tables. Binary detection via `shutil.which()` returns clear error naming the missing binary. Builds provider-specific args: `kiro-cli chat --resume-id <id>` for kiro-cli, `claude --resume <id>` for claude-code. Web endpoints updated to read provider from request body. 6 new launcher tests added. All 184 tests pass.

### Phase 4: Selection-aware custom launchers [QA]

**Goal**: Add opt-in selection-awareness to custom launchers — when enabled and workspaces are selected, fire the launcher once per selected workspace. Show selection count badge on tile.

**File scope**: `src/power_atlas/config.py`, `src/power_atlas/web.py`, `src/power_atlas/launcher.py`, `src/power_atlas/templates/partials/launcher_tile.html`, `src/power_atlas/templates/partials/launcher_modal.html`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`, `tests/test_launcher.py`, `tests/test_web.py`

**Detailed changes**:

1. Update `config.py`:
   - `custom_launchers` dict schema gains `"use_selected_workspaces": bool` (default `False`).
   - Empty `cwd` normalizes to `str(Path.home())` at load time (or at launch time).

2. Update `launcher.py`:
   - `launch_custom_batch(name, command, custom_args, workspaces: list[str], env, terminal_override, use_terminal)`:
     - Iterates `workspaces`, calls `launch_custom()` for each with that workspace as `cwd`.
     - Returns `list[LaunchResult]`.

3. Update `web.py`:
   - New endpoint `POST /api/launcher/run-batch`: accepts `{id, workspaces: [...]}`.
   - Looks up launcher config, calls `launch_custom_batch()`.
   - Returns toast with success/failure count.

4. Update `launcher_tile.html`:
   - When `launcher.use_selected_workspaces` is true, render a badge element `<span class="launcher-badge" id="badge-{{launcher.id}}"></span>` (hidden by default, shown via JS when selection exists).

5. Update `launcher_modal.html`:
   - Add checkbox: `<label class="launcher-checkbox"><input type="checkbox" id="launcherUseSelected"> Use selected workspaces</label>`.
   - Wire into save/load logic.

6. Update `index.html` JS:
   - `updateActionBar()`: also update launcher badge counts for selection-aware tiles.
   - `runLauncherById(id)`: if launcher has `use_selected_workspaces` and selection exists, call `/api/launcher/run-batch` with selected workspace paths. Otherwise, call `/api/launcher/run` as before.
   - Badge shows count (e.g., "3") when workspaces selected; hidden when 0.

7. Update `style.css`:
   - `.launcher-badge`: absolute-positioned pill, small font, accent background, hidden by default.
   - `.launcher-badge.visible`: display block.

8. Update tests:
   - `test_launcher.py`: test `launch_custom_batch()` fires once per workspace.
   - `test_web.py`: test `/api/launcher/run-batch` endpoint.

**Exit criteria**:
- [x] Launcher modal shows "Use selected workspaces" checkbox
- [x] Toggle persists in config
- [x] Selection-aware launcher tile shows badge with count when workspaces selected
- [x] Clicking selection-aware tile with selection fires launcher per workspace
- [x] Clicking selection-aware tile without selection uses configured cwd (or `~`)
- [x] Non-selection-aware launchers unchanged (regression-free)
- [x] Tests pass

**Implementation (2026-07-01, code: d22d494)**
Added selection-aware custom launchers: `launch_custom_batch()` iterates workspaces and fires `launch_custom()` per each. New `POST /api/launcher/run-batch` endpoint. Badge renders on tiles with count from selection. Checkbox in modal persists `use_selected_workspaces` to config. JS wires selection detection, badge updates via `updateLauncherBadges()`, and batch dispatch in `runLauncherById()`. 5 new tests. All 189 tests pass.

### Phase 5: Provider settings modal and trust-all removal [QA]

**Goal**: Add provider settings modal (gear icon on tabs, accessible from settings page), implement per-provider default args and color override, remove the dedicated trust-all toggle.

**File scope**: `src/power_atlas/config.py`, `src/power_atlas/web.py`, `src/power_atlas/tray.py`, `src/power_atlas/templates/index.html`, `src/power_atlas/templates/partials/provider_modal.html` (new), `src/power_atlas/static/style.css`, `tests/test_config.py`, `tests/test_web.py`

**Detailed changes**:

1. Update `config.py`:
   - Remove `trust_all_tools: bool` field.
   - Add `provider_settings: dict[str, dict] = field(default_factory=dict)`.
   - Schema per provider: `{"default_args": "", "color": "", "enabled": True}`.
   - `load_config()`: migrate — if old `trust_all_tools=True` exists and `provider_settings` is empty, seed `provider_settings["kiro-cli"]["default_args"] = "-a"`.

2. Create `templates/partials/provider_modal.html`:
   ```html
   <dialog class="provider-modal" id="providerModal">
     <form method="dialog" onsubmit="saveProvider(event)">
       <h3 id="providerModalTitle">Provider Settings</h3>
       <input type="hidden" id="providerKey" value="">
       <label>Name <input type="text" id="providerName" readonly></label>
       <label>Default args <input type="text" id="providerArgs"></label>
       <input type="hidden" id="providerColor" value="">
       <div class="color-picker-field">...</div>
       <label class="launcher-checkbox"><input type="checkbox" id="providerEnabled" checked> Enabled</label>
       <div class="modal-actions">
         <button type="button" class="modal-cancel-btn" onclick="...">Cancel</button>
         <button type="submit" class="action-btn launch">Save</button>
       </div>
     </form>
   </dialog>
   ```

3. Update `web.py`:
   - `POST /api/provider/save`: accepts `{provider, default_args, color, enabled}`, saves to config.
   - `GET /api/provider/{key}`: returns provider settings for modal pre-fill.
   - Update launch endpoints to read `config.provider_settings[provider]["default_args"]` and pass to `launch_session()`.
   - Remove all `trust_all_tools` references from endpoints.

4. Update `index.html`:
   - Remove trust-all toggle from topbar.
   - Add gear icon button inside each provider tab: `<button class="tab-gear" onclick="openProviderModal('kiro-cli')">⚙️</button>`.
   - Include `partials/provider_modal.html`.
   - Add `openProviderModal(key)` / `saveProvider(event)` JS functions.
   - Add one-time migration banner: on first load post-upgrade (detected by config having `provider_settings` populated from migration), show an inline toast: "Trust All is now per-provider — click ⚙️ on the provider tab to configure."

5. Update `tray.py`:
   - Remove "Trust All Tools" menu item from system tray.
   - Remove all `trust_all_tools` references.

6. Update `style.css`:
   - `.tab-gear` button styles (small, inline with tab text).
   - `.provider-modal` reuses `.launcher-modal` patterns.

6. Update `tests/test_config.py`:
   - Test `trust_all_tools` migration to `provider_settings`.
   - Test `provider_settings` load/save round-trip.
   - Test default values when provider not in settings.

7. Update `tests/test_web.py`:
   - Test `/api/provider/save` endpoint.
   - Test that launch uses `default_args` from provider settings.

**Exit criteria**:
- [x] Trust-all toggle removed from topbar
- [x] "Trust All Tools" menu item removed from system tray (`tray.py`)
- [x] Gear icon on each tab opens provider modal
- [x] Modal shows name (read-only), default args, color picker, enabled toggle
- [x] Saving provider settings persists to config.toml
- [x] `default_args` applied to all launches of that provider
- [x] Disabling a provider hides its tab (even if data exists)
- [x] Migration: existing `trust_all_tools=true` → `kiro-cli.default_args = "-a"`
- [x] One-time migration toast displayed on first post-upgrade launch
- [x] Update README.md: add Claude Code to Features, update Configuration section (provider_settings, remove trust_all_tools)
- [x] Tests pass

**Implementation (2026-07-01, code: 0f91ff7)**
Removed `trust_all_tools` from Config/tray/web/templates. Added `provider_settings` dict with migration logic (trust_all=true → kiro-cli.default_args="-a"). Provider modal with gear icon on tabs. Launch endpoints read `default_args` from provider_settings. Disabled providers hidden from tab bar. Migration toast shown once. README updated. All 199 tests pass.

### Phase 6: Claude Code memory-source documentation [P:1,2,3,4,5]

**Goal**: Document Claude Code's local data format in the agent-playbook config repo, parallel to the existing `kiro-cli-local-data.md`.

**File scope**: `C:\Users\QSylvestre.POLESTAR\OneDrive - Pole Star\Documents\Dev\Perso\agent-playbook\providers\kiro\steering\claude-code-local-data.md` (new)

**Detailed changes**:

Write `claude-code-local-data.md` documenting:
- Session file location: `~/.claude/projects/<encoded-path>/<session-id>.jsonl`
- Project folder naming convention (path encoding rules)
- JSONL message types: `ai-title`, `user`, `assistant`, `system`, `mode`, `bridge-session`, `last-prompt`, `file-history-snapshot`, `hook_success`, `hook_additional_context`, `deferred_tools_delta`, `agent_listing_delta`, `skill_listing`, `task_reminder`, `command_permissions`, `compact_file_reference`, `edited_text_file`, `mcp_instructions_delta`, `queue-operation`, `permission-mode`, `file`
- Title extraction: `{"type": "ai-title", "aiTitle": "..."}` — first occurrence
- User messages: `{"type": "user", "message": {"role": "user", "content": "..."}, "parentUuid": "..."}`
- Assistant messages: `{"type": "assistant", ...}`
- Global history: `~/.claude/history.jsonl` format (`display`, `timestamp`, `project`, `sessionId`)
- Active sessions: `~/.claude/sessions/<pid>.json` (ephemeral, process lifetime only)
- Session-env: `~/.claude/session-env/<session-id>/` (empty dirs, lifecycle markers)
- CLI resume: `claude --resume <session-id>` (must run from project directory)
- Differences from kiro-cli: no separate metadata `.json` file, no `parent_session_id` filtering needed (sub-agents don't produce separate project-level sessions)

**Exit criteria**:
- [ ] File created in agent-playbook steering
- [ ] Content covers all known session data paths and formats
- [ ] Consistent style with existing `kiro-cli-local-data.md`

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Claude Code `.jsonl` format changes between versions | Claude Code tab shows broken/empty sessions | Isolated in `data_claude.py`; single file to update. Accept risk. |
| Path decoding produces wrong cwd for resume | `claude --resume` fails with "No conversation found" | Test decoder against all real folders; surface error clearly in toast |
| Large `.jsonl` files (3MB+) slow down session loading | UI latency on first load | Tail-read strategy (last 256KB); background warmup; cache |
| `trust_all_tools` removal confuses existing users | User loses `-a` flag without realizing | Migration logic auto-seeds provider default_args; no silent breakage |
| Compound cache key changes break existing cache consumers | Stale data or crashes on upgrade | `SessionCache` is in-memory only (no persistence); restart clears it |
| Tab switching feels slow if data not pre-warmed | Perceived regression from current single-view | Warmup all providers at startup; serve from cache on tab switch |

## 7) Verification

**Automated**:
```bash
pytest tests/ -v
```

**Manual checks**:
1. Start PowerAtlas — verify tabs appear based on available data (hide Claude Code tab if `~/.claude/projects/` empty).
2. Click Claude Code tab — verify workspace cards appear with orange left-border, session titles match `ai-title` from local files.
3. Expand a Claude Code workspace card — verify sessions list with correct titles/timestamps.
4. Resume a Claude Code session — verify terminal opens with `claude --resume <id>` in correct cwd.
5. Multi-select workspaces across All tab — verify "Launch selected" dispatches correct provider per card.
6. Create a selection-aware custom launcher — verify badge appears when workspaces selected, fires per workspace on click.
7. Open provider settings modal (gear icon on tab) — verify default args saved and applied to next launch.
8. Verify trust-all toggle is gone from topbar.
9. Delete `~/.claude/projects/` (or rename) — verify Claude Code tab disappears on refresh.

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Add Claude Code support to Features section; update Configuration section (new provider_settings, remove trust_all_tools) | 5 |
| `agent-playbook/.../claude-code-local-data.md` | New file documenting Claude Code local data format | 6 (doc-table-only) |

## 9) Implementation Divergences from Plan

- Phase 2 fixed a circular import in `data.py` introduced by Phase 1: moved `from . import data_kiro, data_claude` below shared type definitions. Minimal (reorder only, no API change).

## Review Log

### 2026-07-01 — Plan Creation (via /qplan)

High-effort review (4 personas: Architect, Senior engineer, End-user advocate, Reliability engineer). 9 unique findings (5 High, 4 Medium). All auto-resolved.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `_decode_project_path()` is fundamentally ambiguous — hyphens encode both separators and literal chars | Resolved — replaced with `history.jsonl` `project` field lookup + `_build_path_index()` |
| 2 | High | `discover_workspaces_with_counts()` return type change breaks all callers | Resolved — added explicit "breaking change" note and included `web.py` in Phase 1 file scope |
| 3 | High | `tray.py` has 3 `trust_all_tools` references not covered by any phase | Resolved — added `tray.py` to Phase 5 file scope + explicit removal steps |
| 4 | High | Module-level `_cache` needs provider-aware keys | Resolved — specified `f"workspaces_with_counts:{provider}"` key pattern |
| 5 | High | Trust-all removal needs user discovery (migration UX) | Resolved — added one-time migration toast requirement to Phase 5 |
| 6 | Medium | `refresh_stale_entries()` lacks Claude Code staleness strategy | Resolved — added stat-based staleness for `.jsonl` files directly |
| 7 | Medium | Colorblind inaccessible: only colored border distinguishes providers | Resolved — added text badge ("K"/"C") requirement to Phase 2 exit criteria |
| 8 | Medium | Empty-state UX per tab not defined | Resolved — added helper message requirement to Phase 2 exit criteria |
| 9 | Medium | Claude Code "last message" by file offset may not be chronological | Resolved — documented "last by file offset" as acceptable approximation with rationale |

### 2026-07-01 -- Implementation Review (after Phases 1-2, persona: Senior engineer, Reliability engineer, End-user advocate, Maintainability reviewer)

Implementation health: Green (post-fix).
22 findings (3 High, 9 Medium, 10 Low). 15 auto-fixed in cycle 1.
QA verification: PASS (2 surfaces verified — Library + GUI, 7 probes executed).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | JS `toggleCard()`/`loadExpandedCards()` fetch sessions without `?provider=` | Fixed — reads `data-provider` from card |
| 2 | High | `refreshCards()` loses active tab state on refresh/pin/unpin | Fixed — stores `window._activeProvider`, includes in URL |
| 3 | High | `refresh_stale_entries` passes casefolded cwd to Claude adapter | Fixed — added `get_original_cwd()` to SessionCache |
| 4 | Medium | `partials_session_tail` ignores provider/cwd for Claude sessions | Fixed — added provider/cwd params to endpoint |
| 5 | Medium | `search()` renders cards without `is_pinned` — always collapsed | Fixed — passes `is_pinned` from `pinned_set` |
| 6 | Medium | `_path_index_cache` unprotected from concurrent threads | Fixed — added `threading.Lock()` |
| 7 | Medium | Provider badge contrast fails WCAG AA at small size | Fixed — darker colors (#4a6ede, #c2590f), 11px/600w |
| 8 | Medium | Tab buttons lack ARIA tab semantics | Fixed — added `role="tab"`, `aria-selected`, `role="tablist"` |
| 9 | Medium | Pinned folders hardcode "kiro-cli" as default provider | Fixed — uses first available provider |
| 10 | Medium | Empty-state discards already-rendered tab bar HTML | Fixed — renders tab bar + empty state together |
| 11 | Medium | Pinned workspace dedup collapses cross-provider entries | Fixed — keyed by `(norm_cwd, provider)` tuple |
| 12 | Medium | `discover_workspaces()` per-file stat race kills all results | Fixed — per-file try/except in loop |
| 13 | Low | `_render_workspace_card()` is dead code | Fixed — removed |
| 14 | Low | No orchestrator-level integration test for `get_sessions` | Accepted — adapter tests suffice for now |
| 15 | Low | `api_launch` discards provider field from JS | Deferred — Phase 3 scope |
| 16 | Low | `warmup_all()` only searches kiro-cli for pinned sessions | Accepted — followup improvement |
| 17 | Low | Tab bar shows raw slugs instead of display names | Fixed — added `PROVIDER_DISPLAY_NAMES` dict |
| 18 | Low | No loading indicator during tab switch | Accepted — minor UX, followup |
| 19 | Low | `PROVIDER_COLORS`/`PROVIDER_BADGES` spread across dicts | Accepted — consolidate at Phase 5 |
| 20 | Low | Unknown provider filter falls through to all | Accepted — defensive, not harmful |
| 21 | Low | Tab bar HTML string-concatenated in Python | Accepted — works, can extract to partial later |
| 22 | Low | `_cache` uses stringly-typed keys | Accepted — functional, low risk |
